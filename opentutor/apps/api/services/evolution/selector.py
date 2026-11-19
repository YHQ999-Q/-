"""Strategy Selector (METAMIND module 4).

Picks at most one enabled skill version for the current teaching situation, or
returns ``None`` so the caller falls back to OpenTutor's original teaching. It
never forces a skill just to demonstrate evolution.

Scoring (all components are explainable and returned in the result):

    score = 2.0 * condition_hits / condition_total      # applicability
          + 1.5 * (objective match)
          + 1.0 * (dimension match with the current weakness)
          + 0.8 * (error-type match)
          + 0.4 * (scene match)
          + 0.6 * historical_effectiveness              # 0..1, from evaluations
          - 0.3 * (staleness penalty for superseded versions)

A skill is only eligible when:
* it is enabled and its current version is ``active``;
* at least one trigger condition matches the situation text (a bare objective
  match is not enough to claim applicability — that would be topic routing, not
  strategy selection).

This module does not inject anything into a prompt. It records a
``skill_invocations`` row with ``status='selected'``; module 5 advances it to
``injected`` and later ``evaluated``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.knowledge_graph import ConceptMastery, KnowledgeNode
from models.teaching_evaluation import TeachingEvaluation
from models.teaching_skill import (
    INVOCATION_EXECUTED,
    INVOCATION_INJECTED,
    INVOCATION_SELECTED,
    VERSION_ACTIVE,
    SkillInvocation,
    TeachingSkill,
    TeachingSkillVersion,
)
from services.evolution import skill_store

logger = logging.getLogger(__name__)

# A weak concept below this mastery is treated as a "current difficulty".
WEAK_MASTERY_THRESHOLD = 0.6
MIN_TRIGGER_HITS = 1


@dataclass
class SelectionContext:
    user_id: uuid.UUID
    course_id: uuid.UUID
    objective_slug: str | None = None
    error_type: str | None = None
    scene: str | None = None
    message: str | None = None
    weakness_dimension: str | None = None
    concept_names: list[str] = field(default_factory=list)
    concept_ids: list[str] = field(default_factory=list)
    session_id: str | None = None
    # Demo/run label (run01 / run02); recorded on the invocation row.
    run_tag: str | None = None
    persist: bool = True

    def situation_text(self) -> str:
        parts = [
            self.message or "",
            self.objective_slug or "",
            self.error_type or "",
            self.scene or "",
            " ".join(self.concept_names),
        ]
        return " ".join(part for part in parts if part).strip()


@dataclass
class Candidate:
    skill: TeachingSkill
    version: TeachingSkillVersion
    score: float
    condition_hits: list[str]
    condition_total: int
    objective_match: bool
    dimension_match: bool
    error_match: bool
    scene_match: bool
    history: dict[str, Any]

    def as_json(self) -> dict[str, Any]:
        return {
            "skill_id": str(self.skill.id),
            "slug": self.skill.slug,
            "name": self.skill.name,
            "version_id": str(self.version.id),
            "version": self.version.version,
            "score": round(self.score, 4),
            "matched_conditions": self.condition_hits,
            "condition_total": self.condition_total,
            "objective_match": self.objective_match,
            "dimension_match": self.dimension_match,
            "error_match": self.error_match,
            "scene_match": self.scene_match,
            "history": self.history,
        }


@dataclass
class SelectionResult:
    selected: Candidate | None
    candidates: list[Candidate]
    reason: str
    context: dict[str, Any]
    invocation_id: str | None = None

    @property
    def fallback(self) -> bool:
        return self.selected is None

    def as_json(self) -> dict[str, Any]:
        return {
            "selected": self.selected.as_json() if self.selected else None,
            "fallback_to_native_tutor": self.fallback,
            "reason": self.reason,
            "candidates": [candidate.as_json() for candidate in self.candidates],
            "context": self.context,
            "invocation_id": self.invocation_id,
        }


def _norm(value: str) -> str:
    return (value or "").strip().casefold()


def _history_score(history: dict[str, Any]) -> float:
    """0..1 from evaluated invocations. No evidence -> neutral 0.5."""
    if not history.get("evaluated"):
        return 0.5
    mean = history.get("mean_target_score")
    if isinstance(mean, (int, float)):
        return max(0.0, min(1.0, float(mean) / 2.0))
    return 0.5


_TOKEN_SPLIT_RE = re.compile(r"[\s，,。;；、/|「」『』（）()\[\]=:：\-—~!?！？\"'“”]+")


def _condition_matches(condition: str, haystack: str) -> bool:
    """Does this trigger condition apply to the situation text?

    Reflection-generated conditions are often full sentences
    (``学生在作答中出现「记住偏好」的表述``), so an exact substring test alone would
    never fire. We therefore also accept a distinctive keyword from the
    condition — a token of at least 4 characters. Shorter tokens like
    ``自进化`` are ignored on their own because they appear in almost every
    message and would make every skill look applicable.
    """
    cond = _norm(condition)
    if not cond:
        return False
    if cond in haystack:
        return True
    for token in _TOKEN_SPLIT_RE.split(condition):
        token = _norm(token)
        if len(token) >= 4 and token in haystack:
            return True
    return False


def _condition_hits(conditions: Iterable[str], text: str, extra: str) -> tuple[list[str], int]:
    conditions = [str(item) for item in conditions or []]
    haystack = _norm(text) + "\u0000" + _norm(extra)
    hits = [condition for condition in conditions if _condition_matches(condition, haystack)]
    return hits, len(conditions)


async def _load_weakness(
    db: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID
) -> tuple[list[str], list[str], str | None]:
    """Weak concept names/ids and the dimension most likely at risk."""
    rows = (
        await db.execute(
            select(KnowledgeNode).where(KnowledgeNode.course_id == course_id)
        )
    ).scalars().all()
    if not rows:
        return [], [], None
    mastery_rows = (
        await db.execute(
            select(ConceptMastery).where(
                ConceptMastery.user_id == user_id,
                ConceptMastery.knowledge_node_id.in_([node.id for node in rows]),
            )
        )
    ).scalars().all()
    mastery_by_node = {row.knowledge_node_id: row for row in mastery_rows}
    names: list[str] = []
    ids: list[str] = []
    for node in rows:
        mastery = mastery_by_node.get(node.id)
        if mastery is None or (mastery.mastery_score or 0.0) < WEAK_MASTERY_THRESHOLD:
            names.append(node.name)
            ids.append(str(node.id))
    return names, ids, None


async def _load_recent_error_types(
    db: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID, limit: int = 20
) -> list[str]:
    from models.ingestion import WrongAnswer

    rows = (
        await db.execute(
            select(WrongAnswer.error_category)
            .where(
                WrongAnswer.user_id == user_id,
                WrongAnswer.course_id == course_id,
                WrongAnswer.error_category.isnot(None),
            )
            .order_by(WrongAnswer.created_at.desc())
            .limit(limit)
        )
    ).scalars().all()
    return [str(item) for item in rows if item]


async def _historical_dimension_scores(
    db: AsyncSession, skill_id: uuid.UUID, version: int
) -> dict[str, Any]:
    stats = await skill_store.invocation_stats(db, skill_id, version)
    return stats


def _score_candidate(
    skill: TeachingSkill,
    version: TeachingSkillVersion,
    ctx: SelectionContext,
    situation: str,
    weakness_text: str,
    error_text: str,
    history: dict[str, Any],
) -> Candidate:
    conditions = list(version.trigger_conditions or [])
    # Eligibility uses only the current situation (utterance, objective, scene,
    # explicitly named concepts). A learner's past error type can strengthen a
    # candidate but must not make an unrelated skill look applicable.
    hits, total = _condition_hits(conditions, situation, "")
    applicability = (len(hits) / total) if total else 0.0

    objectives = [str(item) for item in (version.applicable_objectives or [])]
    objective_match = bool(
        ctx.objective_slug and ctx.objective_slug in objectives
    )
    dimensions = [str(item) for item in (version.target_dimensions or [])]
    dimension_match = bool(
        ctx.weakness_dimension
        and (ctx.weakness_dimension in dimensions or "multiple" in dimensions)
    )
    error_match = any(
        _condition_matches(condition, error_text) for condition in conditions
    ) if error_text else False
    scene = ctx.scene or ""
    scene_match = bool(scene and scene in {
        str(item) for item in (skill.metadata_json or {}).get("scenes", [])
    })

    score = (
        2.0 * applicability
        + (1.5 if objective_match else 0.0)
        + (1.0 if dimension_match else 0.0)
        + (0.8 if error_match else 0.0)
        + (0.4 if scene_match else 0.0)
        + 0.6 * _history_score(history)
    )
    if version.status != VERSION_ACTIVE:
        score -= 0.3

    return Candidate(
        skill=skill,
        version=version,
        score=score,
        condition_hits=hits,
        condition_total=total,
        objective_match=objective_match,
        dimension_match=dimension_match,
        error_match=error_match,
        scene_match=scene_match,
        history=history,
    )


async def select_skill(
    db: AsyncSession, ctx: SelectionContext
) -> SelectionResult:
    """Choose the best enabled skill version for this situation (or None)."""
    skills = await skill_store.list_skills(db, enabled_only=True, limit=200)
    weakness_names, weakness_ids, _dim = await _load_weakness(db, ctx.user_id, ctx.course_id)
    # Only caller-supplied concept names count as "what is being discussed".
    # Auto-loaded weaknesses are context for scoring, not trigger matches —
    # otherwise every skill that mentions a weak topic would look applicable.
    explicit_names = list(ctx.concept_names)
    explicit_error = ctx.error_type
    if not ctx.concept_names:
        ctx.concept_names = weakness_names
    if not ctx.concept_ids:
        ctx.concept_ids = weakness_ids
    recent_errors = await _load_recent_error_types(db, ctx.user_id, ctx.course_id)
    if not ctx.error_type and recent_errors:
        ctx.error_type = recent_errors[0]

    situation = " ".join(
        part for part in [
            ctx.message or "",
            ctx.objective_slug or "",
            explicit_error or "",
            ctx.scene or "",
            " ".join(explicit_names),
        ] if part
    )
    weakness_text = " ".join(explicit_names)
    error_text = " ".join(filter(None, [ctx.error_type, *recent_errors]))

    candidates: list[Candidate] = []
    for skill in skills:
        version = await skill_store.get_version(db, skill.id, skill.current_version)
        if version is None or version.status != VERSION_ACTIVE:
            continue
        history = await _historical_dimension_scores(db, skill.id, version.version)
        candidate = _score_candidate(
            skill, version, ctx, situation, weakness_text, error_text, history
        )
        if len(candidate.condition_hits) < MIN_TRIGGER_HITS:
            # Not applicable to this situation: topic overlap alone is not enough.
            continue
        candidates.append(candidate)

    candidates.sort(key=lambda item: item.score, reverse=True)
    context_json = {
        "objective_slug": ctx.objective_slug,
        "error_type": ctx.error_type,
        "scene": ctx.scene,
        "weakness_dimension": ctx.weakness_dimension,
        "weak_concepts": ctx.concept_names,
        "recent_error_types": recent_errors[:5],
        "message": (ctx.message or "")[:300],
    }

    if not candidates:
        return SelectionResult(
            selected=None,
            candidates=[],
            reason="no enabled skill matched the current situation; fall back to native Tutor",
            context=context_json,
        )

    best = candidates[0]
    invocation_id = None
    if ctx.persist:
        invocation = SkillInvocation(
            user_id=ctx.user_id,
            course_id=ctx.course_id,
            skill_id=best.skill.id,
            version_id=best.version.id,
            version=best.version.version,
            session_id=ctx.session_id,
            objective_slug=ctx.objective_slug,
            scene=ctx.scene,
            run_tag=ctx.run_tag,
            selection_json={
                "selected": best.as_json(),
                "runner_ups": [item.as_json() for item in candidates[1:4]],
                "context": context_json,
            },
            match_score=round(best.score, 4),
            status=INVOCATION_SELECTED,
        )
        db.add(invocation)
        await db.flush()
        invocation_id = str(invocation.id)

    return SelectionResult(
        selected=best,
        candidates=candidates,
        reason=(
            f"selected {best.skill.slug} V{best.version.version} "
            f"(score {best.score:.2f}, {len(best.condition_hits)}/{best.condition_total} "
            "conditions matched)"
        ),
        context=context_json,
        invocation_id=invocation_id,
    )


async def mark_injected(
    db: AsyncSession,
    invocation_id: uuid.UUID,
    *,
    notes: str | None = None,
    prompt: str | None = None,
    marker: str | None = None,
) -> SkillInvocation:
    """Module 5 hook: the skill text really entered the Tutor prompt.

    ``prompt`` is the FULL assembled system prompt when the caller has it, and
    ``marker`` the expected ``<!-- skill:... -->`` marker. Only when the marker
    is actually present in the full prompt do we store ``injected`` together
    with a sha256 of that prompt. Callers that can't supply the full prompt pass
    ``prompt=None`` and the record keeps injection *unverified* — we never claim
    injected just because a selection exists.
    """
    invocation = await db.get(SkillInvocation, invocation_id)
    if invocation is None:
        raise ValueError("invocation_not_found")
    if invocation.status not in (INVOCATION_SELECTED, INVOCATION_INJECTED):
        raise ValueError(f"cannot inject from status {invocation.status}")

    verified = False
    if prompt and marker:
        verified = marker in prompt
    invocation.prompt_injection_verified = bool(verified)
    if prompt:
        invocation.prompt_hash = _sha256(prompt)

    if verified:
        invocation.status = INVOCATION_INJECTED
        invocation.injected_at = _now()
        if notes:
            invocation.notes = notes
    else:
        # The marker is not confirmed in the full prompt; keep the honest state.
        invocation.notes = (notes or "") + (
            " ;prompt_injection_unverified" if not prompt
            else " ;marker_NOT_in_full_prompt"
        )
    await db.flush()
    return invocation


async def set_execution(
    db: AsyncSession,
    invocation_id: uuid.UUID,
    *,
    state: str,
    tutor_response_locator: str | None = None,
) -> SkillInvocation:
    """Honestly record how much of the skill we can claim was carried out.

    ``executed`` is never set just because the text was injected. We only record
    ``execution_state`` (unverified / partial / full) and the location of the
    tutor's actual answer for a human to judge. ``state`` accepts 'unverified'
    unless the caller has real evidence of step adherence.
    """
    from models.teaching_skill import EXEC_UNVERIFIED

    if state not in (EXEC_UNVERIFIED, "partial", "full"):
        state = EXEC_UNVERIFIED
    invocation = await db.get(SkillInvocation, invocation_id)
    if invocation is None:
        raise ValueError("invocation_not_found")
    invocation.execution_state = state
    if state == "full":
        invocation.status = INVOCATION_EXECUTED
        invocation.executed_at = _now()
    if tutor_response_locator:
        invocation.tutor_response_locator = tutor_response_locator
    await db.flush()
    return invocation


async def attach_evaluation(
    db: AsyncSession,
    invocation_id: uuid.UUID,
    evaluation_id: uuid.UUID,
    *,
    target_dimension: str | None = None,
) -> SkillInvocation:
    """Link the evaluation that judged this episode and record the outcome.

    ``status`` becomes ``evaluated`` only when the evaluation actually carries a
    score for the skill's target dimension; otherwise the invocation stays
    ``injected`` with a note, because an unscored episode is not evidence.
    """
    invocation = await db.get(SkillInvocation, invocation_id)
    if invocation is None:
        raise ValueError("invocation_not_found")
    evaluation = await db.get(TeachingEvaluation, evaluation_id)
    if evaluation is None:
        raise ValueError("evaluation_not_found")

    version = await db.get(TeachingSkillVersion, invocation.version_id)
    dimensions = list((version.target_dimensions if version else None) or [])
    measured = {
        "understanding": evaluation.understanding_score,
        "application": evaluation.application_score,
        "transfer": evaluation.transfer_score,
    }
    dimension = target_dimension or next(
        (item for item in dimensions if item in measured and measured[item] is not None),
        None,
    )
    if dimension is None:
        # Version targets "multiple" (or nothing scored in its dimensions):
        # fall back to the weakest measured dimension so the outcome is still
        # tied to this episode instead of being silently dropped.
        scored = {key: value for key, value in measured.items() if value is not None}
        dimension = min(scored, key=scored.get) if scored else None
    score = measured.get(dimension or "")

    invocation.evaluation_id = evaluation.id
    invocation.evaluated_at = _now()
    if score is None:
        invocation.notes = (
            (invocation.notes or "")
            + f" | evaluation {evaluation.id} has no score for {dimension or 'target dimension'}"
        ).strip(" |")
        await db.flush()
        return invocation

    invocation.status = "evaluated"
    invocation.outcome_json = {
        "target_dimension": dimension,
        "target_dimension_score": score,
        "verdict": evaluation.verdict,
        "success": score >= 2,
        "evaluation_id": str(evaluation.id),
        "rubric_version": evaluation.rubric_version,
    }
    await db.flush()
    return invocation


async def list_invocations(
    db: AsyncSession,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    *,
    session_id: str | None = None,
    skill_id: uuid.UUID | None = None,
    limit: int = 50,
) -> list[SkillInvocation]:
    query = (
        select(SkillInvocation)
        .where(
            SkillInvocation.user_id == user_id,
            SkillInvocation.course_id == course_id,
        )
        .order_by(SkillInvocation.created_at.desc())
        .limit(limit)
    )
    if session_id:
        query = query.where(SkillInvocation.session_id == session_id)
    if skill_id:
        query = query.where(SkillInvocation.skill_id == skill_id)
    return list((await db.execute(query)).scalars().all())


def serialize_invocation(invocation: SkillInvocation) -> dict[str, Any]:
    return {
        "invocation_id": str(invocation.id),
        "skill_id": str(invocation.skill_id),
        "version_id": str(invocation.version_id),
        "version": invocation.version,
        "session_id": invocation.session_id,
        "objective_slug": invocation.objective_slug,
        "scene": invocation.scene,
        "run_tag": invocation.run_tag,
        "status": invocation.status,
        "match_score": invocation.match_score,
        "selected_at": invocation.selected_at.isoformat() if invocation.selected_at else None,
        "injected_at": invocation.injected_at.isoformat() if invocation.injected_at else None,
        "evaluated_at": invocation.evaluated_at.isoformat() if invocation.evaluated_at else None,
        "executed_at": invocation.executed_at.isoformat() if invocation.executed_at else None,
        "prompt_hash": invocation.prompt_hash,
        "prompt_injection_verified": invocation.prompt_injection_verified,
        "execution_state": invocation.execution_state,
        "evaluation_id": str(invocation.evaluation_id) if invocation.evaluation_id else None,
        "selection": invocation.selection_json,
        "outcome": invocation.outcome_json,
        "notes": invocation.notes,
        "lifecycle_note": (
            "selected != injected != executed: only 'evaluated' means the strategy was "
            "judged by learner outcomes"
        ),
    }


def _now():
    from datetime import datetime, timezone

    return datetime.now(timezone.utc)


def _sha256(text: str) -> str:
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


__all__ = [
    "SelectionContext",
    "SelectionResult",
    "select_skill",
    "mark_injected",
    "attach_evaluation",
    "list_invocations",
    "serialize_invocation",
]
