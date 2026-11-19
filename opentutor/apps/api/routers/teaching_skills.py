"""METAMIND Teaching endpoints — module 4: Skill Memory + Strategy Selector.

Kept in its own router (same ``/api/teaching`` prefix) so module 1-3 endpoints
stay untouched. Nothing here injects into the Tutor prompt; module 5 does that
and will call :func:`mark_invocation_injected` when it happens.
"""

from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from libs.exceptions import NotFoundError, ValidationError
from models.user import User
from schemas.teaching import (
    EvolutionResponse,
    EvolveFromReflectionRequest,
    InvocationEvaluateRequest,
    InvocationInjectRequest,
    InvocationListResponse,
    InvocationView,
    SkillDetailResponse,
    SkillImportResponse,
    SkillListResponse,
    SkillMutationResponse,
    SkillRollbackRequest,
    SkillSelectionRequest,
    SkillSelectionResponse,
    SkillToggleRequest,
    VersionDiffResponse,
    VersionListResponse,
)
from services.auth.dependency import get_current_user
from services.course_access import get_course_or_404
from services.evolution import selector as selector_svc
from services.evolution import skill_evolution as evolution_svc
from services.evolution import skill_store

logger = logging.getLogger(__name__)

router = APIRouter()


def _skill_or_404(skill):
    if skill is None:
        raise NotFoundError("TeachingSkill", "unknown")
    return skill


# ── Skill library ──────────────────────────────────────────────────────────


@router.post(
    "/skills/import",
    response_model=SkillImportResponse,
    summary="Import the shipped markdown skills (idempotent)",
    description=(
        "Imports apps/api/skills/*.md as seed skills keyed by slug. Re-running never "
        "duplicates rows and never overwrites a version produced by reflection."
    ),
)
async def import_skills(
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    report = await skill_store.import_markdown_skills(db)
    await db.commit()
    return report.as_json()


@router.get("/skills", response_model=SkillListResponse, summary="List teaching skills")
async def list_skills(
    enabled_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    skills = await skill_store.list_skills(db, enabled_only=enabled_only, limit=limit)
    return {"count": len(skills), "skills": [skill_store.serialize_skill(s) for s in skills]}


@router.post(
    "/skills/from-reflection",
    response_model=EvolutionResponse,
    summary="Create or update a skill from a completed reflection",
    description=(
        "Reads the reflection's improvement_proposal, decides between updating an existing "
        "skill (new immutable version) and creating a composite/new skill, validates the "
        "structured payload, and stores it. A version is only enabled when it validates AND "
        "the reflection shows a real shortfall on its target dimension."
    ),
)
async def evolve_skill_from_reflection(
    body: EvolveFromReflectionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_course_or_404(db, body.course_id, user_id=user.id)
    try:
        outcome = await evolution_svc.evolve_from_reflection(
            db,
            reflection_id=body.reflection_id,
            user_id=user.id,
            course_id=body.course_id,
            enable_if_valid=body.enable_if_valid,
            skill_slug=body.skill_slug,
        )
    except ValueError as exc:
        if str(exc) == "reflection_not_found":
            raise NotFoundError("Reflection", body.reflection_id) from exc
        raise ValidationError(str(exc)) from exc
    await db.commit()
    return outcome.as_json()


@router.get(
    "/skills/{skill_ref}",
    response_model=SkillDetailResponse,
    summary="Get one skill with its current version",
)
async def get_skill_detail(
    skill_ref: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    skill = _skill_or_404(await skill_store.get_skill(db, skill_ref))
    version = await skill_store.get_version(db, skill.id, skill.current_version)
    stats = await skill_store.invocation_stats(db, skill.id)
    return {
        "skill": skill_store.serialize_skill(skill),
        "current_version_detail": skill_store.serialize_version(version) if version else None,
        "history_stats": stats,
    }


@router.get(
    "/skills/{skill_ref}/versions",
    response_model=VersionListResponse,
    summary="List all versions (V1..Vn) of a skill",
)
async def list_skill_versions(
    skill_ref: str,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    skill = _skill_or_404(await skill_store.get_skill(db, skill_ref))
    versions = await skill_store.list_versions(db, skill.id)
    return {
        "skill_id": str(skill.id),
        "slug": skill.slug,
        "current_version": skill.current_version,
        "count": len(versions),
        "versions": [skill_store.serialize_version(v) for v in versions],
    }


@router.get(
    "/skills/{skill_ref}/diff",
    response_model=VersionDiffResponse,
    summary="Diff two versions of a skill",
)
async def diff_skill_versions(
    skill_ref: str,
    to_version: int = Query(..., ge=1),
    from_version: int | None = Query(default=None, ge=1),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    skill = _skill_or_404(await skill_store.get_skill(db, skill_ref))
    new = await skill_store.get_version(db, skill.id, to_version)
    if new is None:
        raise NotFoundError("SkillVersion", to_version)
    old = None
    if from_version is not None:
        old = await skill_store.get_version(db, skill.id, from_version)
        if old is None:
            raise NotFoundError("SkillVersion", from_version)
    elif to_version > 1:
        old = await skill_store.get_version(db, skill.id, to_version - 1)
    diff = skill_store.version_diff(old, new)
    return {"skill_id": str(skill.id), "slug": skill.slug, **diff}


@router.post(
    "/skills/{skill_ref}/toggle",
    response_model=SkillMutationResponse,
    summary="Enable or disable a skill",
    description=(
        "Enabling re-validates the current version. An invalid strategy cannot be enabled: "
        "the call returns ok=false in validation and the skill stays disabled."
    ),
)
async def toggle_skill(
    skill_ref: str,
    body: SkillToggleRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    skill = _skill_or_404(await skill_store.get_skill(db, skill_ref))
    skill, validation = await skill_store.set_enabled(db, skill, body.enabled)
    await db.commit()
    await db.refresh(skill)
    return {
        "skill": skill_store.serialize_skill(skill),
        "validation": validation.as_json(),
        "action": "enabled" if skill.enabled else "disabled",
    }


@router.post(
    "/skills/{skill_ref}/rollback",
    response_model=SkillMutationResponse,
    summary="Roll a skill back to an earlier version",
)
async def rollback_skill(
    skill_ref: str,
    body: SkillRollbackRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    skill = _skill_or_404(await skill_store.get_skill(db, skill_ref))
    try:
        skill, validation = await skill_store.rollback_to(db, skill, body.target_version)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    await db.commit()
    await db.refresh(skill)
    return {
        "skill": skill_store.serialize_skill(skill),
        "validation": validation.as_json(),
        "action": f"rolled_back_to_v{skill.current_version}",
    }


# ── Selector + invocation tracking ─────────────────────────────────────────


@router.post(
    "/selector/select",
    response_model=SkillSelectionResponse,
    summary="Select a teaching skill for the current situation",
    description=(
        "Scores enabled skill versions against the learner's current weakness, error type, "
        "scene and history. Returns selected=null (fallback_to_native_tutor=true) when nothing "
        "really fits. With persist=true a skill_invocations row is written as 'selected'."
    ),
)
async def select_skill(
    body: SkillSelectionRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_course_or_404(db, body.course_id, user_id=user.id)
    ctx = selector_svc.SelectionContext(
        user_id=user.id,
        course_id=body.course_id,
        objective_slug=body.objective_slug,
        error_type=body.error_type,
        scene=body.scene,
        message=body.message,
        weakness_dimension=body.weakness_dimension,
        session_id=body.session_id,
        run_tag=body.run_tag,
        persist=body.persist,
    )
    result = await selector_svc.select_skill(db, ctx)
    if body.persist:
        await db.commit()
    return result.as_json()


@router.get(
    "/invocations",
    response_model=InvocationListResponse,
    summary="List skill invocations",
)
async def list_invocations(
    course_id: uuid.UUID = Query(...),
    session_id: str | None = Query(default=None),
    skill_id: uuid.UUID | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    await get_course_or_404(db, course_id, user_id=user.id)
    rows = await selector_svc.list_invocations(
        db, user.id, course_id, session_id=session_id, skill_id=skill_id, limit=limit
    )
    return {
        "course_id": str(course_id),
        "count": len(rows),
        "invocations": [selector_svc.serialize_invocation(row) for row in rows],
    }


@router.post(
    "/invocations/{invocation_id}/inject",
    response_model=InvocationView,
    summary="Mark the selected skill as actually injected into the Tutor prompt",
    description=(
        "Module 5 hook. Selection alone is not injection: this records that the skill text "
        "really entered the prompt."
    ),
)
async def mark_invocation_injected(
    invocation_id: uuid.UUID,
    body: InvocationInjectRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await selector_svc.mark_injected(
            db, invocation_id, prompt=body.prompt, marker=body.marker
        )
    except ValueError as exc:
        if str(exc) == "invocation_not_found":
            raise NotFoundError("SkillInvocation", invocation_id) from exc
        raise ValidationError(str(exc)) from exc
    if row.user_id != user.id:
        raise NotFoundError("SkillInvocation", invocation_id)
    await db.commit()
    await db.refresh(row)
    return selector_svc.serialize_invocation(row)


@router.post(
    "/invocations/{invocation_id}/evaluate",
    response_model=InvocationView,
    summary="Attach the evaluation that judged this skill invocation",
    description=(
        "Links the evaluation to the exact skill version. The invocation only becomes "
        "'evaluated' when the evaluation carries a score for the skill's target dimension; "
        "an unscored episode stays 'injected' because it is not evidence."
    ),
)
async def attach_invocation_evaluation(
    invocation_id: uuid.UUID,
    body: InvocationEvaluateRequest,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    try:
        row = await selector_svc.attach_evaluation(
            db, invocation_id, body.evaluation_id, target_dimension=body.target_dimension
        )
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    if row.user_id != user.id:
        raise NotFoundError("SkillInvocation", invocation_id)
    await db.commit()
    await db.refresh(row)
    return selector_svc.serialize_invocation(row)
