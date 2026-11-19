"""METAMIND Teaching endpoints — module 1: Learner Model, module 2: Evaluator.

Nothing here changes how OpenTutor teaches; the Tutor is untouched.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from models.teaching_evaluation import TeachingEvaluation
from models.user import User
from schemas.teaching import (
    EvaluationListResponse,
    EvaluationRequest,
    EvaluationResponse,
    LearnerStateResponse,
    ReflectionListResponse,
    ReflectionRequest,
    ReflectionResponse,
    RubricResponse,
    SnapshotHistoryResponse,
    SnapshotWriteRequest,
    SnapshotWriteResponse,
)
from services.auth.dependency import get_current_user
from services.course_access import get_course_or_404
from services.evolution import eval_rubric as rubric_def
from services.evolution import evaluator as evaluator_svc
from services.evolution import learner_state as ls
from services.evolution import reflection_engine as reflection_svc

logger = logging.getLogger(__name__)

router = APIRouter()


# ── Module 1: Learner Model ────────────────────────────────────────────────


@router.get(
    "/learner-state",
    response_model=LearnerStateResponse,
    summary="Get aggregated learner state",
    description=(
        "Aggregate the learner's real ConceptMastery / LearningProgress / practice "
        "evidence for one course. Concepts with no practice history are reported as "
        "'not_assessed' with a null mastery score. Read-only."
    ),
)
async def get_learner_state(
    course_id: uuid.UUID = Query(..., description="Course to aggregate"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    course = await get_course_or_404(db, course_id, user_id=user.id)
    return await ls.build_learner_state(db, user.id, course_id, course=course)


@router.post(
    "/learner-state/snapshot",
    response_model=SnapshotWriteResponse,
    summary="Write a concept-level mastery snapshot",
    description=(
        "Append-only snapshot of the current concept states into mastery_snapshots, "
        "tagged 'mm:<run_tag>:<band>'. Never mutates LearningProgress or BKT state."
    ),
)
async def create_learner_state_snapshot(
    body: SnapshotWriteRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_course_or_404(db, body.course_id, user_id=user.id)
    result = await ls.write_state_snapshot(
        db,
        user.id,
        body.course_id,
        run_tag=body.run_tag,
        concepts=[str(cid) for cid in body.concept_ids] if body.concept_ids else None,
    )
    await db.commit()
    return result


@router.get(
    "/learner-state/history",
    response_model=SnapshotHistoryResponse,
    summary="Read the concept mastery snapshot series",
    description=(
        "Time series from mastery_snapshots. Default returns only METAMIND-written "
        "points ('mm:' tag); pass include_upstream=true to also see tracker snapshots."
    ),
)
async def get_learner_state_history(
    course_id: uuid.UUID = Query(...),
    concept_id: uuid.UUID | None = Query(
        default=None, description="Restrict to one concept (knowledge_nodes.id)"
    ),
    include_upstream: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_course_or_404(db, course_id, user_id=user.id)
    return await ls.list_state_snapshots(
        db,
        user.id,
        course_id,
        concept_id=concept_id,
        include_upstream=include_upstream,
        limit=limit,
    )


# ── Module 2: Evaluator ────────────────────────────────────────────────────


@router.get(
    "/rubric",
    response_model=RubricResponse,
    summary="Get the fixed evaluation rubric",
    description="Machine-readable rubric (3 dimensions x 0-2) plus the verdict rules.",
)
async def get_rubric():
    return rubric_def.rubric_document()


@router.post(
    "/evaluate",
    response_model=EvaluationResponse,
    summary="Evaluate whether a learning objective was met",
    description=(
        "Scores three dimensions from real evidence: objective items are graded "
        "deterministically by the existing grading code; open answers are scored "
        "against the fixed rubric and every score >= 1 must quote the student "
        "verbatim. Unmeasured dimensions are null, never 0. Retention is not "
        "measured here. Failures are persisted with status='failed'."
    ),
)
async def evaluate(
    body: EvaluationRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_course_or_404(db, body.course_id, user_id=user.id)
    row = await evaluator_svc.evaluate_objective(
        db,
        user_id=user.id,
        course_id=body.course_id,
        session_id=body.session_id,
        run_tag=body.run_tag,
        objective_slug=body.objective_slug,
        objective_id=body.objective_id,
        strategy_version=body.strategy_version,
        problem_ids=body.problem_ids,
        open_answer=body.open_answer,
        open_question=body.open_question,
        transfer_case=body.transfer_case,
        transfer_answer=body.transfer_answer,
        requested_rubric_version=body.rubric_version,
    )
    await db.commit()
    await db.refresh(row)
    return evaluator_svc.serialize_evaluation(row)


@router.get(
    "/evaluations",
    response_model=EvaluationListResponse,
    summary="List stored evaluations",
    description="Newest first. Filter by objective and/or session (run label).",
)
async def list_evaluations(
    course_id: uuid.UUID = Query(...),
    objective_slug: str | None = Query(default=None),
    session_id: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_course_or_404(db, course_id, user_id=user.id)
    query = (
        select(TeachingEvaluation)
        .where(
            TeachingEvaluation.user_id == user.id,
            TeachingEvaluation.course_id == course_id,
        )
        .order_by(desc(TeachingEvaluation.created_at))
        .limit(limit)
    )
    if objective_slug:
        query = query.where(TeachingEvaluation.objective_slug == objective_slug)
    if session_id:
        query = query.where(TeachingEvaluation.session_id == session_id)
    rows = (await db.execute(query)).scalars().all()
    return {
        "course_id": str(course_id),
        "count": len(rows),
        "evaluations": [evaluator_svc.serialize_evaluation(row) for row in rows],
    }


@router.get(
    "/evaluations/{evaluation_id}",
    response_model=EvaluationResponse,
    summary="Get one evaluation",
)
async def get_evaluation(
    evaluation_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from libs.exceptions import NotFoundError

    row = await db.get(TeachingEvaluation, evaluation_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("Evaluation", evaluation_id)
    return evaluator_svc.serialize_evaluation(row)


# ── Module 3: Reflection Engine ────────────────────────────────────────────


@router.post(
    "/reflect",
    response_model=ReflectionResponse,
    summary="Diagnose one evaluation and propose a verifiable teaching improvement",
    description=(
        "Builds an evidence package from the stored evaluation + learner state + the "
        "student's real answers + the existing teaching-skill library, then asks the "
        "model for a five-part diagnosis. Every citation must match a real id and be "
        "verbatim; unverified causes/proposals are rejected; achieved objectives yield "
        "'no_improvement_needed' instead of an invented failure. Nothing is applied: no "
        "skill is written or enabled and the Tutor prompt is untouched."
    ),
)
async def reflect(
    body: ReflectionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_course_or_404(db, body.course_id, user_id=user.id)
    try:
        row = await reflection_svc.reflect_on_evaluation(
            db,
            user_id=user.id,
            course_id=body.course_id,
            evaluation_id=body.evaluation_id,
            subjective_feedback=body.subjective_feedback,
        )
    except ValueError as exc:
        if str(exc) == reflection_svc.ERROR_EVALUATION_NOT_FOUND:
            from libs.exceptions import NotFoundError

            raise NotFoundError("Evaluation", body.evaluation_id) from exc
        raise
    await db.commit()
    await db.refresh(row)
    return reflection_svc.serialize_reflection(row)


@router.get(
    "/reflections",
    response_model=ReflectionListResponse,
    summary="List stored reflections",
    description="Newest first, optionally filtered by evaluation or objective.",
)
async def list_reflections(
    course_id: uuid.UUID = Query(...),
    evaluation_id: uuid.UUID | None = Query(default=None),
    objective_slug: str | None = Query(default=None),
    limit: int = Query(default=20, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from models.teaching_reflection import TeachingReflection

    await get_course_or_404(db, course_id, user_id=user.id)
    query = (
        select(TeachingReflection)
        .where(
            TeachingReflection.user_id == user.id,
            TeachingReflection.course_id == course_id,
        )
        .order_by(desc(TeachingReflection.created_at))
        .limit(limit)
    )
    if evaluation_id:
        query = query.where(TeachingReflection.evaluation_id == evaluation_id)
    if objective_slug:
        query = query.where(TeachingReflection.objective_slug == objective_slug)
    rows = (await db.execute(query)).scalars().all()
    return {
        "course_id": str(course_id),
        "count": len(rows),
        "reflections": [reflection_svc.serialize_reflection(row) for row in rows],
    }


@router.get(
    "/reflections/{reflection_id}",
    response_model=ReflectionResponse,
    summary="Get one reflection",
)
async def get_reflection(
    reflection_id: uuid.UUID,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    from libs.exceptions import NotFoundError
    from models.teaching_reflection import TeachingReflection

    row = await db.get(TeachingReflection, reflection_id)
    if row is None or row.user_id != user.id:
        raise NotFoundError("Reflection", reflection_id)
    return reflection_svc.serialize_reflection(row)
