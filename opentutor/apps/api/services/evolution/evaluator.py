"""Teaching evaluation service (METAMIND module 2).

Decides whether a learning objective was met, from real student answers, using
the fixed rubric in :mod:`services.evolution.eval_rubric`.

Guiding rules
-------------
* **Deterministic first.** Objective items are scored by the existing grading
  results (``practice_results.is_correct``) — the model never re-grades them.
* **Evidence or nothing.** Open-answer scores require verbatim quotes from the
  student's own text. A quote that cannot be located makes the dimension
  ``not_measured``; it never silently becomes a number.
* **Not measured != 0.** Unmeasured dimensions are ``None`` end to end.
* **No mastery shortcut.** BKT / ConceptMastery is *context only* and never
  contributes to a score.
* **Fail loud.** Model/JSON/evidence failures are persisted with
  ``status='failed'`` and an ``error_code``; they are never reported as success.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any, Iterable

from pydantic import BaseModel, Field, ValidationError, field_validator
from sqlalchemy import select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from models.knowledge_graph import KnowledgeNode
from models.practice import PracticeProblem, PracticeResult
from models.teaching_evaluation import (
    EVAL_STATUS_COMPLETED,
    EVAL_STATUS_FAILED,
    EVAL_STATUS_PARTIAL,
    TeachingEvaluation,
)
from services.evolution import eval_rubric as rubric
from services.evolution.learner_state import build_learner_state

logger = logging.getLogger(__name__)

# ── error codes (stable, used by tests and by the reflection module later) ──
ERROR_LLM_UNAVAILABLE = "llm_unavailable"
ERROR_LLM_ERROR = "llm_error"
ERROR_INVALID_JSON = "invalid_json"
ERROR_SCHEMA_MISMATCH = "schema_mismatch"
ERROR_EVIDENCE_NOT_VERBATIM = "evidence_not_verbatim"
ERROR_INVALID_SCORE = "invalid_score"
ERROR_NO_EVIDENCE = "no_evidence"
ERROR_RUBRIC_MISMATCH = "rubric_version_mismatch"
ERROR_SCORE_REASON_CONFLICT = "score_reason_direction_conflict"

# A bare "I get it" / "I don't know" is an assertion, not evidence of ability.
_ASSERTION_ONLY_RE = re.compile(
    r"^\s*(我(已经)?(懂|明白|理解|会)了?|会了|懂了|明白了|知道了|"
    r"不知道|不太清楚|说不清|不会|没学过|不懂|不确定|"
    r"yes|ok|okay|i\s+get\s+it|got\s+it|no\s+idea|i\s+don'?t\s+know)"
    r"[\s。.!！~～?？]*$",
    re.IGNORECASE,
)
_MIN_SUBSTANTIVE_CHARS = 24

# Signals that the grader's own reason says the direction was wrong. Used to
# reject a score >= 1 whose justification contradicts the rubric ordering
# (direction mismatch must be 0; only "right direction, thin support" may be 1).
_DIRECTION_MISMATCH_RE = re.compile(
    r"(方向[^。；;，,]{0,10}(相反|错误|不对|反了)"
    r"|判断(方向)?[^。；;，,]{0,6}相反"
    r"|与正确(判断|答案)相反"
    r"|结论[^。；;，,]{0,6}(相反|反了))"
)


class DimensionGrade(BaseModel):
    """What the model must return for one open-answer dimension.

    Length limits are enforced by *truncation*, not rejection: a verbose
    explanation is not a reason to throw away an otherwise valid score. What is
    strictly validated is the part that carries meaning — score range, a reason,
    and the confidence label.
    """

    score: int = Field(ge=0, le=2)
    reason: str = Field(min_length=8)
    evidence_quotes: list[str] = Field(default_factory=list)
    missing_requirements: list[str] = Field(default_factory=list)
    confidence: str = Field(default="medium")

    @field_validator("reason", mode="before")
    @classmethod
    def _clamp_reason(cls, value: Any) -> Any:
        if isinstance(value, str) and len(value) > 1200:
            return value[:1200]
        return value

    @field_validator("evidence_quotes", "missing_requirements", mode="before")
    @classmethod
    def _coerce_list(cls, value: Any) -> Any:
        if value is None:
            return []
        if isinstance(value, str):
            return [value]
        if isinstance(value, (list, tuple)):
            return [str(item) for item in value if str(item).strip()][:12]
        return value

    @field_validator("confidence")
    @classmethod
    def _confidence_values(cls, value: str) -> str:
        allowed = {"low", "medium", "high"}
        return value if value in allowed else "medium"


def _normalise(text: str) -> str:
    """Collapse whitespace and unify quotes so quotes survive formatting noise."""
    text = (
        text.replace("“", '"').replace("”", '"')
        .replace("‘", "'").replace("’", "'")
        .replace("\u3000", " ")
    )
    return re.sub(r"\s+", "", text).casefold()


_QUOTE_WRAPPER_RE = re.compile(r"^[\s\"'「」『』\(\)\[\]…\.]+|[\s\"'「」『』\(\)\[\]…\.]+$")


def _strip_wrappers(quote: str) -> str:
    """Remove quoting punctuation and leading/trailing ellipses the model adds."""
    previous = None
    cleaned = quote
    while previous != cleaned:
        previous = cleaned
        cleaned = _QUOTE_WRAPPER_RE.sub("", cleaned)
    return cleaned.strip()


def _quote_is_verbatim(quote: str, answer: str) -> bool:
    needle = _normalise(_strip_wrappers(quote))
    if len(needle) < 2:
        return False
    return needle in _normalise(answer)


def _looks_like_bare_assertion(answer: str) -> bool:
    stripped = answer.strip()
    if not stripped:
        return True
    if _ASSERTION_ONLY_RE.match(stripped):
        return True
    if len(stripped) < _MIN_SUBSTANTIVE_CHARS and not re.search(r"[，,。;；:：]", stripped):
        return True
    return False


def _content_hash(payload: Any) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


@dataclass
class ObjectiveContext:
    objective_slug: str | None
    objective_id: uuid.UUID | None
    objective_text: str | None
    concept_name: str | None
    content_node_id: uuid.UUID | None


async def load_objective_context(
    db: AsyncSession,
    course_id: uuid.UUID,
    objective_slug: str | None,
    objective_id: uuid.UUID | None = None,
) -> ObjectiveContext:
    """Resolve an objective slug/id to its concept in the knowledge graph."""
    if objective_id is not None:
        node = await db.get(KnowledgeNode, objective_id)
        if node is not None and node.course_id == course_id:
            return ObjectiveContext(
                objective_slug=(node.metadata_ or {}).get("goal_slug"),
                objective_id=node.id,
                objective_text=node.description or node.name,
                concept_name=node.name,
                content_node_id=node.content_node_id,
            )
    if objective_slug:
        result = await db.execute(
            select(KnowledgeNode).where(KnowledgeNode.course_id == course_id)
        )
        for node in result.scalars().all():
            if (node.metadata_ or {}).get("goal_slug") == objective_slug:
                return ObjectiveContext(
                    objective_slug=objective_slug,
                    objective_id=node.id,
                    objective_text=node.description or node.name,
                    concept_name=node.name,
                    content_node_id=node.content_node_id,
                )
    return ObjectiveContext(
        objective_slug=objective_slug,
        objective_id=objective_id,
        objective_text=None,
        concept_name=None,
        content_node_id=None,
    )


async def _load_attempts(
    db: AsyncSession,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    problem_ids: Iterable[uuid.UUID] | None,
    content_node_id: uuid.UUID | None,
) -> list[dict[str, Any]]:
    """Load the learner's latest answer per requested objective item."""
    query = (
        select(PracticeResult, PracticeProblem)
        .join(PracticeProblem, PracticeResult.problem_id == PracticeProblem.id)
        .where(
            PracticeResult.user_id == user_id,
            PracticeProblem.course_id == course_id,
        )
        .order_by(PracticeResult.answered_at.desc())
    )
    id_list = [pid for pid in (problem_ids or [])]
    if id_list:
        query = query.where(PracticeProblem.id.in_(id_list))
    elif content_node_id is not None:
        query = query.where(PracticeProblem.content_node_id == content_node_id)
        query = query.where(PracticeProblem.is_diagnostic == False)  # noqa: E712
    else:
        return []

    rows = (await db.execute(query)).all()
    latest: dict[uuid.UUID, dict[str, Any]] = {}
    for practice_result, problem in rows:
        if problem.id in latest:
            continue
        latest[problem.id] = {
            "problem_id": str(problem.id),
            "practice_result_id": str(practice_result.id),
            "question": problem.question,
            "question_type": problem.question_type,
            "difficulty_layer": problem.difficulty_layer,
            "is_diagnostic": bool(problem.is_diagnostic),
            "user_answer": practice_result.user_answer,
            "is_correct": bool(practice_result.is_correct),
            "error_category": practice_result.error_category,
            "answered_at": practice_result.answered_at.isoformat()
            if practice_result.answered_at
            else None,
        }
    return list(latest.values())


def score_objective_items(attempts: list[dict[str, Any]]) -> tuple[int | None, dict[str, Any]]:
    """Deterministic application score from graded objective items.

    * no attempts -> ``(None, ...)`` (not measured, not zero)
    * all correct -> 2, all wrong -> 0, mixed -> 1
    """
    if not attempts:
        return None, {"instrument": "objective_items", "attempts": 0, "reason": "no_attempts"}
    correct = sum(1 for item in attempts if item["is_correct"])
    total = len(attempts)
    if correct == total:
        score = 2
    elif correct == 0:
        score = 0
    else:
        score = 1
    return score, {
        "instrument": "objective_items",
        "attempts": total,
        "correct": correct,
        "wrong": total - correct,
        "accuracy": round(correct / total, 4),
        "problem_ids": [item.get("problem_id") for item in attempts],
        "practice_result_ids": [item.get("practice_result_id") for item in attempts],
        "graded_by": "services.practice.answers (deterministic)",
    }


# ── Model-graded dimensions ────────────────────────────────────────────────

GRADING_SYSTEM_PROMPT = """你是一名严格的学习评价员。你只根据「学生原话」评分，不猜测、不补全、不因为语气自信而加分。

你必须遵守：
1. 只输出一个 JSON 对象，不要 markdown 代码块，不要多余文字。
2. score 只能是 0、1、2。
3. **先判断方向，再判断依据。** 如果学生的结论与正确判断相反，score 必须是 0，无论理由写得多长。
   只有「方向正确但依据不完整」才允许 1 分；「方向正确且逐条引用依据」才是 2 分。
4. score >= 1 时，必须给出至少一条 evidence_quotes，且必须是学生原话的逐字片段（可以包含标点），
   不得改写、不得拼接、不得引用题目文字。
5. 学生只说「我懂了」「明白了」「不知道」这类断言而没有解释时，score 必须为 0，
   并在 missing_requirements 中写明缺少解释。
6. 不确定时给较低分并在 confidence 中写 "low"，不要编造证据。
"""

GRADING_USER_TEMPLATE = """评价维度：{dimension_intent}

评分标准（固定，不可改写）：
{levels}

必须能指出的证据：{required_evidence}

学习目标：{objective}
{context_block}
学生原话：
\"\"\"
{answer}
\"\"\"

输出 JSON：
{{"score": 0,
  "reason": "为什么给这个分（引用 rubric 档位，说明缺什么）",
  "evidence_quotes": ["学生原话中的逐字片段"],
  "missing_requirements": ["缺少的必要条件"],
  "confidence": "low|medium|high"}}
"""


def _levels_text(dimension: str) -> str:
    lines = []
    for level in rubric.level_table(dimension):
        lines.append(f"- {level['score']} 分（{level['label']}）：{level['behaviour']}")
    return "\n".join(lines)


def _extract_json(text: str) -> dict[str, Any]:
    """Pull the first JSON object out of a model reply."""
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
class GradeOutcome:
    score: int | None
    payload: dict[str, Any]
    error_code: str | None = None
    error_message: str | None = None
    raw_response: str | None = None


async def grade_open_answer(
    dimension: str,
    answer: str,
    objective_text: str | None,
    *,
    extra_context: str | None = None,
    allow_repair: bool = True,
) -> GradeOutcome:
    """Grade one free-text dimension against the rubric, with evidence checks.

    When the model produces a quote that cannot be located in the answer, one
    repair attempt is made (``allow_repair``) with an explicit correction. If the
    second attempt still cannot quote the student, the dimension stays
    unmeasured and the failure is reported — it never becomes a silent number.
    """
    if answer is None:
        # Dimension was never evaluated — must stay unmeasured, not become a 0.
        return GradeOutcome(
            score=None,
            payload={"instrument": "open_answer", "reason": "not_evaluated"},
        )
    if not answer.strip():
        # The student WAS asked and submitted nothing: no ability demonstrated.
        return GradeOutcome(
            score=0,
            payload={
                "instrument": "open_answer",
                "score": 0,
                "reason": "学生被要求作答但提交为空，未展示任何可判断的目标能力，按 0 分记录",
                "evidence_quotes": [],
                "missing_requirements": ["未提交作答内容"],
                "confidence": "high",
                "guard": "empty_answer_submitted",
                "graded_by": "deterministic_guard",
            },
        )
    if _looks_like_bare_assertion(answer):
        return GradeOutcome(
            score=0,
            payload={
                "instrument": "open_answer",
                "score": 0,
                "reason": "学生只给出「懂了/会了」式断言，没有提供可核查的解释或依据",
                "evidence_quotes": [answer.strip()[:80]],
                "missing_requirements": ["缺少对概念的解释", "缺少判断依据"],
                "confidence": "high",
                "guard": "bare_assertion_detected",
                "graded_by": "deterministic_guard",
            },
        )

    try:
        from services.llm.router import get_llm_client

        client = get_llm_client()
    except Exception as exc:  # noqa: BLE001 - provider init failures are reported, not hidden
        return GradeOutcome(
            score=None,
            payload={"instrument": "open_answer"},
            error_code=ERROR_LLM_UNAVAILABLE,
            error_message=str(exc)[:500],
        )

    outcome = await _grade_once(client, dimension, answer, objective_text, extra_context)
    repair_triggered = (
        allow_repair
        and outcome.error_code in (ERROR_EVIDENCE_NOT_VERBATIM, ERROR_SCHEMA_MISMATCH)
    )
    if repair_triggered:
        if outcome.error_code == ERROR_EVIDENCE_NOT_VERBATIM:
            rejected = outcome.payload.get("evidence_quotes") or []
            correction = (
                "上一次输出的 evidence_quotes 在这段学生原话里找不到："
                f"{json.dumps(rejected, ensure_ascii=False)}。"
                "请重新输出 JSON，evidence_quotes 必须是从下面这段学生原话中**原样复制**的连续片段，"
                "不要改写、不要拼接、不要使用省略号。若确实找不到可支撑加分的内容，请给 0 分。"
            )
        else:
            correction = (
                "上一次输出不符合 JSON 结构要求："
                f"{json.dumps(outcome.payload.get('validation_errors') or [], ensure_ascii=False)[:600]}。"
                "请只输出一个 JSON 对象，字段为 score(int 0-2)、reason(string)、"
                "evidence_quotes(string 数组)、missing_requirements(string 数组)、confidence(string)。"
            )
        repaired = await _grade_once(
            client, dimension, answer, objective_text, extra_context, correction=correction
        )
        repaired.payload["repair_attempted"] = True
        repaired.payload["first_attempt_error"] = outcome.error_code
        if repaired.raw_response and outcome.raw_response:
            repaired.raw_response = (
                f"[attempt 1 rejected: {outcome.error_code}]\n{outcome.raw_response}\n\n"
                f"[attempt 2]\n{repaired.raw_response}"
            )
        return repaired
    return outcome


async def _grade_once(
    client,
    dimension: str,
    answer: str,
    objective_text: str | None,
    extra_context: str | None,
    *,
    correction: str | None = None,
) -> GradeOutcome:
    context_block = f"\n补充情境：{extra_context}\n" if extra_context else ""
    user_prompt = GRADING_USER_TEMPLATE.format(
        dimension_intent=rubric.DIMENSION_INTENT[dimension],
        levels=_levels_text(dimension),
        required_evidence=rubric.RUBRIC[dimension].required_evidence,
        objective=objective_text or "（未提供目标描述）",
        context_block=context_block,
        answer=answer.strip()[:4000],
    )
    system_prompt = GRADING_SYSTEM_PROMPT
    if correction:
        system_prompt = f"{GRADING_SYSTEM_PROMPT}\n\n纠正要求：{correction}"

    raw = None
    try:
        raw, _usage = await client.extract(system_prompt, user_prompt)
    except Exception as exc:  # noqa: BLE001
        return GradeOutcome(
            score=None,
            payload={"instrument": "open_answer"},
            error_code=ERROR_LLM_ERROR,
            error_message=str(exc)[:500],
        )

    try:
        parsed = _extract_json(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        return GradeOutcome(
            score=None,
            payload={"instrument": "open_answer"},
            error_code=ERROR_INVALID_JSON,
            error_message=str(exc)[:300],
            raw_response=raw,
        )

    try:
        grade = DimensionGrade.model_validate(parsed)
    except ValidationError as exc:
        return GradeOutcome(
            score=None,
            payload={"instrument": "open_answer", "validation_errors": exc.errors()[:10]},
            error_code=ERROR_SCHEMA_MISMATCH,
            error_message=str(exc)[:500],
            raw_response=raw,
        )

    # Score >= 1 must be backed by a quote that really exists in the answer.
    if grade.score >= 1:
        # Direction check: a reason saying the judgement was reversed can never
        # justify 1 or 2 — per the rubric that case is 0.
        if _DIRECTION_MISMATCH_RE.search(grade.reason or ""):
            return GradeOutcome(
                score=None,
                payload={
                    "instrument": "open_answer",
                    "rejected_score": grade.score,
                    "reason": grade.reason,
                    "evidence_quotes": grade.evidence_quotes,
                    "guard": "score_reason_direction_conflict",
                },
                error_code=ERROR_SCORE_REASON_CONFLICT,
                error_message=(
                    "grader reason states the judgement direction was wrong, which "
                    "the rubric scores 0, but the score was >= 1"
                ),
                raw_response=raw,
            )
        verified = [q for q in grade.evidence_quotes if _quote_is_verbatim(q, answer)]
        if not verified:
            return GradeOutcome(
                score=None,
                payload={
                    "instrument": "open_answer",
                    "rejected_score": grade.score,
                    "reason": grade.reason,
                    "evidence_quotes": grade.evidence_quotes,
                    "guard": "quote_not_found_in_answer",
                },
                error_code=ERROR_EVIDENCE_NOT_VERBATIM,
                error_message="no evidence_quote could be located verbatim in the student answer",
                raw_response=raw,
            )
        evidence = verified
    else:
        evidence = [q for q in grade.evidence_quotes if _quote_is_verbatim(q, answer)]

    return GradeOutcome(
        score=grade.score,
        payload={
            "instrument": "open_answer",
            "score": grade.score,
            "reason": grade.reason,
            "evidence_quotes": evidence,
            "missing_requirements": grade.missing_requirements,
            "confidence": grade.confidence,
            "graded_by": "llm_rubric_with_verbatim_evidence_check",
            "rubric_version": rubric.RUBRIC_VERSION,
        },
        raw_response=raw,
    )


# ── Orchestration ──────────────────────────────────────────────────────────


async def evaluate_objective(
    db: AsyncSession,
    *,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    session_id: str | None,
    objective_slug: str | None,
    objective_id: uuid.UUID | None = None,
    strategy_version: str | None = None,
    problem_ids: list[uuid.UUID] | None = None,
    open_answer: str | None = None,
    open_question: str | None = None,
    transfer_case: str | None = None,
    transfer_answer: str | None = None,
    requested_rubric_version: str | None = None,
    run_tag: str | None = None,
    persist: bool = True,
) -> TeachingEvaluation:
    """Score one objective and (optionally) persist the evaluation row."""
    if requested_rubric_version and requested_rubric_version != rubric.RUBRIC_VERSION:
        row = TeachingEvaluation(
            user_id=user_id,
            course_id=course_id,
            session_id=session_id,
            run_tag=run_tag,
            objective_slug=objective_slug,
            objective_id=objective_id,
            rubric_version=requested_rubric_version,
            status=EVAL_STATUS_FAILED,
            error_code=ERROR_RUBRIC_MISMATCH,
            error_message=(
                f"requested rubric {requested_rubric_version!r} but this build only "
                f"implements {rubric.RUBRIC_VERSION!r}"
            ),
            dimensions_measured=0,
        )
        if persist:
            db.add(row)
        return row

    objective = await load_objective_context(db, course_id, objective_slug, objective_id)
    attempts = await _load_attempts(
        db, user_id, course_id, problem_ids, objective.content_node_id
    )

    evidence: dict[str, Any] = {}
    rationale: dict[str, Any] = {}
    errors: list[dict[str, str]] = []
    raw_responses: list[str] = []

    # 1) Application — deterministic, from real graded items.
    application_score, application_detail = score_objective_items(attempts)
    evidence["application"] = application_detail
    rationale["application"] = (
        "deterministic: all objective items correct"
        if application_score == 2
        else "deterministic: no objective item correct"
        if application_score == 0
        else "deterministic: partially correct"
        if application_score == 1
        else "no objective item evidence available"
    )

    # 2) Understanding — free text against rubric.
    if open_answer is not None:
        outcome = await grade_open_answer(
            rubric.DIMENSION_UNDERSTANDING,
            open_answer,
            objective.objective_text,
            extra_context=open_question,
        )
        evidence["understanding"] = outcome.payload
        rationale["understanding"] = outcome.payload.get("reason", "")
        if outcome.raw_response:
            raw_responses.append(f"[understanding]\n{outcome.raw_response}")
        if outcome.error_code:
            errors.append(
                {"dimension": rubric.DIMENSION_UNDERSTANDING, "code": outcome.error_code,
                 "message": outcome.error_message or ""}
            )
        understanding_score = outcome.score
    else:
        understanding_score = None
        evidence["understanding"] = {"instrument": "open_answer", "reason": "not_provided"}
        rationale["understanding"] = "no open answer submitted"

    # 3) Transfer — new case, free text against rubric.
    if transfer_answer is not None:
        outcome = await grade_open_answer(
            rubric.DIMENSION_TRANSFER,
            transfer_answer,
            objective.objective_text,
            extra_context=transfer_case,
        )
        evidence["transfer"] = outcome.payload
        rationale["transfer"] = outcome.payload.get("reason", "")
        if outcome.raw_response:
            raw_responses.append(f"[transfer]\n{outcome.raw_response}")
        if outcome.error_code:
            errors.append(
                {"dimension": rubric.DIMENSION_TRANSFER, "code": outcome.error_code,
                 "message": outcome.error_message or ""}
            )
        transfer_score = outcome.score
    else:
        transfer_score = None
        evidence["transfer"] = {"instrument": "transfer", "reason": "not_provided"}
        rationale["transfer"] = "no transfer answer submitted"

    measured = sum(
        1 for value in (understanding_score, application_score, transfer_score)
        if value is not None
    )
    verdict, verdict_reason = rubric.compute_verdict(
        understanding_score, application_score, transfer_score
    )

    if errors and measured == 0:
        status = EVAL_STATUS_FAILED
    elif errors:
        status = EVAL_STATUS_PARTIAL
    elif measured == 3:
        status = EVAL_STATUS_COMPLETED
    else:
        status = EVAL_STATUS_PARTIAL

    # Context only — explicitly NOT part of the score.
    learner_context: dict[str, Any] = {}
    try:
        state = await build_learner_state(db, user_id, course_id)
        matching = next(
            (
                concept
                for concept in state["concepts"]
                if objective_slug and concept.get("goal_slug") == objective_slug
            ),
            None,
        )
        learner_context = {
            "learner_state_schema": state["schema_version"],
            "concept_state": matching["state"] if matching else None,
            "concept_mastery": matching["mastery_score"] if matching else None,
            "used_for_scoring": False,
            "note": "BKT mastery is context for humans/reflection only; it never sets a score here.",
        }
    except (SQLAlchemyError, ValueError) as exc:
        learner_context = {"error": type(exc).__name__, "used_for_scoring": False}

    inputs_payload = {
        "objective_slug": objective_slug,
        "objective_id": str(objective.objective_id) if objective.objective_id else None,
        "session_id": session_id,
        "strategy_version": strategy_version,
        "rubric_version": rubric.RUBRIC_VERSION,
        "problem_ids": [str(pid) for pid in (problem_ids or [])],
        "open_question": open_question,
        "open_answer": open_answer,
        "transfer_case": transfer_case,
        "transfer_answer": transfer_answer,
        "attempts": [
            {
                "problem_id": item["problem_id"],
                "practice_result_id": item["practice_result_id"],
                "is_correct": item["is_correct"],
            }
            for item in attempts
        ],
    }

    provider_name = None
    model_name = None
    try:
        from services.llm.router import get_llm_client

        client = get_llm_client()
        provider_name = type(client).__name__
        model_name = getattr(client, "model", None)
    except Exception:  # noqa: BLE001 - metadata only
        pass

    row = TeachingEvaluation(
        user_id=user_id,
        course_id=course_id,
        session_id=session_id,
        run_tag=run_tag,
        objective_slug=objective.objective_slug or objective_slug,
        objective_id=objective.objective_id,
        objective_text=objective.objective_text,
        rubric_version=rubric.RUBRIC_VERSION,
        strategy_version=strategy_version,
        status=status,
        verdict=verdict,
        understanding_score=understanding_score,
        application_score=application_score,
        transfer_score=transfer_score,
        dimensions_measured=measured,
        evidence_json={
            "dimensions": evidence,
            "verified_quotes_only": True,
            "learner_context": learner_context,
        },
        rationale_json={
            "dimensions": rationale,
            "verdict_reason": verdict_reason,
            "deferred": {"retention": rubric.DEFERRED_DIMENSION_NOTE},
            "comparison_warning": (
                "这是单次评价结果。跨 Run 的分数差异不能直接作为教学策略有效的证据，"
                "需要 Reflection 结合证据链判断。"
            ),
        },
        inputs_json=inputs_payload,
        content_hash=_content_hash(inputs_payload),
        raw_llm_response="\n\n".join(raw_responses) if raw_responses else None,
        error_code=errors[0]["code"] if errors else None,
        error_message="; ".join(
            f"{item['dimension']}:{item['code']}" for item in errors
        ) or None,
        model_provider=provider_name,
        model_name=model_name,
    )
    if persist:
        db.add(row)
        await db.flush()
    return row


def serialize_evaluation(row: TeachingEvaluation) -> dict[str, Any]:
    """Stable API shape for one evaluation row."""
    return {
        "evaluation_id": str(row.id),
        "student_id": str(row.user_id),
        "course_id": str(row.course_id),
        "session_id": row.session_id,
        "run_tag": row.run_tag,
        "objective_slug": row.objective_slug,
        "objective_id": str(row.objective_id) if row.objective_id else None,
        "objective_text": row.objective_text,
        "rubric_version": row.rubric_version,
        "strategy_version": row.strategy_version,
        "status": row.status,
        "verdict": row.verdict,
        "scores": {
            "understanding": row.understanding_score,
            "application": row.application_score,
            "transfer": row.transfer_score,
            "retention": None,
        },
        "dimensions_measured": row.dimensions_measured,
        "unmeasured_dimensions": [
            name
            for name, value in (
                ("understanding", row.understanding_score),
                ("application", row.application_score),
                ("transfer", row.transfer_score),
            )
            if value is None
        ],
        "evidence": row.evidence_json,
        "rationale": row.rationale_json,
        "inputs": row.inputs_json,
        "content_hash": row.content_hash,
        "error_code": row.error_code,
        "error_message": row.error_message,
        "model": {"provider": row.model_provider, "name": row.model_name},
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }
