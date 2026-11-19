"""Skill evolution: turn a reflection into a versioned teaching skill.

The chain this service implements, and nothing more:

    teaching_reflections (status=completed)
        -> improvement_proposal (structured fields only)
        -> existing-skill lookup (update vs compose vs new)
        -> new immutable version
        -> validation gate
        -> optionally enabled for the selector

Deliberate constraints
----------------------
* **No hardcoded strategies.** Content comes only from the reflection proposal;
  this module never invents steps or names a strategy in advance.
* **Idempotent.** Re-running on the same reflection returns the version it
  already produced instead of stacking V3, V4, ...
* **Failure never overwrites.** If validation fails, the new version is stored
  as ``draft`` (history kept) and the previous active version stays active and
  enabled.
* **Enablement is earned.** A version may only become callable when it validates
  *and* the reflection shows a real shortfall on the dimension it targets.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.teaching_evaluation import TeachingEvaluation
from models.teaching_reflection import (
    REFLECTION_STATUS_COMPLETED,
    TeachingReflection,
)
from models.teaching_skill import (
    ORIGIN_KIND_COMPOSE,
    ORIGIN_KIND_NEW,
    ORIGIN_KIND_REUSE,
    ORIGIN_REFLECTION,
    TeachingSkill,
    TeachingSkillVersion,
)
from services.evolution import skill_store
from services.evolution.skill_store import VersionPayload

logger = logging.getLogger(__name__)

# A reflection must show this much of a shortfall on the targeted dimension for
# its proposal to be auto-enabled. 2 = the top band, so < 2 means "not met".
ENABLEABLE_MAX_SCORE = 1


@dataclass
class EvolutionOutcome:
    status: str                      # created | version_added | duplicate | skipped
    reason: str
    skill: TeachingSkill | None = None
    version: TeachingSkillVersion | None = None
    validation: dict[str, Any] | None = None

    def as_json(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "skill": skill_store.serialize_skill(self.skill) if self.skill else None,
            "version": skill_store.serialize_version(self.version) if self.version else None,
            "validation": self.validation,
        }


def _dimension_score(evaluation: TeachingEvaluation, dimension: str) -> int | None:
    mapping = {
        "understanding": evaluation.understanding_score,
        "application": evaluation.application_score,
        "transfer": evaluation.transfer_score,
    }
    return mapping.get(dimension)


def _plan_to_verification(plan: dict[str, Any]) -> dict[str, Any]:
    """Carry the reflection's verification plan into the skill payload."""
    plan = plan or {}
    return {
        "metric": plan.get("metric") or "evaluation dimension score after use",
        "comparison": plan.get("comparison"),
        "success_criteria": list(plan.get("success_criteria") or []),
        "failure_criteria": list(plan.get("failure_criteria") or []),
        "uses_parallel_items": bool(plan.get("uses_parallel_items", True)),
        "next_run_procedure": list(plan.get("next_run_procedure") or []),
        "source": "reflection.verification_plan",
    }


def _pick_primary_skill(
    skills_by_slug: dict[str, TeachingSkill], reused: list[str]
) -> TeachingSkill | None:
    for slug in reused:
        skill = skills_by_slug.get(slug)
        if skill is not None:
            return skill
    return None


def _same_content(version: TeachingSkillVersion | None, payload: VersionPayload) -> bool:
    if version is None:
        return False
    return (
        list(version.steps or []) == payload.steps
        and list(version.trigger_conditions or []) == payload.trigger_conditions
        and list(version.target_dimensions or []) == payload.target_dimensions
    )


def _derive_slug(
    proposal: dict[str, Any], reused: list[str], target_dimension: str, forced: str | None
) -> str:
    """Readable, stable slug for a new/composite skill."""
    if forced:
        return skill_store.slugify(forced)
    named = proposal.get("new_skill_name")
    if named:
        return skill_store.slugify(str(named))
    if reused:
        parts = [re.sub(r"[^a-z0-9]+", "", item.lower()) for item in reused[:2]]
        parts = [part for part in parts if part]
        if parts:
            return skill_store.slugify(f"compose_{'_'.join(parts)}_{target_dimension}")
    return skill_store.slugify(str(proposal.get("summary") or "reflection skill"))


async def evolve_from_reflection(
    db: AsyncSession,
    *,
    reflection_id: uuid.UUID,
    user_id: uuid.UUID,
    course_id: uuid.UUID,
    enable_if_valid: bool = True,
    skill_slug: str | None = None,
) -> EvolutionOutcome:
    """Create/update a skill from a completed reflection's proposal."""
    reflection = await db.get(TeachingReflection, reflection_id)
    if reflection is None or reflection.user_id != user_id or reflection.course_id != course_id:
        raise ValueError("reflection_not_found")
    if reflection.status != REFLECTION_STATUS_COMPLETED:
        return EvolutionOutcome(
            status="skipped",
            reason=f"reflection status is {reflection.status}; only completed reflections "
            "can produce a skill version",
        )
    proposal = reflection.improvement_json or {}
    plan = reflection.verification_plan_json or {}
    if not proposal.get("steps"):
        return EvolutionOutcome(
            status="skipped", reason="reflection has no improvement proposal to apply"
        )

    # ── idempotency: this reflection already produced a version ──
    # Scoped per skill: the same reflection may legitimately seed both a new
    # composite skill and a version of an existing one, but it must never
    # produce two versions of the same skill.
    same_reflection_versions = list(
        (
            await db.execute(
                select(TeachingSkillVersion).where(
                    TeachingSkillVersion.source_reflection_id == reflection.id
                )
            )
        ).scalars().all()
    )
    forced_skill = (
        await skill_store.get_skill(db, skill_slug) if skill_slug else None
    )
    if forced_skill is None and skill_slug:
        raise ValueError(f"skill_not_found: {skill_slug}")
    existing_for_target = next(
        (
            version for version in same_reflection_versions
            if forced_skill is not None and version.skill_id == forced_skill.id
        ),
        None,
    )
    if existing_for_target is None and forced_skill is None and same_reflection_versions:
        existing_for_target = same_reflection_versions[0]
    if existing_for_target is not None:
        skill = await db.get(TeachingSkill, existing_for_target.skill_id)
        return EvolutionOutcome(
            status="duplicate",
            reason="this reflection already produced a version for this skill",
            skill=skill,
            version=existing_for_target,
            validation=existing_for_target.validation_json,
        )

    target_dimension = proposal.get("target_dimension") or "multiple"
    dimensions = [target_dimension] if target_dimension != "multiple" else ["multiple"]
    reused = [str(item) for item in (proposal.get("reuses_skills") or [])]
    origin_kind = proposal.get("strategy_origin") or "compose_existing"
    if origin_kind not in {ORIGIN_KIND_REUSE, ORIGIN_KIND_COMPOSE, ORIGIN_KIND_NEW}:
        origin_kind = ORIGIN_KIND_COMPOSE

    payload = VersionPayload(
        trigger_conditions=[str(item) for item in (proposal.get("trigger_conditions") or [])],
        applicable_objectives=[reflection.objective_slug] if reflection.objective_slug else [],
        target_dimensions=dimensions,
        steps=[str(item) for item in (proposal.get("steps") or [])],
        expected_effect=str(proposal.get("expected_effect") or proposal.get("summary") or ""),
        verification=_plan_to_verification(plan),
        reused_skills=reused,
        origin_kind=origin_kind,
        change_summary=(
            f"{proposal.get('summary', '')} (from reflection {reflection.id})"
        )[:1000],
    )

    # ── decide: update an existing skill, or create a new (composite) one ──
    all_skills = await skill_store.list_skills(db, limit=500)
    skills_by_slug = {skill.slug: skill for skill in all_skills}
    primary = _pick_primary_skill(skills_by_slug, reused)
    if forced_skill is not None:
        # The caller explicitly named the skill to update.
        primary = forced_skill

    evaluation = (
        await db.get(TeachingEvaluation, reflection.evaluation_id)
        if reflection.evaluation_id else None
    )
    score = _dimension_score(evaluation, target_dimension) if evaluation else None
    real_shortfall = score is not None and score <= ENABLEABLE_MAX_SCORE

    # Frozen provenance so the version can show its origin even if the reflection
    # row is later removed (SET NULL only breaks the FK link, not this snapshot).
    provenance_json = {
        "reflection_id": str(reflection.id),
        "evaluation_id": str(reflection.evaluation_id) if reflection.evaluation_id else None,
        "run_tag": reflection.run_tag,
        "problem": {
            "statement": reflection.problem_statement,
            "severity": reflection.problem_severity,
            "dimension": reflection.problem_dimension,
        },
        "proposal": proposal,
        "input_hash": reflection.input_hash,
        "objective_slug": reflection.objective_slug,
        "captured_at": str(reflection.created_at),
    }

    validation_probe = skill_store.validate_version_payload(
        payload, set(skills_by_slug) | ({skill_slug} if skill_slug else set())
    )
    should_enable = bool(enable_if_valid and validation_probe.ok and real_shortfall)

    # Composite: several existing skills combined and no primary chosen by the
    # reflection → new skill identity, lineage recorded in reused_skills.
    make_new_skill = (
        forced_skill is None
        and (
            primary is None
            or origin_kind == ORIGIN_KIND_NEW
            or (origin_kind == ORIGIN_KIND_COMPOSE and len(reused) >= 2)
        )
    )

    if make_new_skill:
        name = (
            proposal.get("new_skill_name")
            or f"复合教学策略（{target_dimension}）"
        )
        slug = _derive_slug(proposal, reused, target_dimension, skill_slug)
        clash = skills_by_slug.get(slug)
        if clash is not None:
            # Same slug already exists: append a version instead of failing.
            version, validation = await skill_store.add_version(
                db,
                skill=clash,
                payload=payload,
                origin=ORIGIN_REFLECTION,
                source_reflection_id=reflection.id,
                source_evaluation_id=reflection.evaluation_id,
                created_by="reflection",
                enable=should_enable,
                provenance_json=provenance_json,
            )
            return EvolutionOutcome(
                status="version_added",
                reason=f"slug {slug} already existed; appended V{version.version}",
                skill=clash,
                version=version,
                validation=validation.as_json(),
            )
        skill, version, validation = await skill_store.create_skill_with_version(
            db,
            slug=slug,
            name=str(name)[:200],
            description=(proposal.get("new_skill_rationale") or proposal.get("summary") or "")[:2000],
            payload=payload,
            origin=ORIGIN_REFLECTION,
            source_reflection_id=reflection.id,
            source_evaluation_id=reflection.evaluation_id,
            created_by="reflection",
            enable=should_enable,
            provenance_json=provenance_json,
        )
        return EvolutionOutcome(
            status="created",
            reason=(
                "new skill created from reflection"
                + (" and enabled" if should_enable else " (stored unenabled)")
            ),
            skill=skill,
            version=version,
            validation=validation.as_json(),
        )

    # Update the primary skill with a new version.
    current = await skill_store.get_version(db, primary.id, primary.current_version)
    if _same_content(current, payload):
        return EvolutionOutcome(
            status="skipped",
            reason="proposal content is identical to the current version; no new version created",
            skill=primary,
            version=current,
            validation=(current.validation_json if current else None),
        )

    version, validation = await skill_store.add_version(
        db,
        skill=primary,
        payload=payload,
        origin=ORIGIN_REFLECTION,
        source_reflection_id=reflection.id,
        source_evaluation_id=reflection.evaluation_id,
        created_by="reflection",
        enable=should_enable and primary.enabled,
        provenance_json=provenance_json,
    )
    return EvolutionOutcome(
        status="version_added",
        reason=(
            f"appended V{version.version} to {primary.slug}"
            + (" and enabled" if version.status == "active" else " (stored unenabled)")
        ),
        skill=primary,
        version=version,
        validation=validation.as_json(),
    )
