"""Skill Memory service (METAMIND module 4).

The database is the single source of truth for teaching skills. The shipped
markdown files in ``apps/api/skills/*.md`` are *seeds*: they are imported once
(idempotently, by slug) and afterwards the DB row wins. That is what makes a
version survive a restart and a re-import.

Design rules
------------
* **Identity vs content.** ``TeachingSkill`` is the identity (slug, origin,
  enable switch); ``TeachingSkillVersion`` holds the content and is never
  mutated. Rolling back moves the pointer, it does not edit history.
* **Validation before enablement.** A version can only be enabled after it
  passes :func:`validate_version_payload`. Invalid versions stay ``draft``.
* **No prompt overrides, no code.** Steps are structured strings. Payloads
  containing instruction-override markers are rejected outright.
* **Reflection shapes the content, but only through fixed fields.**
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import AsyncSession

from models.teaching_skill import (
    ALLOWED_DIMENSIONS,
    ORIGIN_MANUAL,
    ORIGIN_REFLECTION,
    ORIGIN_SEED,
    VERSION_ACTIVE,
    VERSION_DRAFT,
    VERSION_ROLLED_BACK,
    VERSION_SUPERSEDED,
    VERSION_VALIDATED,
    SkillInvocation,
    TeachingSkill,
    TeachingSkillVersion,
)

logger = logging.getLogger(__name__)

SKILLS_DIR = Path(__file__).resolve().parents[2] / "skills"

# Deterministic seed ids so re-running the import never duplicates rows.
SEED_NAMESPACE = uuid.UUID("2b7e5c11-9a4d-5f8e-b6c3-7d1a0e9f4c52")

MIN_STEP_CHARS = 6
MAX_STEPS = 12
MAX_STEP_CHARS = 500
MAX_TRIGGERS = 12

# Instruction-override / code-execution markers. A teaching strategy has no
# business containing these; refusing them keeps model-generated skill text from
# ever acting like a system prompt or an executable payload.
_FORBIDDEN_PATTERNS = (
    "ignore previous instructions",
    "ignore all previous",
    "disregard the system",
    "system prompt",
    "you are now",
    "```",
    "rm -rf",
    "subprocess",
    "os.system",
    "eval(",
    "exec(",
    "<script",
)
_VAGUE_PATTERNS = (
    "更努力", "多练习", "多做题", "讲清楚一点", "多举例子", "try harder", "be clearer",
)

# Localisation of the shipped skills' English triggers. The markdown files were
# written for English conversations; the product UI and students are Chinese, so
# the seed import adds equivalent Chinese terms. This is seed translation, not
# generated strategy content: it never adds steps or changes pedagogy, and it is
# only applied to seed V1 (never to a version produced by reflection).
SEED_TRIGGER_LOCALIZATION: dict[str, list[str]] = {
    "socratic_questioning": ["为什么", "解释", "理解", "概念", "原理", "什么是", "怎么", "区分"],
    "worked_examples": ["例子", "示例", "示范", "步骤", "怎么做", "演示"],
    "analogies": ["类比", "比喻", "通俗", "直觉", "生活"],
    "error_analysis": ["错", "错误", "做错", "错题", "混淆", "搞混", "不对", "失败"],
    "spaced_retrieval": ["复习", "记住", "忘记", "测验", "回忆", "巩固"],
}


def seed_skill_id(slug: str) -> uuid.UUID:
    return uuid.uuid5(SEED_NAMESPACE, f"skill:{slug}")


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _hash(payload: Any) -> str:
    blob = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def slugify(name: str) -> str:
    """Stable, prompt-safe slug for a model-proposed skill name."""
    cleaned = re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "_", (name or "").strip().lower())
    cleaned = cleaned.strip("_")
    if not cleaned:
        cleaned = "skill"
    if not cleaned.isascii():
        # Keep CJK names readable but bounded: hash suffix guarantees uniqueness.
        cleaned = f"skill_{hashlib.sha256(name.encode('utf-8')).hexdigest()[:10]}"
    return cleaned[:70]


@dataclass
class VersionPayload:
    trigger_conditions: list[str] = field(default_factory=list)
    applicable_objectives: list[str] = field(default_factory=list)
    target_dimensions: list[str] = field(default_factory=list)
    steps: list[str] = field(default_factory=list)
    expected_effect: str = ""
    verification: dict[str, Any] = field(default_factory=dict)
    reused_skills: list[str] = field(default_factory=list)
    origin_kind: str = "compose_existing"
    change_summary: str = ""

    def to_json(self) -> dict[str, Any]:
        return {
            "trigger_conditions": self.trigger_conditions,
            "applicable_objectives": self.applicable_objectives,
            "target_dimensions": self.target_dimensions,
            "steps": self.steps,
            "expected_effect": self.expected_effect,
            "verification": self.verification,
            "reused_skills": self.reused_skills,
            "origin_kind": self.origin_kind,
            "change_summary": self.change_summary,
        }


@dataclass
class ValidationResult:
    ok: bool
    problems: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {"ok": self.ok, "problems": self.problems}


def _strings(value: Any, *, limit: int = MAX_TRIGGERS) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [str(item).strip() for item in value if str(item).strip()][:limit]


def validate_version_payload(payload: VersionPayload, known_skill_slugs: set[str]) -> ValidationResult:
    """Business rules a version must satisfy before it may be enabled."""
    problems: list[str] = []

    triggers = _strings(payload.trigger_conditions)
    if not triggers:
        problems.append("trigger_conditions must not be empty")
    for trigger in triggers:
        if len(trigger) < 2:
            problems.append(f"trigger condition too short: {trigger!r}")

    dims = _strings(payload.target_dimensions)
    if not dims:
        problems.append("target_dimensions must not be empty")
    unknown_dims = [d for d in dims if d not in ALLOWED_DIMENSIONS]
    if unknown_dims:
        problems.append(f"unknown target dimensions: {unknown_dims}")

    steps = _strings(payload.steps, limit=MAX_STEPS)
    if len(steps) < 2:
        problems.append(f"need at least 2 steps, got {len(steps)}")
    for step in steps:
        if len(step) < MIN_STEP_CHARS:
            problems.append(f"step too short to execute: {step!r}")
        if len(step) > MAX_STEP_CHARS:
            problems.append("step longer than 500 characters")
        if any(word in step for word in _VAGUE_PATTERNS):
            problems.append(f"step is generic filler: {step!r}")

    if not (payload.expected_effect or "").strip():
        problems.append("expected_effect must not be empty")
    elif len(payload.expected_effect.strip()) < MIN_STEP_CHARS:
        problems.append("expected_effect is too short to be checkable")

    verification = payload.verification or {}
    if not isinstance(verification, dict) or not verification.get("metric"):
        problems.append("verification.metric is required")
    if not _strings(verification.get("success_criteria")):
        problems.append("verification.success_criteria is required")

    reused = _strings(payload.reused_skills)
    missing = [slug for slug in reused if slug not in known_skill_slugs]
    if missing:
        problems.append(f"reused skills do not exist: {missing}")
    if not reused and payload.origin_kind != "new_skill":
        problems.append("a non-new skill must reference at least one existing skill")

    blob = json.dumps(payload.to_json(), ensure_ascii=False).lower()
    for pattern in _FORBIDDEN_PATTERNS:
        if pattern in blob:
            problems.append(f"payload contains forbidden content: {pattern!r}")

    return ValidationResult(ok=not problems, problems=problems)


# ── seed import ────────────────────────────────────────────────────────────


@dataclass
class ImportReport:
    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    unchanged: list[str] = field(default_factory=list)

    def as_json(self) -> dict[str, Any]:
        return {
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "total": len(self.created) + len(self.updated) + len(self.unchanged),
        }


async def import_markdown_skills(db: AsyncSession) -> ImportReport:
    """Idempotently import ``apps/api/skills/*.md`` as seed skills (V1)."""
    report = ImportReport()
    if not SKILLS_DIR.is_dir():
        logger.warning("skills directory not found: %s", SKILLS_DIR)
        return report

    import yaml

    for path in sorted(SKILLS_DIR.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            logger.exception("cannot read skill file %s", path)
            continue
        match = re.match(r"^---\n(.*?)\n---\n(.*)", text, re.DOTALL)
        if not match:
            logger.warning("skill file without frontmatter: %s", path.name)
            continue
        meta = yaml.safe_load(match.group(1)) or {}
        body = match.group(2).strip()
        slug = str(meta.get("name") or path.stem)
        triggers = _strings(meta.get("triggers"))
        for localized in SEED_TRIGGER_LOCALIZATION.get(slug, []):
            if localized not in triggers:
                triggers.append(localized)
        triggers = triggers[:MAX_TRIGGERS + len(SEED_TRIGGER_LOCALIZATION.get(slug, []))]
        # Markdown bodies are prose; the first three numbered lines become steps
        # so the selector has something structural to reason about.
        steps = [
            re.sub(r"^\d+\.\s*", "", line).strip(" -")
            for line in body.splitlines()
            if re.match(r"^\d+\.\s+\S", line.strip())
        ][:MAX_STEPS]

        payload = VersionPayload(
            trigger_conditions=triggers or [slug.replace("_", " ")],
            applicable_objectives=[],
            target_dimensions=["multiple"],
            steps=steps or [body[:MAX_STEP_CHARS]] if body else [],
            expected_effect=f"Improve learner outcomes using {slug.replace('_', ' ')}",
            verification={"metric": "evaluation dimension scores after use",
                          "success_criteria": ["target dimension improves vs baseline"]},
            reused_skills=[],
            origin_kind="new_skill",
            change_summary="imported from shipped markdown skill",
        )
        payload.reused_skills = []
        skill_id = seed_skill_id(slug)

        existing = await _get_skill_by_slug(db, slug)
        if existing is None:
            skill = TeachingSkill(
                id=skill_id,
                slug=slug,
                name=str(meta.get("name") or slug).replace("_", " ").title(),
                description=(body.splitlines()[0] if body else "")[:500],
                origin=ORIGIN_SEED,
                enabled=True,
                current_version=1,
                version_count=1,
                seed_path=str(path.relative_to(SKILLS_DIR.parents[2])),
                metadata_json={"priority": meta.get("priority", 5),
                               "scenes": _strings(meta.get("scenes")),
                               "imported_from": "markdown"},
            )
            db.add(skill)
            await db.flush()
            # Defensive: a leftover version row for the deterministic seed id can
            # exist if the skill row was removed outside the app (FK enforcement
            # is per-connection in SQLite). Refresh it instead of duplicating.
            version = await get_version(db, skill.id, 1)
            if version is None:
                version = TeachingSkillVersion(skill_id=skill.id, version=1)
                db.add(version)
            version.status = VERSION_ACTIVE
            version.trigger_conditions = payload.trigger_conditions
            version.applicable_objectives = payload.applicable_objectives
            version.target_dimensions = payload.target_dimensions
            version.steps = payload.steps
            version.expected_effect = payload.expected_effect
            version.verification = payload.verification
            version.reused_skills = []
            version.origin_kind = "new_skill"
            version.change_summary = payload.change_summary
            version.created_by = "seed_import"
            version.content_hash = _hash(payload.to_json())
            version.validation_json = validate_version_payload(payload, {slug}).as_json()
            await db.flush()
            report.created.append(slug)
            continue

        # Existing skill: only refresh the seed metadata, never overwrite a
        # version that the Evolution Engine produced.
        existing.seed_path = str(path.relative_to(SKILLS_DIR.parents[2]))
        existing.updated_at = _now()
        existing_meta = dict(existing.metadata_json or {})
        expected = {
            "priority": meta.get("priority", 5),
            "scenes": _strings(meta.get("scenes")),
            "imported_from": "markdown",
        }
        seed_drift = False
        # Only a still-pristine seed version may be refreshed in place. Once a
        # reflection has added V2+, history is immutable.
        seed_version = await get_version(db, existing.id, 1)
        if (
            existing.origin == ORIGIN_SEED
            and existing.version_count == 1
            and seed_version is not None
            and seed_version.created_by == "seed_import"
            and payload.steps
            and (list(seed_version.trigger_conditions or []) != payload.trigger_conditions
                 or list(seed_version.steps or []) != payload.steps)
        ):
            seed_version.trigger_conditions = payload.trigger_conditions
            seed_version.steps = payload.steps
            seed_version.content_hash = _hash(payload.to_json())
            seed_version.validation_json = validate_version_payload(
                payload, {slug}
            ).as_json()
            seed_drift = True

        if (
            existing_meta.get("priority") == expected["priority"]
            and existing_meta.get("scenes") == expected["scenes"]
            and not seed_drift
        ):
            report.unchanged.append(slug)
        else:
            existing_meta.update(expected)
            existing.metadata_json = existing_meta
            report.updated.append(slug)

    await db.flush()
    await _refresh_agent_skill_cache()
    return report


async def _refresh_agent_skill_cache() -> None:
    """Keep the legacy markdown cache from serving stale content."""
    try:
        from services.agent.skills import clear_cache

        clear_cache()
    except Exception:  # noqa: BLE001 - cache refresh is best-effort
        logger.debug("skill cache clear failed", exc_info=True)


# ── reads ──────────────────────────────────────────────────────────────────


async def _get_skill_by_slug(db: AsyncSession, slug: str) -> TeachingSkill | None:
    result = await db.execute(select(TeachingSkill).where(TeachingSkill.slug == slug))
    return result.scalar_one_or_none()


async def get_skill(db: AsyncSession, skill_ref: str) -> TeachingSkill | None:
    """Resolve by uuid or slug."""
    try:
        skill_id = uuid.UUID(skill_ref)
    except (ValueError, AttributeError):
        return await _get_skill_by_slug(db, skill_ref)
    return await db.get(TeachingSkill, skill_id)


async def get_version(
    db: AsyncSession, skill_id: uuid.UUID, version: int
) -> TeachingSkillVersion | None:
    result = await db.execute(
        select(TeachingSkillVersion).where(
            TeachingSkillVersion.skill_id == skill_id,
            TeachingSkillVersion.version == version,
        )
    )
    return result.scalar_one_or_none()


async def list_skills(
    db: AsyncSession, *, enabled_only: bool = False, limit: int = 100
) -> list[TeachingSkill]:
    query = select(TeachingSkill).order_by(TeachingSkill.created_at).limit(limit)
    if enabled_only:
        query = query.where(TeachingSkill.enabled.is_(True))  # noqa: E712
    return list((await db.execute(query)).scalars().all())


async def list_versions(
    db: AsyncSession, skill_id: uuid.UUID
) -> list[TeachingSkillVersion]:
    result = await db.execute(
        select(TeachingSkillVersion)
        .where(TeachingSkillVersion.skill_id == skill_id)
        .order_by(TeachingSkillVersion.version)
    )
    return list(result.scalars().all())


def version_diff(old: TeachingSkillVersion | None, new: TeachingSkillVersion) -> dict[str, Any]:
    """Field-by-field diff between two versions (added / removed / changed)."""
    def _as_list(value: Any) -> list[str]:
        return [str(item) for item in (value or [])]

    def _list_delta(a: list[str], b: list[str]) -> dict[str, list[str]]:
        return {
            "added": [item for item in b if item not in a],
            "removed": [item for item in a if item not in b],
            "unchanged": [item for item in b if item in a],
        }

    if old is None:
        return {
            "from_version": None,
            "to_version": new.version,
            "summary": "initial version",
            "changes": {
                "steps": {"added": _as_list(new.steps), "removed": [], "unchanged": []},
                "trigger_conditions": {
                    "added": _as_list(new.trigger_conditions), "removed": [], "unchanged": [],
                },
                "target_dimensions": {
                    "added": _as_list(new.target_dimensions), "removed": [], "unchanged": [],
                },
            },
        }

    changes: dict[str, Any] = {
        "steps": _list_delta(_as_list(old.steps), _as_list(new.steps)),
        "trigger_conditions": _list_delta(
            _as_list(old.trigger_conditions), _as_list(new.trigger_conditions)
        ),
        "target_dimensions": _list_delta(
            _as_list(old.target_dimensions), _as_list(new.target_dimensions)
        ),
        "reused_skills": _list_delta(_as_list(old.reused_skills), _as_list(new.reused_skills)),
    }
    for name in ("expected_effect", "origin_kind", "change_summary"):
        before, after = getattr(old, name), getattr(new, name)
        if before != after:
            changes[name] = {"from": before, "to": after}

    return {
        "from_version": old.version,
        "to_version": new.version,
        "summary": new.change_summary or "",
        "changes": changes,
        "changed_fields": sorted(
            key for key, value in changes.items()
            if isinstance(value, dict) and (value.get("added") or value.get("removed")
                                            or "from" in value)
        ),
    }


# ── writes ─────────────────────────────────────────────────────────────────


async def create_skill_with_version(
    db: AsyncSession,
    *,
    slug: str,
    name: str,
    description: str | None,
    payload: VersionPayload,
    origin: str = ORIGIN_MANUAL,
    source_reflection_id: uuid.UUID | None = None,
    source_evaluation_id: uuid.UUID | None = None,
    created_by: str = "manual",
    enable: bool = False,
    provenance_json: dict[str, Any] | None = None,
) -> tuple[TeachingSkill, TeachingSkillVersion, ValidationResult]:
    """Create a brand-new skill (V1). Validation gates enablement, not creation."""
    known = {skill.slug for skill in await list_skills(db, limit=500)}
    validation = validate_version_payload(payload, known | {slug})

    existing = await _get_skill_by_slug(db, slug)
    if existing is not None:
        raise ValueError(f"skill already exists: {slug}")

    skill = TeachingSkill(
        slug=slug,
        name=name[:200],
        description=(description or "")[:2000] or None,
        origin=origin,
        source_reflection_id=source_reflection_id,
        source_evaluation_id=source_evaluation_id,
        enabled=bool(enable and validation.ok),
        current_version=1,
        version_count=1,
        metadata_json={"created_by": created_by},
    )
    db.add(skill)
    await db.flush()

    version = TeachingSkillVersion(
        skill_id=skill.id,
        version=1,
        status=VERSION_ACTIVE if (enable and validation.ok) else VERSION_DRAFT,
        trigger_conditions=payload.trigger_conditions,
        applicable_objectives=payload.applicable_objectives,
        target_dimensions=payload.target_dimensions,
        steps=payload.steps,
        expected_effect=payload.expected_effect,
        verification=payload.verification,
        reused_skills=payload.reused_skills,
        origin_kind=payload.origin_kind,
        change_summary=payload.change_summary,
        source_reflection_id=source_reflection_id,
        source_evaluation_id=source_evaluation_id,
        created_by=created_by,
        content_hash=_hash(payload.to_json()),
        validation_json=validation.as_json(),
        provenance_json=provenance_json,
    )
    db.add(version)
    await db.flush()
    await _refresh_agent_skill_cache()
    return skill, version, validation


async def add_version(
    db: AsyncSession,
    *,
    skill: TeachingSkill,
    payload: VersionPayload,
    origin: str = ORIGIN_REFLECTION,
    source_reflection_id: uuid.UUID | None = None,
    source_evaluation_id: uuid.UUID | None = None,
    created_by: str = "reflection",
    enable: bool = False,
    expected_version: int | None = None,
    provenance_json: dict[str, Any] | None = None,
) -> tuple[TeachingSkillVersion, ValidationResult]:
    """Append a new immutable version. Never mutates earlier versions.

    ``expected_version`` is an optimistic-concurrency guard: if another writer
    already produced that version number, we refuse instead of overwriting it.
    """
    known = {item.slug for item in await list_skills(db, limit=500)}
    validation = validate_version_payload(payload, known)

    if expected_version is not None and skill.version_count != expected_version:
        raise ValueError(
            f"version conflict: expected {expected_version}, found {skill.version_count}"
        )

    next_version = skill.version_count + 1
    clash = await get_version(db, skill.id, next_version)
    if clash is not None:
        raise ValueError(f"version {next_version} already exists for {skill.slug}")

    version = TeachingSkillVersion(
        skill_id=skill.id,
        version=next_version,
        status=VERSION_ACTIVE if (enable and validation.ok) else (
            VERSION_VALIDATED if validation.ok else VERSION_DRAFT
        ),
        trigger_conditions=payload.trigger_conditions,
        applicable_objectives=payload.applicable_objectives,
        target_dimensions=payload.target_dimensions,
        steps=payload.steps,
        expected_effect=payload.expected_effect,
        verification=payload.verification,
        reused_skills=payload.reused_skills,
        origin_kind=payload.origin_kind,
        change_summary=payload.change_summary,
        source_reflection_id=source_reflection_id,
        source_evaluation_id=source_evaluation_id,
        created_by=created_by,
        content_hash=_hash(payload.to_json()),
        validation_json=validation.as_json(),
        provenance_json=provenance_json,
    )
    db.add(version)

    # Previous active version becomes superseded only when the new one is live.
    if enable and validation.ok:
        old = await get_version(db, skill.id, skill.current_version)
        if old is not None and old.status == VERSION_ACTIVE:
            old.status = VERSION_SUPERSEDED
        skill.current_version = next_version
        skill.enabled = True
    skill.version_count = next_version
    # ``origin`` records where the *skill* came from (seed/markdown vs a
    # reflection-created skill). Evolving a seed skill does not turn it into a
    # reflection skill — the version row carries that provenance instead.
    if skill.origin == ORIGIN_SEED:
        pass
    else:
        skill.origin = origin
    if source_reflection_id is not None:
        skill.source_reflection_id = source_reflection_id
    if source_evaluation_id is not None:
        skill.source_evaluation_id = source_evaluation_id
    skill.updated_at = _now()

    await db.flush()
    await _refresh_agent_skill_cache()
    return version, validation


async def set_enabled(
    db: AsyncSession, skill: TeachingSkill, enabled: bool
) -> tuple[TeachingSkill, ValidationResult]:
    """Enable/disable a skill. Enabling re-validates the current version."""
    version = await get_version(db, skill.id, skill.current_version)
    if version is None:
        raise ValueError("skill has no current version")

    if enabled:
        payload = VersionPayload(
            trigger_conditions=list(version.trigger_conditions or []),
            applicable_objectives=list(version.applicable_objectives or []),
            target_dimensions=list(version.target_dimensions or []),
            steps=list(version.steps or []),
            expected_effect=version.expected_effect or "",
            verification=dict(version.verification or {}),
            reused_skills=list(version.reused_skills or []),
            origin_kind=version.origin_kind or "compose_existing",
            change_summary=version.change_summary or "",
        )
        known = {item.slug for item in await list_skills(db, limit=500)}
        validation = validate_version_payload(payload, known)
        if not validation.ok:
            # Refuse to enable an invalid strategy; keep the skill off.
            skill.enabled = False
            skill.updated_at = _now()
            return skill, validation
        version.status = VERSION_ACTIVE
    else:
        validation = ValidationResult(ok=True)
        if version.status == VERSION_ACTIVE:
            version.status = VERSION_VALIDATED

    skill.enabled = enabled
    skill.updated_at = _now()
    await db.flush()
    await _refresh_agent_skill_cache()
    return skill, validation


async def rollback_to(
    db: AsyncSession, skill: TeachingSkill, target_version: int
) -> tuple[TeachingSkill, ValidationResult]:
    """Point the skill back at an earlier version (history is preserved)."""
    target = await get_version(db, skill.id, target_version)
    if target is None:
        raise ValueError(f"version {target_version} does not exist for {skill.slug}")
    if target_version == skill.current_version:
        return skill, ValidationResult(ok=True, problems=["already current"])

    payload = VersionPayload(
        trigger_conditions=list(target.trigger_conditions or []),
        applicable_objectives=list(target.applicable_objectives or []),
        target_dimensions=list(target.target_dimensions or []),
        steps=list(target.steps or []),
        expected_effect=target.expected_effect or "",
        verification=dict(target.verification or {}),
        reused_skills=list(target.reused_skills or []),
        origin_kind=target.origin_kind or "compose_existing",
        change_summary=target.change_summary or "",
    )
    known = {item.slug for item in await list_skills(db, limit=500)}
    validation = validate_version_payload(payload, known)
    if not validation.ok:
        return skill, validation

    current = await get_version(db, skill.id, skill.current_version)
    if current is not None and current.status == VERSION_ACTIVE:
        current.status = VERSION_ROLLED_BACK
    target.status = VERSION_ACTIVE
    skill.current_version = target_version
    skill.enabled = True
    skill.updated_at = _now()
    await db.flush()
    await _refresh_agent_skill_cache()
    return skill, validation


def serialize_skill(skill: TeachingSkill) -> dict[str, Any]:
    return {
        "skill_id": str(skill.id),
        "slug": skill.slug,
        "name": skill.name,
        "description": skill.description,
        "origin": skill.origin,
        "enabled": skill.enabled,
        "current_version": skill.current_version,
        "version_count": skill.version_count,
        "source_reflection_id": str(skill.source_reflection_id)
        if skill.source_reflection_id else None,
        "source_evaluation_id": str(skill.source_evaluation_id)
        if skill.source_evaluation_id else None,
        "seed_path": skill.seed_path,
        "metadata": skill.metadata_json,
        "created_at": skill.created_at.isoformat() if skill.created_at else None,
        "updated_at": skill.updated_at.isoformat() if skill.updated_at else None,
    }


def serialize_version(version: TeachingSkillVersion) -> dict[str, Any]:
    return {
        "version_id": str(version.id),
        "skill_id": str(version.skill_id),
        "version": version.version,
        "status": version.status,
        "trigger_conditions": version.trigger_conditions or [],
        "applicable_objectives": version.applicable_objectives or [],
        "target_dimensions": version.target_dimensions or [],
        "steps": version.steps or [],
        "expected_effect": version.expected_effect,
        "verification": version.verification or {},
        "reused_skills": version.reused_skills or [],
        "origin_kind": version.origin_kind,
        "change_summary": version.change_summary,
        "source_reflection_id": str(version.source_reflection_id)
        if version.source_reflection_id else None,
        "source_evaluation_id": str(version.source_evaluation_id)
        if version.source_evaluation_id else None,
        "created_by": version.created_by,
        "content_hash": version.content_hash,
        "provenance": version.provenance_json or {},
        "validation": version.validation_json or {},
        "created_at": version.created_at.isoformat() if version.created_at else None,
    }


async def invocation_stats(
    db: AsyncSession, skill_id: uuid.UUID, version: int | None = None
) -> dict[str, Any]:
    """Historical effectiveness, used by the selector."""
    query = select(SkillInvocation).where(SkillInvocation.skill_id == skill_id)
    if version is not None:
        query = query.where(SkillInvocation.version == version)
    rows = list((await db.execute(query)).scalars().all())
    evaluated = [row for row in rows if row.status == "evaluated"]
    gains: list[float] = []
    for row in evaluated:
        outcome = row.outcome_json or {}
        value = outcome.get("target_dimension_score")
        if isinstance(value, (int, float)):
            gains.append(float(value))
    return {
        "invocations": len(rows),
        "evaluated": len(evaluated),
        "mean_target_score": round(sum(gains) / len(gains), 3) if gains else None,
        "success_rate": round(
            sum(1 for row in evaluated if (row.outcome_json or {}).get("success")) / len(evaluated),
            3,
        ) if evaluated else None,
    }
