"""Teaching evaluation records (METAMIND module 2).

One row per evaluation of one learning objective. Dimensions are nullable on
purpose: ``NULL`` means **not measured**, which is a different fact from a
measured score of ``0``. Downstream (Reflection) must be able to tell them
apart, so the distinction is enforced by the schema rather than by convention.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Index, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from database import Base
from models.compat import CompatJSONB, CompatUUID

# Evaluation run status
EVAL_STATUS_COMPLETED = "completed"  # every requested dimension was measured
EVAL_STATUS_PARTIAL = "partial"  # some dimensions measured, some not/failed
EVAL_STATUS_FAILED = "failed"  # nothing usable produced; see error_code

# Overall verdict (rule-based, never LLM-decided)
VERDICT_ACHIEVED = "achieved"
VERDICT_PARTIALLY_ACHIEVED = "partially_achieved"
VERDICT_NOT_ACHIEVED = "not_achieved"
VERDICT_INSUFFICIENT_EVIDENCE = "insufficient_evidence"

DIMENSIONS = ("understanding", "application", "transfer")


class TeachingEvaluation(Base):
    """Structured, evidence-backed judgement of whether an objective was met."""

    __tablename__ = "teaching_evaluations"
    __table_args__ = (
        Index("ix_teaching_eval_user_course_time", "user_id", "course_id", "created_at"),
        Index("ix_teaching_eval_objective", "user_id", "course_id", "objective_slug"),
    )

    id: Mapped[uuid.UUID] = mapped_column(CompatUUID, primary_key=True, default=uuid.uuid4)
    user_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("users.id", ondelete="CASCADE"), nullable=False
    )
    course_id: Mapped[uuid.UUID] = mapped_column(
        CompatUUID, ForeignKey("courses.id", ondelete="CASCADE"), nullable=False
    )

    # A chat session id, or a demo run label such as "run01". Free-form so the
    # evaluation is not tied to chat persistence.
    session_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    # Demo/run label (run01 / run02); lets a reset scope to one run and keeps
    # the two runs' evaluations independently comparable evidence.
    run_tag: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    objective_slug: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    objective_id: Mapped[Optional[uuid.UUID]] = mapped_column(CompatUUID, nullable=True)
    objective_text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    rubric_version: Mapped[str] = mapped_column(String(32), nullable=False)
    # Teaching strategy / skill version that produced this learning attempt.
    # Written by later modules; NULL means "no strategy version recorded".
    strategy_version: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    status: Mapped[str] = mapped_column(String(20), nullable=False, default=EVAL_STATUS_PARTIAL)
    verdict: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)

    # NULL = not measured (do not read as 0)
    understanding_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    application_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    transfer_score: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    dimensions_measured: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Per-dimension detail: instruments used, verbatim quotes, item ids, reasoning.
    evidence_json: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    rationale_json: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)
    # Exactly what was judged (ids, prompts, hashes) so the row is reproducible.
    inputs_json: Mapped[Optional[dict]] = mapped_column(CompatJSONB, nullable=True)

    # sha256 over the normalised inputs: same hash => same judged material.
    content_hash: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)

    raw_llm_response: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    model_provider: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    model_name: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
