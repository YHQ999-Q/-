"""Learner Model aggregation layer (METAMIND module 1).

Answers one question for the Evolution Engine: **what state is this learner in
right now?** — without re-implementing any learning-science model.

Design rules (deliberate, do not "simplify" them away):

1. **Reuse, never duplicate.** Mastery comes from ``ConceptMastery`` (written by
   ``services/loom_mastery`` via BKT) and ``LearningProgress`` (written by
   ``services/progress/tracker`` via BKT + weighted decay + FSRS). This module
   only reads them. ``MasterySnapshot`` is reused for time-series, no new table.
2. **No record is not zero mastery.** A concept with no practice history is
   reported as ``not_assessed`` with ``mastery_score = None``. Never ``0.0``.
   Course-level ``overall_mastery`` is ``None`` when nothing has been assessed.
3. **Nothing here mutates learner state.** The only writes are append-only
   ``MasterySnapshot`` rows, and only when explicitly requested through
   :func:`write_state_snapshot`.
4. **Everything is traceable.** Each concept entry carries the raw upstream
   values plus the evidence ids used to reach the verdict, and the response
   records which source tables were read.

Snapshot tagging convention
---------------------------
``MasterySnapshot`` has no metadata column and is keyed by
``(user_id, course_id, content_node_id)``. Concept-level tracking therefore
uses the concept's linked ``content_node_id``, with ``gap_type`` carrying a
namespaced tag instead of a pedagogical gap label::

    mm:<band>                 e.g. "mm:assessed_low"
    mm:<run_tag>:<band>       e.g. "mm:run01:assessed_low"

Any row whose ``gap_type`` starts with ``mm:`` was written by METAMIND;
``services/progress/tracker.py`` rows stay untagged, so the two never mix.
"""

from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Iterable

from sqlalchemy import desc, func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from models.course import Course
from models.ingestion import WrongAnswer
from models.knowledge_graph import ConceptMastery, KnowledgeNode
from models.mastery_snapshot import MasterySnapshot
from models.practice import PracticeProblem, PracticeResult
from models.progress import LearningProgress
from models.study_goal import StudyGoal

logger = logging.getLogger(__name__)

SCHEMA_VERSION = "metamind.learner_state.v1"

# Reuse the upstream mastery threshold instead of inventing a new one.
from services.learning_science.velocity_tracker import MASTERY_THRESHOLD  # noqa: E402

# Mirrors the rule that ``services/progress/tracker.py`` uses before it sets
# ``LearningProgress.status = "mastered"`` (score >= 0.8 AND >= 3 attempts).
MASTERED_MIN_ATTEMPTS = 3

STATE_NOT_ASSESSED = "not_assessed"
STATE_ASSESSED_BELOW_MASTERY = "assessed_below_mastery"
STATE_MASTERED = "mastered"

SNAPSHOT_TAG_PREFIX = "mm:"
_SHORT_BAND = {STATE_ASSESSED_BELOW_MASTERY: "assessed_low", STATE_MASTERED: "mastered"}
_RUN_TAG_RE = re.compile(r"^[a-z0-9_-]{1,12}$")
_EVIDENCE_SAMPLE_LIMIT = 5


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


def _band_state(mastery: float | None, attempts: int) -> tuple[str, str]:
    """Return ``(state, state_reason)`` from raw upstream signals.

    ``mastery is None`` means "never assessed" — it is *not* a score of zero.
    """
    if mastery is None or attempts <= 0:
        return STATE_NOT_ASSESSED, "no_practice_evidence"
    if mastery >= MASTERY_THRESHOLD and attempts >= MASTERED_MIN_ATTEMPTS:
        return STATE_MASTERED, "score_and_attempts_meet_mastery"
    if mastery >= MASTERY_THRESHOLD:
        return STATE_ASSESSED_BELOW_MASTERY, "score_met_but_attempts_low"
    return STATE_ASSESSED_BELOW_MASTERY, "score_below_mastery_threshold"


async def _load_concepts(db: AsyncSession, course_id: uuid.UUID) -> list[KnowledgeNode]:
    result = await db.execute(
        select(KnowledgeNode)
        .where(KnowledgeNode.course_id == course_id)
        .order_by(KnowledgeNode.created_at, KnowledgeNode.name)
    )
    return list(result.scalars().all())


async def _load_concept_mastery(
    db: AsyncSession, user_id: uuid.UUID, node_ids: Iterable[uuid.UUID]
) -> dict[uuid.UUID, ConceptMastery]:
    node_ids = list(node_ids)
    if not node_ids:
        return {}
    result = await db.execute(
        select(ConceptMastery).where(
            ConceptMastery.user_id == user_id,
            ConceptMastery.knowledge_node_id.in_(node_ids),
        )
    )
    return {row.knowledge_node_id: row for row in result.scalars().all()}


async def _load_progress(
    db: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID
) -> list[LearningProgress]:
    result = await db.execute(
        select(LearningProgress)
        .where(
            LearningProgress.user_id == user_id,
            LearningProgress.course_id == course_id,
        )
        .order_by(LearningProgress.created_at)
    )
    return list(result.scalars().all())


async def _load_results_by_content_node(
    db: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID
) -> dict[uuid.UUID | None, list[PracticeResult]]:
    """Group this learner's practice results by the problem's content node."""
    result = await db.execute(
        select(PracticeResult, PracticeProblem.content_node_id, PracticeProblem.question_type)
        .join(PracticeProblem, PracticeResult.problem_id == PracticeProblem.id)
        .where(
            PracticeResult.user_id == user_id,
            PracticeProblem.course_id == course_id,
            PracticeProblem.is_diagnostic == False,  # noqa: E712 — derived drill-downs
        )
        .order_by(desc(PracticeResult.answered_at))
    )
    grouped: dict[uuid.UUID | None, list[PracticeResult]] = {}
    for practice_result, content_node_id, _question_type in result.all():
        grouped.setdefault(content_node_id, []).append(practice_result)
    return grouped


async def _load_wrong_answer_profile(
    db: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID
) -> dict[str, Any]:
    result = await db.execute(
        select(WrongAnswer.error_category, WrongAnswer.mastered, func.count(WrongAnswer.id))
        .where(WrongAnswer.user_id == user_id, WrongAnswer.course_id == course_id)
        .group_by(WrongAnswer.error_category, WrongAnswer.mastered)
    )
    by_category: dict[str, int] = {}
    open_total = 0
    for category, mastered, count in result.all():
        key = category or "unclassified"
        by_category[key] = by_category.get(key, 0) + int(count)
        if not mastered:
            open_total += int(count)
    ordered = sorted(by_category.items(), key=lambda item: item[1], reverse=True)
    return {
        "by_category": dict(ordered),
        "common_errors": [{"category": k, "count": v} for k, v in ordered],
        "open_wrong_answers": open_total,
        "total_wrong_answers": sum(by_category.values()),
    }


async def _load_wrong_categories_by_problem(
    db: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID
) -> dict[uuid.UUID, dict[str, int]]:
    """Per-problem error categories, so each concept can show its own error mix."""
    result = await db.execute(
        select(WrongAnswer.problem_id, WrongAnswer.error_category, func.count(WrongAnswer.id))
        .where(WrongAnswer.user_id == user_id, WrongAnswer.course_id == course_id)
        .group_by(WrongAnswer.problem_id, WrongAnswer.error_category)
    )
    profile: dict[uuid.UUID, dict[str, int]] = {}
    for problem_id, category, count in result.all():
        bucket = profile.setdefault(problem_id, {})
        key = category or "unclassified"
        bucket[key] = bucket.get(key, 0) + int(count)
    return profile


async def _load_goals(db: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID) -> list[StudyGoal]:
    result = await db.execute(
        select(StudyGoal)
        .where(StudyGoal.user_id == user_id, StudyGoal.course_id == course_id)
        .order_by(StudyGoal.created_at)
    )
    return list(result.scalars().all())


def _fallback_mastery_from_sequence(
    results: list[PracticeResult], question_type: str | None
) -> float | None:
    """BKT recomputation used only when ``ConceptMastery`` is missing entirely.

    Reuses the upstream tracer; it never writes back.
    """
    if not results:
        return None
    sequence = [bool(item.is_correct) for item in reversed(results)]
    try:
        from services.learning_science.knowledge_tracer import compute_mastery_from_sequence

        return float(compute_mastery_from_sequence(sequence, question_type))
    except (ImportError, ValueError, RuntimeError):  # pragma: no cover - defensive
        logger.exception("Fallback BKT recomputation failed")
        return None


def _goal_slug(entity_metadata: dict | None) -> str | None:
    if isinstance(entity_metadata, dict):
        value = entity_metadata.get("goal_slug")
        if isinstance(value, str) and value:
            return value
    return None


async def build_learner_state(
    db: AsyncSession,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    *,
    course: Course | None = None,
) -> dict[str, Any]:
    """Aggregate the learner's real state for one course (read-only)."""
    course = course or await db.get(Course, course_id)
    if course is None:
        raise ValueError(f"course not found: {course_id}")

    concepts = await _load_concepts(db, course_id)
    node_ids = [node.id for node in concepts]
    mastery_map = await _load_concept_mastery(db, user_id, node_ids)
    progress_rows = await _load_progress(db, user_id, course_id)
    progress_by_node = {
        row.content_node_id: row for row in progress_rows if row.content_node_id is not None
    }
    results_by_node = await _load_results_by_content_node(db, user_id, course_id)
    wrong_by_problem = await _load_wrong_categories_by_problem(db, user_id, course_id)
    error_profile = await _load_wrong_answer_profile(db, user_id, course_id)
    goals = await _load_goals(db, user_id, course_id)

    now = datetime.now(timezone.utc)
    warnings: list[str] = []
    concept_entries: list[dict[str, Any]] = []
    concept_by_goal: dict[str, dict[str, Any]] = {}

    for node in concepts:
        content_node_id = node.content_node_id
        mastery_row = mastery_map.get(node.id)
        progress_row = progress_by_node.get(content_node_id) if content_node_id else None
        results = results_by_node.get(content_node_id, []) if content_node_id else []

        attempts_direct = max(mastery_row.practice_count if mastery_row else 0, len(results))
        mastery_score: float | None = None
        mastery_source = "none"

        if mastery_row is not None and mastery_row.practice_count > 0:
            mastery_score = float(mastery_row.mastery_score)
            mastery_source = "concept_mastery"
        elif results:
            question_type = None
            mastery_score = _fallback_mastery_from_sequence(results, question_type)
            mastery_source = "bkt_recomputed_from_results"
            warnings.append(
                f"concept {node.name!r}: ConceptMastery row missing but {len(results)} practice "
                "result(s) exist; mastery recomputed read-only via BKT tracer"
            )
        elif mastery_row is not None:
            # A row with zero direct practice (e.g. only graph propagation) is an
            # inference artefact, not an assessment of this learner.
            mastery_source = "inferred_only_no_direct_practice"
            warnings.append(
                f"concept {node.name!r}: ConceptMastery row has no direct practice; "
                "treated as not_assessed"
            )

        state, state_reason = _band_state(mastery_score, attempts_direct)

        # ── Count provenance: never blend two sources into one ratio ──
        # ``practice_results`` and ``concept_mastery`` counters are written by
        # different code paths and their totals can legitimately disagree (e.g.
        # legacy rows, rows pruned by reset, graph-only inferences). Each is
        # reported raw; ``accuracy`` is computed from practice_results ONLY so
        # numerator and denominator always describe the same set of attempts.
        pr_total = len(results)
        pr_correct = sum(1 for item in results if item.is_correct)
        cm_total = int(mastery_row.practice_count) if mastery_row is not None else 0
        cm_correct = int(mastery_row.correct_count) if mastery_row is not None else 0
        cm_wrong = int(mastery_row.wrong_count) if mastery_row is not None else 0

        per_concept_errors: dict[str, int] = {}
        for item in results:
            for category, count in (wrong_by_problem.get(item.problem_id) or {}).items():
                per_concept_errors[category] = per_concept_errors.get(category, 0) + count

        goal_slug = _goal_slug(node.metadata_)
        entry = {
            "concept_id": str(node.id),
            "name": node.name,
            "description": node.description,
            "content_node_id": str(content_node_id) if content_node_id else None,
            "goal_slug": goal_slug,
            "state": state,
            "state_reason": state_reason,
            "mastery_score": round(mastery_score, 4) if mastery_score is not None else None,
            "mastery_source": mastery_source,
            "mastery_threshold": MASTERY_THRESHOLD,
            "mastered_min_attempts": MASTERED_MIN_ATTEMPTS,
            "direct_attempts": attempts_direct,
            "direct_attempts_basis": (
                "concept_mastery.practice_count"
                if cm_total >= pr_total
                else "practice_results_count"
            ),
            "correct": pr_correct,
            "wrong": pr_total - pr_correct,
            "accuracy": round(pr_correct / pr_total, 4) if pr_total else None,
            "accuracy_basis": "practice_results" if pr_total else None,
            "practice_result_counts": {
                "total": pr_total,
                "correct": pr_correct,
                "wrong": pr_total - pr_correct,
            },
            "concept_mastery_counts": {
                "practice_count": cm_total,
                "correct": cm_correct,
                "wrong": cm_wrong,
            },
            "counts_consistent": pr_total == cm_total and pr_correct == cm_correct,
            "last_practiced_at": _iso(
                mastery_row.last_practiced_at if mastery_row else None
            ),
            "next_review_at": _iso(mastery_row.next_review_at if mastery_row else None),
            "stability_days": round(float(mastery_row.stability_days), 3) if mastery_row else None,
            "error_categories": dict(
                sorted(per_concept_errors.items(), key=lambda item: item[1], reverse=True)
            ),
            "evidence_practice_result_ids": [str(item.id) for item in results[:_EVIDENCE_SAMPLE_LIMIT]],
            "progress": {
                "status": progress_row.status if progress_row else None,
                "mastery_score": round(float(progress_row.mastery_score), 4) if progress_row else None,
                "quiz_attempts": progress_row.quiz_attempts if progress_row else 0,
                "quiz_correct": progress_row.quiz_correct if progress_row else 0,
                "gap_type": progress_row.gap_type if progress_row else None,
                "time_spent_minutes": progress_row.time_spent_minutes if progress_row else 0,
                "fsrs_state": progress_row.fsrs_state if progress_row else None,
            },
            "content_node_missing": content_node_id is None,
        }
        if content_node_id is None:
            warnings.append(
                f"concept {node.name!r} has no content_node_id; it cannot be snapshotted "
                "into mastery_snapshots (which is keyed by content_node_id)"
            )
        if progress_row is None and content_node_id is not None and results:
            warnings.append(
                f"concept {node.name!r}: practice results exist but no LearningProgress row"
            )
        concept_entries.append(entry)
        if goal_slug:
            concept_by_goal[goal_slug] = entry

    assessed = [c for c in concept_entries if c["state"] != STATE_NOT_ASSESSED]
    mastered = [c for c in concept_entries if c["state"] == STATE_MASTERED]

    if not assessed:
        summary_state = "not_assessed"
    elif concept_entries and len(mastered) == len(concept_entries):
        summary_state = "all_mastered"
    else:
        # Some evidence exists but at least one concept is still below mastery.
        summary_state = "partially_assessed"

    overall_mastery = (
        round(sum(c["mastery_score"] for c in assessed) / len(assessed), 4) if assessed else None
    )

    weak = sorted(
        [c for c in assessed if c["state"] != STATE_MASTERED],
        key=lambda c: (c["mastery_score"] if c["mastery_score"] is not None else 1.0),
    )

    review_due = [
        {
            "concept_id": c["concept_id"],
            "name": c["name"],
            "next_review_at": c["next_review_at"],
            "mastery_score": c["mastery_score"],
        }
        for c in concept_entries
        if c["next_review_at"] is not None
        and datetime.fromisoformat(c["next_review_at"]) <= now
    ]

    goal_entries = []
    for goal in goals:
        slug = _goal_slug(goal.metadata_json)
        concept = concept_by_goal.get(slug) if slug else None
        goal_entries.append(
            {
                "goal_id": str(goal.id),
                "goal_slug": slug,
                "title": goal.title,
                "objective": goal.objective,
                "success_metric": goal.success_metric,
                "status": goal.status,
                "concept_id": concept["concept_id"] if concept else None,
                "concept_state": concept["state"] if concept else None,
                "concept_mastery": concept["mastery_score"] if concept else None,
            }
        )

    quiz_attempts = sum(row.quiz_attempts for row in progress_rows)
    quiz_correct = sum(row.quiz_correct for row in progress_rows)
    time_spent = sum(row.time_spent_minutes for row in progress_rows)
    last_studied = max(
        (row.last_studied_at for row in progress_rows if row.last_studied_at),
        default=None,
    )
    gap_types = sorted({row.gap_type for row in progress_rows if row.gap_type})

    total_practice_results = await db.execute(
        select(func.count(PracticeResult.id))
        .join(PracticeProblem, PracticeResult.problem_id == PracticeProblem.id)
        .where(
            PracticeResult.user_id == user_id,
            PracticeProblem.course_id == course_id,
        )
    )
    snapshot_count = await db.execute(
        select(func.count(MasterySnapshot.id)).where(
            MasterySnapshot.user_id == user_id,
            MasterySnapshot.course_id == course_id,
        )
    )
    practice_result_total = int(total_practice_results.scalar() or 0)
    snapshot_total = int(snapshot_count.scalar() or 0)

    return {
        "schema_version": SCHEMA_VERSION,
        "generated_at": _iso(now),
        "student_id": str(user_id),
        "course_id": str(course_id),
        "course_name": course.name,
        "assessment_summary": {
            "state": summary_state,
            "concepts_total": len(concept_entries),
            "concepts_assessed": len(assessed),
            "concepts_mastered": len(mastered),
            "overall_mastery": overall_mastery,
            "overall_mastery_basis": (
                "mean_of_assessed_concept_mastery" if assessed else "no_assessment_yet"
            ),
        },
        "concepts": concept_entries,
        "weak_concepts": weak,
        "review_due": review_due,
        "goals": goal_entries,
        "error_profile": error_profile,
        "activity": {
            "quiz_attempts": quiz_attempts,
            "quiz_correct": quiz_correct,
            "accuracy": round(quiz_correct / quiz_attempts, 4) if quiz_attempts else None,
            "time_spent_minutes": time_spent,
            "last_studied_at": _iso(last_studied),
            "gap_types": gap_types,
            "practice_results": practice_result_total,
        },
        "evidence": {
            "practice_results": practice_result_total,
            "wrong_answers": error_profile["total_wrong_answers"],
            "mastery_snapshots": snapshot_total,
            "sources": [
                "knowledge_nodes",
                "concept_mastery",
                "learning_progress",
                "practice_results",
                "wrong_answers",
                "study_goals",
                "mastery_snapshots(count)",
            ],
        },
        "warnings": warnings,
    }


def _snapshot_tag(band: str, run_tag: str | None) -> str:
    short = _SHORT_BAND.get(band, "unknown")
    tag = f"{SNAPSHOT_TAG_PREFIX}{short}"
    if run_tag:
        if not _RUN_TAG_RE.match(run_tag):
            raise ValueError("run_tag must match [a-z0-9_-]{1,12}")
        tag = f"{SNAPSHOT_TAG_PREFIX}{run_tag}:{short}"
    if len(tag) > 30:
        raise ValueError(f"snapshot tag too long for mastery_snapshots.gap_type: {tag}")
    return tag


async def write_state_snapshot(
    db: AsyncSession,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    *,
    run_tag: str | None = None,
    concepts: list[str] | None = None,
) -> dict[str, Any]:
    """Append concept-level snapshots to ``mastery_snapshots``.

    Append-only: no ``LearningProgress`` / ``ConceptMastery`` field is touched,
    and nothing is committed here — the caller decides.

    Concepts that are ``not_assessed`` are skipped on purpose: writing a 0.0 row
    would fabricate an assessment that never happened.
    """
    state = await build_learner_state(db, user_id, course_id)
    wanted = set(concepts) if concepts else None
    written: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for concept in state["concepts"]:
        name = concept["name"]
        if wanted is not None and concept["concept_id"] not in wanted and name not in wanted:
            continue
        if concept["state"] == STATE_NOT_ASSESSED:
            skipped.append({"concept_id": concept["concept_id"], "reason": "not_assessed"})
            continue
        if concept["content_node_id"] is None:
            skipped.append({"concept_id": concept["concept_id"], "reason": "no_content_node_id"})
            continue
        snapshot = MasterySnapshot(
            user_id=user_id,
            course_id=course_id,
            content_node_id=uuid.UUID(concept["content_node_id"]),
            mastery_score=float(concept["mastery_score"]),
            gap_type=_snapshot_tag(concept["state"], run_tag),
        )
        db.add(snapshot)
        written.append(
            {
                "concept_id": concept["concept_id"],
                "concept_name": name,
                "state": concept["state"],
                "mastery_score": concept["mastery_score"],
                "tag": snapshot.gap_type,
                "snapshot_id": str(snapshot.id),
            }
        )

    await db.flush()
    return {
        "written": written,
        "skipped": skipped,
        "run_tag": run_tag,
        "schema_version": SCHEMA_VERSION,
    }


async def list_state_snapshots(
    db: AsyncSession,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    *,
    concept_id: uuid.UUID | None = None,
    include_upstream: bool = False,
    limit: int = 100,
) -> dict[str, Any]:
    """Read back the snapshot time series, optionally for a single concept."""
    content_node_id: uuid.UUID | None = None
    concept_name: str | None = None
    if concept_id is not None:
        node = await db.get(KnowledgeNode, concept_id)
        if node is None:
            raise ValueError(f"concept not found: {concept_id}")
        concept_name = node.name
        if node.content_node_id is None:
            return {
                "concept_id": str(concept_id),
                "concept_name": concept_name,
                "points": [],
                "reason": "concept has no content_node_id; no snapshots can reference it",
            }
        content_node_id = node.content_node_id

    query = (
        select(MasterySnapshot)
        .where(
            MasterySnapshot.user_id == user_id,
            MasterySnapshot.course_id == course_id,
        )
        .order_by(MasterySnapshot.recorded_at)
        .limit(limit)
    )
    if content_node_id is not None:
        query = query.where(MasterySnapshot.content_node_id == content_node_id)
    if not include_upstream:
        query = query.where(MasterySnapshot.gap_type.like(f"{SNAPSHOT_TAG_PREFIX}%"))

    result = await db.execute(query)
    points = [
        {
            "snapshot_id": str(row.id),
            "content_node_id": str(row.content_node_id) if row.content_node_id else None,
            "mastery_score": round(float(row.mastery_score), 4),
            "tag": row.gap_type,
            "run_tag": _parse_run_tag(row.gap_type),
            "state": _parse_band(row.gap_type),
            "recorded_at": _iso(row.recorded_at),
        }
        for row in result.scalars().all()
    ]
    return {
        "concept_id": str(concept_id) if concept_id else None,
        "concept_name": concept_name,
        "content_node_id": str(content_node_id) if content_node_id else None,
        "include_upstream": include_upstream,
        "points": points,
    }


def _parse_run_tag(tag: str | None) -> str | None:
    if not tag or not tag.startswith(SNAPSHOT_TAG_PREFIX):
        return None
    body = tag[len(SNAPSHOT_TAG_PREFIX):]
    if ":" in body:
        return body.split(":", 1)[0]
    return None


def _parse_band(tag: str | None) -> str | None:
    if not tag or not tag.startswith(SNAPSHOT_TAG_PREFIX):
        return None
    body = tag[len(SNAPSHOT_TAG_PREFIX):]
    short = body.split(":", 1)[1] if ":" in body else body
    for state, value in _SHORT_BAND.items():
        if value == short:
            return state
    return None


async def assert_learner_state_readable(
    db: AsyncSession, user_id: uuid.UUID, course_id: uuid.UUID
) -> dict[str, Any]:
    """Lightweight self-check used by scripts/tests: build + verify invariants."""
    state = await build_learner_state(db, user_id, course_id)
    problems: list[str] = []
    if state["assessment_summary"]["concepts_assessed"] == 0:
        if state["assessment_summary"]["overall_mastery"] is not None:
            problems.append("overall_mastery must be null when nothing was assessed")
    for concept in state["concepts"]:
        if concept["state"] == STATE_NOT_ASSESSED and concept["mastery_score"] is not None:
            problems.append(
                f"not_assessed concept {concept['name']!r} exposes mastery_score="
                f"{concept['mastery_score']}"
            )
    return {"ok": not problems, "problems": problems, "state": state}
