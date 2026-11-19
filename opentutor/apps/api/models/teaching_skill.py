"""Teaching skill memory (METAMIND module 4).

Three entities, deliberately separate:

* ``TeachingSkill``       — identity: slug, origin, enable switch, current version.
* ``TeachingSkillVersion``— immutable snapshot of one strategy revision.
* ``SkillInvocation``     — one selection in one teaching episode, with its
  lifecycle (selected -> injected -> evaluated) and the version it used.

Why versions are immutable: the Evolution Engine's whole claim is that a change
was made, kept, and later verified. Mutating a version in place would erase the
evidence that anything changed.

Nothing here executes model output. Versions store structured fields only
(trigger conditions, steps, dimensions, verification); the Tutor prompt is not
touched by this module.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.compat import CompatJSONB, CompatUUID

# ── origins ────────────────────────────────────────────────────────────────
ORIGIN_SEED = "seed"            # imported from apps/api/skills/*.md
ORIGIN_REFLECTION = "reflection"  # created by the Evolution Engine
ORIGIN_MANUAL = "manual"

# ── version lifecycle ──────────────────────────────────────────────────────
VERSION_DRAFT = "draft"        # written but not validated/enabled
VERSION_VALIDATED = "validated"  # passed schema + business rules
VERSION_ACTIVE = "active"      # currently served to the selector
VERSION_SUPERSEDED = "superseded"
VERSION_ROLLED_BACK = "rolled_back"

# ── proposal lineage ───────────────────────────────────────────────────────
ORIGIN_KIND_REUSE = "reuse_existing"
ORIGIN_KIND_COMPOSE = "compose_existing"
ORIGIN_KIND_NEW = "new_skill"

# ── invocation lifecycle ───────────────────────────────────────────────────
INVOCATION_SELECTED = "selected"
INVOCATION_INJECTED = "injected"
INVOCATION_EXECUTED = "executed"
INVOCATION_EVALUATED = "evaluated"
INVOCATION_FAILED = "failed"
INVOCATION_DISCARDED = "discarded"

# How much we can claim about whether the tutor followed the skill's steps.
# ``injected`` only means the text reached the prompt; we do NOT auto-promote to
# ``executed`` because that would equate "in the prompt" with "carried out".
# We record an honest state and let a human (or the evaluation) judge effect.
EXEC_UNVERIFIED = "unverified"
EXEC_PARTIAL = "partial"
EXEC_FULL = "full"

ALLOWED_DIMENSIONS = ("understanding", "application", "transfer", "multiple")


class TeachingSkill(Base):
    """Stable identity of a teaching skill; versions carry the content."""

    __tablename__ = "teaching_skills"
    __table_args__ = (
        UniqueConstraint("slug", name="uq_teaching_skill_slug"),
        Index("ix_teaching_skill_enabled", "enabled"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    slug: Mapped[str] = mapped_column(String(80), nullable=False)
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # seed | reflection | manual
    origin: Mapped[str] = mapped_column(String(20), nullable=False, default=ORIGIN_SEED)
    # Reflection that produced the latest version, when applicable.
    source_reflection_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        CompatUUID, ForeignKey("teaching_reflections.id", ondelete="SET NULL"), nullable=True
    )
    source_evaluation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        CompatUUID, ForeignKey("teaching_evaluations.id", ondelete="SET NULL"), nullable=True
    )

    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    current_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    version_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    # Where the shipped markdown lived, so a re-import can detect drift.
    seed_path: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)
    metadata_json: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )


class TeachingSkillVersion(Base):
    """Immutable snapshot of one revision of a skill."""

    __tablename__ = "skill_versions"
    __table_args__ = (
        UniqueConstraint("skill_id", "version", name="uq_skill_version"),
        Index("ix_skill_version_skill", "skill_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    skill_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("teaching_skills.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=VERSION_DRAFT)

    # ── structured strategy (no free-form code, no prompt overrides) ──
    trigger_conditions: Mapped[Optional[list]] = mapped_column(CompatJSONB, nullable=True)
    applicable_objectives: Mapped[Optional[list]] = mapped_column(CompatJSONB, nullable=True)
    target_dimensions: Mapped[Optional[list]] = mapped_column(CompatJSONB, nullable=True)
    steps: Mapped[Optional[list]] = mapped_column(CompatJSONB, nullable=True)
    expected_effect: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    verification: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    # Composite lineage: ids of skills this version builds on.
    reused_skills: Mapped[Optional[list]] = mapped_column(CompatJSONB, nullable=True)
    # reuse_existing | compose_existing | new_skill
    origin_kind: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)
    change_summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    # Provenance
    source_reflection_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        CompatUUID, ForeignKey("teaching_reflections.id", ondelete="SET NULL"), nullable=True
    )
    source_evaluation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        CompatUUID, ForeignKey("teaching_evaluations.id", ondelete="SET NULL"), nullable=True
    )
    created_by: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    validation_json: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    # Frozen snapshot of the reflection that produced this version (problem,
    # proposal, input_hash). SET NULL on skills/versions only breaks the FK
    # *link* if a reflection is removed; this snapshot keeps the version able to
    # show its origin even then.
    provenance_json: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class SkillInvocation(Base):
    """One skill selection in one teaching episode, tracked to the version."""

    __tablename__ = "skill_invocations"
    __table_args__ = (
        Index("ix_skill_invocation_user_course_time", "user_id", "course_id", "created_at"),
        Index("ix_skill_invocation_session", "session_id"),
        Index("ix_skill_invocation_skill", "skill_id", "version"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    skill_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("teaching_skills.id", ondelete="CASCADE"), nullable=False
    )
    version_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("skill_versions.id", ondelete="CASCADE"), nullable=False
    )
    # Denormalised so a historical invocation still reports the version number
    # even if the row is later archived.
    version: Mapped[int] = mapped_column(Integer, nullable=False)

    session_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    objective_slug: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    scene: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    # Demo/run label (run01 / run02) so selective resets never touch a run's evidence.
    run_tag: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Why it was chosen: matched conditions, score, runner-ups.
    selection_json: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    match_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default=INVOCATION_SELECTED
    )
    selected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    injected_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    evaluated_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    # P0-3 honesty: whether the marker really reached the FULL assembled prompt
    # (sha256 of that prompt) and how much of the skill we can claim was carried
    # out. We never set executed just because injected was set.
    prompt_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    prompt_injection_verified: Mapped[Optional[bool]] = mapped_column(
        Boolean, nullable=True
    )
    execution_state: Mapped[Optional[str]] = mapped_column(
        String(16), nullable=True, default=EXEC_UNVERIFIED
    )
    executed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    # Where the tutor's actual answer for this episode is archived (file path /
    # marker), so a human can eyeball whether the steps were followed.
    tutor_response_locator: Mapped[Optional[str]] = mapped_column(String(300), nullable=True)

    evaluation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        CompatUUID, ForeignKey("teaching_evaluations.id", ondelete="SET NULL"), nullable=True
    )
    outcome_json: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
