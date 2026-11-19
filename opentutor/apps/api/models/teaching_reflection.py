"""Teaching reflection records (METAMIND module 3).

A reflection is a *diagnosis with an auditable evidence trail*, produced after an
evaluation. It never mutates scores, learner progress, skills or prompts.

Status values:

* ``completed``               — a problem, cause hypothesis, proposal and verification plan
* ``insufficient_evidence``   — the evaluation cannot support a diagnosis
* ``no_improvement_needed``   — the learner already met the objective
* ``failed``                  — the model output could not be validated (see error_code)
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.compat import CompatJSONB, CompatUUID

REFLECTION_STATUS_COMPLETED = "completed"
REFLECTION_STATUS_INSUFFICIENT = "insufficient_evidence"
REFLECTION_STATUS_NO_IMPROVEMENT = "no_improvement_needed"
REFLECTION_STATUS_FAILED = "failed"


class TeachingReflection(Base):
    """Structured, evidence-linked reflection over one evaluation."""

    __tablename__ = "teaching_reflections"
    __table_args__ = (
        Index("ix_teaching_reflection_user_course_time",
              "user_id", "course_id", "created_at"),
        Index("ix_teaching_reflection_evaluation", "evaluation_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )
    # The evaluation that produced this reflection. Nullable + SET NULL so an
    # evaluation reset never cascades away the reflection: the reflection is
    # independently valuable audit evidence (its problem/proposal/input_hash
    # remain even if the evaluation row is gone). Same goes for the source ids
    # on skills/versions, which already use SET NULL.
    evaluation_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        CompatUUID, ForeignKey("teaching_evaluations.id", ondelete="SET NULL"), nullable=True
    )
    # Demo/run label so Run01 vs Run02 evidence stays distinguishable and a
    # reset can scope to one run without touching the other.
    run_tag: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    objective_slug: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    objective_id: Mapped[Optional[uuid.UUID]] = mapped_column(CompatUUID, nullable=True)
    session_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    strategy_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(String(32), nullable=False)
    rubric_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # Problem block
    problem_statement: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    problem_severity: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    problem_dimension: Mapped[Optional[str]] = mapped_column(String(24), nullable=True)

    # Cause hypothesis (always unverified)
    cause_statement: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    cause_confidence: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    cause_hypothesis_json: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)

    # Improvement proposal + verification plan
    improvement_json: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    verification_plan_json: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)

    # Audit trail
    evidence_json: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    input_package_json: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    input_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    model_provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    raw_llm_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
