"""Teaching Reflection Engine (METAMIND module 3).

Not a "lesson summary" generator. This module takes one stored evaluation plus
the learner state and the student's actual words, and produces a *diagnosis with
an audit trail*: what went wrong, a hypothesis about why (explicitly unverified),
a concrete reusable proposal built from OpenTutor's existing teaching skills, and
a plan for how the next run would verify it.

Deliberately NOT done here (later modules own these):
* writing or enabling a Teaching Skill
* changing the Tutor prompt
* changing any score, mastery value or progress row

Hard rules enforced in code
---------------------------
1. **Gates before generation.** A failed evaluation or one with no measured
   dimension yields ``insufficient_evidence``; a fully achieved objective yields
   ``no_improvement_needed``. Neither calls the model — inventing a failure to
   force "evolution" is a bug, not a feature.
2. **Every citation must exist.** Each evidence reference must point at a real id
   from the input package, and its quote must appear verbatim in that source.
3. **Nothing may be claimed as verified.** ``is_verified`` must be false in both
   the cause hypothesis and the proposal; a model that marks them true is
   rejected and retried.
4. **Reused skills must exist.** ``reuses_skills`` is validated against the
   loaded skill library, so a proposal cannot name an invented skill.
5. **Failure is recorded as failure.** Validation/LLM failures are persisted with
   ``status='failed'`` and an error code, never silently downgraded to success.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from models.practice import PracticeProblem, PracticeResult
from models.teaching_evaluation import EVAL_STATUS_FAILED, TeachingEvaluation
from models.teaching_reflection import (
    REFLECTION_STATUS_COMPLETED,
    REFLECTION_STATUS_FAILED,
    REFLECTION_STATUS_INSUFFICIENT,
    REFLECTION_STATUS_NO_IMPROVEMENT,
    TeachingReflection,
)
from services.evolution import eval_rubric as rubric
from services.evolution.learner_state import build_learner_state

logger = logging.getLogger(__name__)

# ── error codes (stable) ───────────────────────────────────────────────────
ERROR_EVALUATION_NOT_FOUND = "evaluation_not_found"
ERROR_LLM_UNAVAILABLE = "llm_unavailable"
ERROR_LLM_ERROR = "llm_error"
ERROR_INVALID_JSON = "invalid_json"
ERROR_SCHEMA_MISMATCH = "schema_mismatch"
ERROR_UNKNOWN_EVIDENCE_ID = "unknown_evidence_id"
ERROR_EVIDENCE_NOT_VERBATIM = "evidence_not_verbatim"
ERROR_UNVERIFIED_CLAIM = "unverified_claim"
ERROR_UNKNOWN_SKILL = "unknown_skill"
ERROR_VAGUE_PROPOSAL = "vague_proposal"

# Rejected phrasings: advice that cannot be executed or tested.
_VAGUE_PATTERNS = (
    "更努力", "多练习", "多做题", "更加用心", "下次注意", "好好讲", "讲清楚一点",
    "换个方式讲", "多举例子", "尽量通俗", "try harder", "be clearer", "explain better",
)
_MIN_STEP_CHARS = 6

SOURCE_TYPES = (
    "evaluation",
    "student_answer",
    "learner_state",
    "objective",
    "skill",
    "subjective_feedback",
    "practice_result",
)


# ── Schema ─────────────────────────────────────────────────────────────────


class EvidenceRef(BaseModel):
    source_type: str
    source_id: str = Field(min_length=1, max_length=64)
    # A one-character quote is legitimate: a student's objective answer may be "b".
    quote: str = Field(min_length=1, max_length=400)
    note: str | None = Field(default=None, max_length=300)

    @field_validator("source_type")
    @classmethod
    def _source_type_allowed(cls, value: str) -> str:
        if value not in SOURCE_TYPES:
            raise ValueError(f"source_type must be one of {SOURCE_TYPES}")
        return value


class ProblemBlock(BaseModel):
    statement: str = Field(min_length=8, max_length=600)
    severity: str = Field(default="medium")
    dimension: str = Field(default="multiple")
    affected_objective: str | None = Field(default=None, max_length=300)

    @field_validator("severity")
    @classmethod
    def _severity_allowed(cls, value: str) -> str:
        return value if value in {"low", "medium", "high"} else "medium"

    @field_validator("dimension")
    @classmethod
    def _dimension_allowed(cls, value: str) -> str:
        allowed = {"understanding", "application", "transfer", "multiple"}
        return value if value in allowed else "multiple"


class CauseHypothesis(BaseModel):
    statement: str = Field(min_length=10, max_length=800)
    confidence: str = Field(default="low")
    is_verified: bool = False
    rationale: str = Field(min_length=8, max_length=600)
    alternatives: list[str] = Field(default_factory=list)

    @field_validator("confidence")
    @classmethod
    def _confidence_allowed(cls, value: str) -> str:
        return value if value in {"low", "medium", "high"} else "low"

    @field_validator("alternatives", mode="before")
    @classmethod
    def _coerce_alternatives(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if str(item).strip()][:6]
        return value


class ImprovementProposal(BaseModel):
    summary: str = Field(min_length=10, max_length=600)
    target_dimension: str = Field(default="multiple")
    # How this proposal relates to the existing library. ``new_skill`` is the
    # only case where an empty ``reuses_skills`` is acceptable: forcing a model
    # to bolt on an unrelated existing skill just to pass validation would
    # produce a fake lineage.
    strategy_origin: str = Field(default="compose_existing")
    new_skill_name: str | None = Field(default=None, max_length=80)
    new_skill_rationale: str | None = Field(default=None, max_length=400)
    reuses_skills: list[str] = Field(default_factory=list)
    steps: list[str] = Field(default_factory=list)
    trigger_conditions: list[str] = Field(default_factory=list)
    expected_effect: str = Field(min_length=6, max_length=400)
    is_verified: bool = False

    @field_validator("target_dimension")
    @classmethod
    def _target_allowed(cls, value: str) -> str:
        allowed = {"understanding", "application", "transfer", "multiple"}
        return value if value in allowed else "multiple"

    @field_validator("strategy_origin")
    @classmethod
    def _origin_allowed(cls, value: str) -> str:
        allowed = {"reuse_existing", "compose_existing", "new_skill"}
        return value if value in allowed else "compose_existing"

    @field_validator("reuses_skills", "steps", "trigger_conditions", mode="before")
    @classmethod
    def _coerce_list(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()][:10]
        return value


class VerificationPlan(BaseModel):
    metric: str = Field(min_length=4, max_length=300)
    comparison: str = Field(min_length=4, max_length=300)
    next_run_procedure: list[str] = Field(default_factory=list)
    success_criteria: list[str] = Field(default_factory=list)
    failure_criteria: list[str] = Field(default_factory=list)
    uses_parallel_items: bool = True

    @field_validator(
        "next_run_procedure", "success_criteria", "failure_criteria", mode="before"
    )
    @classmethod
    def _coerce_list(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            return [str(item).strip() for item in value if str(item).strip()][:10]
        return value


class ReflectionOutput(BaseModel):
    problem: ProblemBlock
    evidence: list[EvidenceRef] = Field(default_factory=list)
    cause_hypothesis: CauseHypothesis
    improvement_proposal: ImprovementProposal
    verification_plan: VerificationPlan


# ── Evidence package ───────────────────────────────────────────────────────


@dataclass
class EvidencePackage:
    evaluation: dict[str, Any]
    learner_state: dict[str, Any]
    attempts: list[dict[str, Any]]
    skills: list[dict[str, Any]]
    subjective_feedback: str | None = None
    objective_text: str | None = None
    # id -> text that a quote may be drawn from
    corpus: dict[str, str] = field(default_factory=dict)
    allowed_skill_ids: set[str] = field(default_factory=set)

    def as_prompt_payload(self) -> dict[str, Any]:
        return {
            "evaluation": self.evaluation,
            "learner_state": self.learner_state,
            "objective_text": self.objective_text,
            "student_attempts": self.attempts,
            "available_teaching_skills": self.skills,
            "subjective_feedback": (
                {"text": self.subjective_feedback, "is_objective_self_report": True}
                if self.subjective_feedback
                else None
            ),
            "how_to_cite": {
                "allowed_source_ids": sorted(self.corpus),
                "rule": (
                    "evidence[].source_id 必须从 allowed_source_ids 里**逐字复制**，不要添加任何前缀，"
                    "不要加 practice_result: / student_answer: 之类。quote 必须是该来源中逐字出现的片段。"
                ),
                "source_type_values": list(SOURCE_TYPES),
            },
        }


def _normalise(text: str) -> str:
    text = (
        text.replace("“", '"').replace("”", '"')
        .replace("‘", "'").replace("’", "'")
        .replace("\u3000", " ")
    )
    return re.sub(r"\s+", "", text).casefold()


def _quote_in(quote: str, source_text: str) -> bool:
    """Verbatim check.

    Short quotes (e.g. a one-letter answer "b") are only accepted when they are
    the *entire* source text — otherwise a stray character would match by chance.
    """
    needle = _normalise(quote.strip().strip("\"'「」『』…"))
    haystack = _normalise(source_text)
    if not needle:
        return False
    if len(needle) < 3:
        return needle == haystack
    return needle in haystack


def _hash(payload: Any) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


async def _load_skill_library() -> list[dict[str, Any]]:
    """OpenTutor's existing teaching skills (read from the shipped skill files)."""
    try:
        from services.agent.skills import load_skills

        skills = load_skills()
    except Exception:  # noqa: BLE001 - reflection can still run with no library
        logger.exception("Failed to load teaching skills")
        return []
    return [
        {
            "id": skill.name,
            "triggers": list(skill.triggers),
            "scenes": list(skill.scenes),
            "summary": (skill.content or "").strip().splitlines()[0][:200]
            if skill.content
            else "",
            "instruction": (skill.content or "").strip()[:1200],
        }
        for skill in skills
    ]


async def _load_attempts_from_evaluation(
    db: AsyncSession, evaluation: TeachingEvaluation
) -> list[dict[str, Any]]:
    inputs = evaluation.inputs_json or {}
    attempt_meta = inputs.get("attempts") or []
    problem_ids = [item.get("problem_id") for item in attempt_meta if item.get("problem_id")]
    attempts: list[dict[str, Any]] = []
    if problem_ids:
        result = await db.execute(
            select(PracticeResult, PracticeProblem)
            .join(PracticeProblem, PracticeResult.problem_id == PracticeProblem.id)
            .where(PracticeResult.id.in_([a.get("practice_result_id") for a in attempt_meta
                                          if a.get("practice_result_id")]))
        )
        for practice_result, problem in result.all():
            attempts.append(
                {
                    "practice_result_id": str(practice_result.id),
                    "problem_id": str(problem.id),
                    "question": problem.question,
                    "question_type": problem.question_type,
                    "difficulty_layer": problem.difficulty_layer,
                    "user_answer": practice_result.user_answer,
                    "is_correct": bool(practice_result.is_correct),
                    "error_category": practice_result.error_category,
                }
            )
    for key in ("open_answer", "transfer_answer"):
        value = inputs.get(key)
        if value:
            attempts.append(
                {
                    "practice_result_id": f"{key}:{evaluation.id}",
                    "problem_id": None,
                    "question": inputs.get("open_question")
                    if key == "open_answer"
                    else inputs.get("transfer_case"),
                    "question_type": "open_answer" if key == "open_answer" else "transfer",
                    "user_answer": value,
                    "is_correct": None,
                    "error_category": None,
                }
            )
    return attempts


async def build_evidence_package(
    db: AsyncSession,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    evaluation: TeachingEvaluation,
    *,
    subjective_feedback: str | None = None,
) -> EvidencePackage:
    state = await build_learner_state(db, user_id, course_id)
    attempts = await _load_attempts_from_evaluation(db, evaluation)
    skills = await _load_skill_library()

    corpus: dict[str, str] = {}
    evaluation_text = json.dumps(
        {
            "verdict": evaluation.verdict,
            "scores": {
                "understanding": evaluation.understanding_score,
                "application": evaluation.application_score,
                "transfer": evaluation.transfer_score,
            },
            "evidence": evaluation.evidence_json,
            "rationale": evaluation.rationale_json,
            "objective_text": evaluation.objective_text,
        },
        ensure_ascii=False,
    )
    corpus[str(evaluation.id)] = evaluation_text
    if evaluation.objective_text:
        corpus["objective"] = evaluation.objective_text
    for attempt in attempts:
        corpus[str(attempt["practice_result_id"])] = attempt["user_answer"] or ""
    for concept in state["concepts"]:
        corpus[f"concept:{concept['concept_id']}"] = " ".join(
            str(part)
            for part in (
                concept["name"],
                concept.get("description") or "",
                concept["state"],
                concept["mastery_score"],
                concept.get("direct_attempts"),
                json.dumps(concept.get("error_categories") or {}, ensure_ascii=False),
            )
        )
    for skill in skills:
        corpus[f"skill:{skill['id']}"] = f"{skill['id']} {skill['summary']} {skill['instruction']}"
    if subjective_feedback:
        corpus["subjective_feedback"] = subjective_feedback

    return EvidencePackage(
        evaluation={
            "evaluation_id": str(evaluation.id),
            "objective_slug": evaluation.objective_slug,
            "objective_id": str(evaluation.objective_id) if evaluation.objective_id else None,
            "objective_text": evaluation.objective_text,
            "rubric_version": evaluation.rubric_version,
            "strategy_version": evaluation.strategy_version,
            "status": evaluation.status,
            "verdict": evaluation.verdict,
            "scores": {
                "understanding": evaluation.understanding_score,
                "application": evaluation.application_score,
                "transfer": evaluation.transfer_score,
                "retention": None,
            },
            "dimensions_measured": evaluation.dimensions_measured,
            "unmeasured_dimensions": [
                name
                for name, value in (
                    ("understanding", evaluation.understanding_score),
                    ("application", evaluation.application_score),
                    ("transfer", evaluation.transfer_score),
                )
                if value is None
            ],
            "evidence": evaluation.evidence_json,
            "rationale": evaluation.rationale_json,
        },
        learner_state={
            "concepts": [
                {
                    "concept_id": concept["concept_id"],
                    "name": concept["name"],
                    "state": concept["state"],
                    "mastery_score": concept["mastery_score"],
                    "error_categories": concept.get("error_categories", {}),
                    "direct_attempts": concept["direct_attempts"],
                }
                for concept in state["concepts"]
            ],
            "assessment_summary": state["assessment_summary"],
        },
        attempts=attempts,
        skills=skills,
        subjective_feedback=subjective_feedback,
        objective_text=evaluation.objective_text,
        corpus=corpus,
        allowed_skill_ids={skill["id"] for skill in skills},
    )


# ── Validation ─────────────────────────────────────────────────────────────


@dataclass
class ValidationFailure:
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)


_REDUNDANT_PREFIXES = ("practice_result:", "student_answer:", "evaluation:", "attempt:")


def _resolve_source_id(source_id: str, corpus: dict[str, str]) -> str | None:
    """Map a cited id to a real corpus key, tolerating a redundant prefix.

    The model sometimes writes ``practice_result:<uuid>`` for an attempt whose
    real key is just ``<uuid>``. That is a formatting slip, not fabrication, so
    the prefix is stripped *only* when the bare id really exists.
    """
    if source_id in corpus:
        return source_id
    for prefix in _REDUNDANT_PREFIXES:
        if source_id.startswith(prefix):
            bare = source_id[len(prefix):]
            if bare in corpus:
                return bare
    return None


def validate_output(
    output: ReflectionOutput, package: EvidencePackage
) -> list[ValidationFailure]:
    failures: list[ValidationFailure] = []

    if not output.evidence:
        failures.append(
            ValidationFailure(ERROR_EVIDENCE_NOT_VERBATIM, "reflection cites no evidence")
        )
    else:
        unknown: list[str] = []
        not_verbatim: list[dict[str, str]] = []
        for ref in output.evidence:
            resolved = _resolve_source_id(ref.source_id, package.corpus)
            if resolved is None:
                unknown.append(ref.source_id)
                continue
            if not _quote_in(ref.quote, package.corpus[resolved]):
                not_verbatim.append({"source_id": ref.source_id, "quote": ref.quote})
        if unknown:
            failures.append(
                ValidationFailure(
                    ERROR_UNKNOWN_EVIDENCE_ID,
                    f"evidence ids not present in the input package: {unknown}",
                    {"unknown_ids": unknown},
                )
            )
        if not_verbatim:
            failures.append(
                ValidationFailure(
                    ERROR_EVIDENCE_NOT_VERBATIM,
                    "quotes that do not appear verbatim in their cited source: "
                    + json.dumps(not_verbatim, ensure_ascii=False)[:600],
                    {"mismatches": not_verbatim},
                )
            )

    if output.cause_hypothesis.is_verified:
        failures.append(
            ValidationFailure(
                ERROR_UNVERIFIED_CLAIM,
                "cause_hypothesis.is_verified must be false: a hypothesis cannot be verified "
                "before the next run",
            )
        )
    if output.improvement_proposal.is_verified:
        failures.append(
            ValidationFailure(
                ERROR_UNVERIFIED_CLAIM,
                "improvement_proposal.is_verified must be false: efficacy has not been tested",
            )
        )

    unknown_skills = [
        skill for skill in output.improvement_proposal.reuses_skills
        if skill not in package.allowed_skill_ids
    ]
    if unknown_skills:
        failures.append(
            ValidationFailure(
                ERROR_UNKNOWN_SKILL,
                f"proposal reuses skills that do not exist in the library: {unknown_skills}",
                {"unknown_skills": unknown_skills},
            )
        )
    if not output.improvement_proposal.reuses_skills:
        # Empty reuse is legitimate only when the model explicitly says it is
        # proposing a genuinely new skill, and names it. Otherwise the proposal
        # is either incomplete or trying to smuggle in an unknown strategy.
        if output.improvement_proposal.strategy_origin != "new_skill":
            failures.append(
                ValidationFailure(
                    ERROR_UNKNOWN_SKILL,
                    "proposal reuses no existing skill and does not declare "
                    "strategy_origin='new_skill'",
                )
            )
        elif not (output.improvement_proposal.new_skill_name or "").strip():
            failures.append(
                ValidationFailure(
                    ERROR_UNKNOWN_SKILL,
                    "strategy_origin='new_skill' requires new_skill_name",
                )
            )

    steps = output.improvement_proposal.steps
    if len(steps) < 2:
        failures.append(
            ValidationFailure(
                ERROR_VAGUE_PROPOSAL, f"proposal needs at least 2 concrete steps, got {len(steps)}"
            )
        )
    vague = [
        step for step in steps
        if len(step) < _MIN_STEP_CHARS
        or any(pattern in step for pattern in _VAGUE_PATTERNS)
    ]
    if vague and steps:
        failures.append(
            ValidationFailure(
                ERROR_VAGUE_PROPOSAL,
                "proposal steps are too generic to execute",
                {"vague_steps": vague},
            )
        )
    if not output.verification_plan.success_criteria or not output.verification_plan.failure_criteria:
        failures.append(
            ValidationFailure(
                ERROR_VAGUE_PROPOSAL,
                "verification plan must define both success and failure criteria",
            )
        )
    return failures


# ── Prompt ─────────────────────────────────────────────────────────────────

REFLECTION_SYSTEM_PROMPT = """你是 METAMIND 的教学诊断员。你的任务不是写课后总结，而是根据真实证据诊断教学问题，并提出可执行、可验证的改进方案。

铁律：
1. 只输出一个 JSON 对象，不要 markdown、不要多余文字。
2. 你只能引用输入证据包中真实存在的 id 与原文。禁止编造学生回答、测验结果、策略版本或学习记录。
3. evidence[].quote 必须是从被引用来源中**逐字复制**的片段，不得改写、不得拼接、不得使用省略号。
   如果学生只提交了选项字母（例如 "b"），可以直接引用该字母，但必须与原文完全一致。
   引用 evaluation 或 learner_state 时，请直接复制 JSON 中的字段值（例如 "assessed_below_mastery"、
   verdict_reason 的原文），不要用自己的话概括——概括会被判为编造证据。
4. cause_hypothesis 只是**待验证假设**：is_verified 必须为 false；不确定就给 confidence="low" 并在 alternatives 写出其他可能。
5. improvement_proposal 的 reuses_skills 只能使用 available_teaching_skills 中的 id；is_verified 必须为 false。
   如果现有技能确实都不合适，把 strategy_origin 设为 "new_skill"，填写 new_skill_name 与
   new_skill_rationale（说明为什么现有技能不够用），此时 reuses_skills 可以为空。
   只有当提案确实是这些技能的组合或微调时，才填 "compose_existing" / "reuse_existing"。
   不要为了通过校验硬塞一个不相关的技能。
6. steps 必须是可执行的具体动作（做什么、按什么顺序、判断依据是什么），不得写「多练习」「更努力」「讲清楚一点」这类空话。
7. verification_plan 必须给出下一次如何验证：用什么题/什么维度、与什么比较、成功与失败的标准分别是什么。
8. 不得声称改进已验证有效；也不得因为想「进化」而虚构失败。
"""

REFLECTION_USER_TEMPLATE = """学习目标：{objective}

请基于下面的证据包做一次教学诊断，并输出 JSON：

{package}

输出格式（严格遵守）：
{{
  "problem": {{"statement": "...", "severity": "low|medium|high",
               "dimension": "understanding|application|transfer|multiple",
               "affected_objective": "..."}},
  "evidence": [{{"source_type": "evaluation|student_answer|learner_state|objective|skill|subjective_feedback|practice_result",
                 "source_id": "<证据包里出现过的 id>",
                 "quote": "<逐字片段>",
                 "note": "这条证据说明什么"}}],
  "cause_hypothesis": {{"statement": "...", "confidence": "low|medium|high",
                        "is_verified": false, "rationale": "...",
                        "alternatives": ["..."]}},
  "improvement_proposal": {{"summary": "...", "target_dimension": "...",
                            "strategy_origin": "reuse_existing|compose_existing|new_skill",
                            "new_skill_name": null, "new_skill_rationale": null,
                            "reuses_skills": ["<skill id>"],
                            "steps": ["...", "..."],
                            "trigger_conditions": ["..."],
                            "expected_effect": "...",
                            "is_verified": false}},
  "verification_plan": {{"metric": "...", "comparison": "...",
                         "next_run_procedure": ["..."],
                         "success_criteria": ["..."],
                         "failure_criteria": ["..."],
                         "uses_parallel_items": true}}
}}
"""


def _extract_json(text: str) -> dict[str, Any]:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end <= start:
        raise ValueError("no JSON object found")
    return json.loads(text[start : end + 1])


@dataclass
class ReflectionAttempt:
    output: ReflectionOutput | None
    failures: list[ValidationFailure]
    raw: str | None
    error_code: str | None = None
    error_message: str | None = None


async def _generate_once(package: EvidencePackage, correction: str | None = None) -> ReflectionAttempt:
    try:
        from services.llm.router import get_llm_client

        client = get_llm_client()
    except Exception as exc:  # noqa: BLE001
        return ReflectionAttempt(
            output=None, failures=[], raw=None,
            error_code=ERROR_LLM_UNAVAILABLE, error_message=str(exc)[:500],
        )

    system_prompt = REFLECTION_SYSTEM_PROMPT
    if correction:
        system_prompt = f"{REFLECTION_SYSTEM_PROMPT}\n\n上一次输出被拒绝，原因：{correction}\n请修正后重新输出完整 JSON。"
    user_prompt = REFLECTION_USER_TEMPLATE.format(
        objective=package.objective_text or "（未提供目标描述）",
        package=json.dumps(package.as_prompt_payload(), ensure_ascii=False, indent=1)[:12000],
    )

    raw = None
    try:
        raw, _usage = await client.extract(system_prompt, user_prompt)
    except Exception as exc:  # noqa: BLE001
        return ReflectionAttempt(
            output=None, failures=[], raw=None,
            error_code=ERROR_LLM_ERROR, error_message=str(exc)[:500],
        )

    try:
        parsed = _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return ReflectionAttempt(
            output=None, failures=[], raw=raw,
            error_code=ERROR_INVALID_JSON, error_message=str(exc)[:300],
        )

    try:
        output = ReflectionOutput.model_validate(parsed)
    except ValidationError as exc:
        return ReflectionAttempt(
            output=None, failures=[], raw=raw,
            error_code=ERROR_SCHEMA_MISMATCH, error_message=str(exc)[:600],
        )

    failures = validate_output(output, package)
    if failures:
        return ReflectionAttempt(
            output=output, failures=failures, raw=raw,
            error_code=failures[0].code,
            error_message="; ".join(f"{item.code}: {item.message}" for item in failures)[:800],
        )
    return ReflectionAttempt(output=output, failures=[], raw=raw)


# ── Orchestration ──────────────────────────────────────────────────────────


def _gate_reason(evaluation: TeachingEvaluation) -> tuple[str, str] | None:
    """Deterministic pre-checks. Returns (status, reason) when the model must not run."""
    if evaluation.status == EVAL_STATUS_FAILED:
        return (
            REFLECTION_STATUS_INSUFFICIENT,
            "评价本身失败（status=failed），没有可用证据，不能据此诊断教学问题",
        )
    if evaluation.dimensions_measured == 0:
        return (
            REFLECTION_STATUS_INSUFFICIENT,
            "评价没有任何维度被测得，证据不足，不能据此诊断教学问题",
        )
    if evaluation.verdict == "achieved":
        return (
            REFLECTION_STATUS_NO_IMPROVEMENT,
            "三个维度均测得且均为满分，目标已达成，不为强行进化而虚构教学失败",
        )
    return None


async def reflect_on_evaluation(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    evaluation_id: uuid.UUID,
    subjective_feedback: str | None = None,
    max_attempts: int = 3,
    persist: bool = True,
) -> TeachingReflection:
    evaluation = await db.get(TeachingEvaluation, evaluation_id)
    if evaluation is None or evaluation.user_id != user_id or evaluation.course_id != course_id:
        # A reflection must reference a real evaluation (FK); an unknown id is a
        # caller error, so it is reported as such instead of being persisted.
        raise ValueError(ERROR_EVALUATION_NOT_FOUND)

    package = await build_evidence_package(
        db, user_id, course_id, evaluation, subjective_feedback=subjective_feedback
    )
    input_hash = _hash(package.as_prompt_payload())

    gate = _gate_reason(evaluation)
    if gate is not None:
        status, reason = gate
        row = TeachingReflection(
            user_id=user_id,
            course_id=course_id,
            evaluation_id=evaluation.id,
            run_tag=evaluation.run_tag,
            objective_slug=evaluation.objective_slug,
            objective_id=evaluation.objective_id,
            session_id=evaluation.session_id,
            strategy_version=evaluation.strategy_version,
            rubric_version=evaluation.rubric_version,
            status=status,
            problem_statement=None if status == REFLECTION_STATUS_NO_IMPROVEMENT else reason,
            problem_severity="none" if status == REFLECTION_STATUS_NO_IMPROVEMENT else None,
            evidence_json={
                "gate": status,
                "reason": reason,
                "evaluation_snapshot": package.evaluation,
                "cited": [],
            },
            input_package_json=package.as_prompt_payload(),
            input_hash=input_hash,
            attempts=0,
        )
        if persist:
            db.add(row)
            await db.flush()
        return row

    attempt = await _generate_once(package)
    attempts_used = 1
    all_raw = [attempt.raw] if attempt.raw else []

    while attempt.error_code and attempts_used < max_attempts and attempt.error_code in {
        ERROR_SCHEMA_MISMATCH,
        ERROR_INVALID_JSON,
        ERROR_UNKNOWN_EVIDENCE_ID,
        ERROR_EVIDENCE_NOT_VERBATIM,
        ERROR_UNVERIFIED_CLAIM,
        ERROR_UNKNOWN_SKILL,
        ERROR_VAGUE_PROPOSAL,
    }:
        correction = attempt.error_message or attempt.error_code
        attempt = await _generate_once(package, correction=correction)
        attempts_used += 1
        if attempt.raw:
            all_raw.append(attempt.raw)

    provider_name = model_name = None
    try:
        from services.llm.router import get_llm_client

        client = get_llm_client()
        provider_name = type(client).__name__
        model_name = getattr(client, "model", None)
    except Exception:  # noqa: BLE001
        pass

    common = dict(
        user_id=user_id,
        course_id=course_id,
        evaluation_id=evaluation.id,
        run_tag=evaluation.run_tag,
        objective_slug=evaluation.objective_slug,
        objective_id=evaluation.objective_id,
        session_id=evaluation.session_id,
        strategy_version=evaluation.strategy_version,
        rubric_version=evaluation.rubric_version,
        evidence_json={
            "gate": None,
            "cited": [ref.model_dump() for ref in (attempt.output.evidence if attempt.output else [])],
            "evaluation_snapshot": package.evaluation,
            "allowed_source_ids": sorted(package.corpus),
        },
        input_package_json=package.as_prompt_payload(),
        input_hash=input_hash,
        attempts=attempts_used,
        model_provider=provider_name,
        model_name=model_name,
        raw_llm_response="\n\n--- attempt ---\n\n".join(all_raw) if all_raw else None,
    )

    if attempt.output is None or attempt.failures:
        row = TeachingReflection(
            **common,
            status=REFLECTION_STATUS_FAILED,
            error_code=attempt.error_code,
            error_message=attempt.error_message,
        )
    else:
        output = attempt.output
        row = TeachingReflection(
            **common,
            status=REFLECTION_STATUS_COMPLETED,
            problem_statement=output.problem.statement,
            problem_severity=output.problem.severity,
            problem_dimension=output.problem.dimension,
            cause_statement=output.cause_hypothesis.statement,
            cause_confidence=output.cause_hypothesis.confidence,
            cause_hypothesis_json=output.cause_hypothesis.model_dump(),
            improvement_json=output.improvement_proposal.model_dump(),
            verification_plan_json=output.verification_plan.model_dump(),
        )

    if persist:
        db.add(row)
        await db.flush()
    return row


def serialize_reflection(row: TeachingReflection) -> dict[str, Any]:
    return {
        "reflection_id": str(row.id),
        "evaluation_id": str(row.evaluation_id),
        "student_id": str(row.user_id),
        "course_id": str(row.course_id),
        "objective_slug": row.objective_slug,
        "objective_id": str(row.objective_id) if row.objective_id else None,
        "session_id": row.session_id,
        "run_tag": row.run_tag,
        "strategy_version": row.strategy_version,
        "rubric_version": row.rubric_version,
        "status": row.status,
        "problem": {
            "statement": row.problem_statement,
            "severity": row.problem_severity,
            "dimension": row.problem_dimension,
        },
        "cause_hypothesis": row.cause_hypothesis_json,
        "improvement_proposal": row.improvement_json,
        "verification_plan": row.verification_plan_json,
        "evidence": row.evidence_json,
        "input_package": row.input_package_json,
        "input_hash": row.input_hash,
        "attempts": row.attempts,
        "model": {"provider": row.model_provider, "name": row.model_name},
        "error_code": row.error_code,
        "error_message": row.error_message,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


__all__ = [
    "build_evidence_package",
    "reflect_on_evaluation",
    "serialize_reflection",
    "validate_output",
    "ReflectionOutput",
    "EvidenceRef",
    "ProblemBlock",
    "CauseHypothesis",
    "ImprovementProposal",
    "VerificationPlan",
]
