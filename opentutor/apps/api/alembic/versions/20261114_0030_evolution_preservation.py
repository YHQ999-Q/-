"""METAMIND P0-1: preserve evidence across demo runs + honest injection tracking

Adds run_tag to evaluations/reflections/invocations, a frozen provenance snapshot
to skill_versions, and prompt-injection honesty fields to skill_invocations. It
also relaxes teaching_reflections.evaluation_id from a hard non-null FK with
ON DELETE CASCADE to nullable ON DELETE SET NULL, so resetting evaluations never
deletes the reflections that are the run's audit evidence.

Revision ID: 20261114_0030
Revises: 20261114_0029
Create Date: 2026-11-14
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261114_0030"
down_revision: Union[str, None] = "20261114_0029"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # run_tag on evaluations (distinguish Run01 / Run02 evidence).
    op.add_column("teaching_evaluations", sa.Column("run_tag", sa.String(32), nullable=True))
    # run_tag on reflections + evaluation FK relaxed to nullable SET NULL.
    op.add_column("teaching_reflections", sa.Column("run_tag", sa.String(32), nullable=True))
    op.alter_column(
        "teaching_reflections", "evaluation_id",
        existing_type=sa.String(36),
        nullable=True,
    )
    # Frozen provenance on skill_versions.
    op.add_column("skill_versions", sa.Column("provenance_json", sa.JSON(), nullable=True))
    # run_tag + prompt-injection honesty on skill_invocations.
    op.add_column("skill_invocations", sa.Column("run_tag", sa.String(32), nullable=True))
    op.add_column("skill_invocations", sa.Column("prompt_hash", sa.String(64), nullable=True))
    op.add_column(
        "skill_invocations",
        sa.Column("prompt_injection_verified", sa.Boolean(), nullable=True),
    )
    op.add_column(
        "skill_invocations", sa.Column("execution_state", sa.String(16), nullable=True),
    )
    op.add_column(
        "skill_invocations", sa.Column("executed_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "skill_invocations",
        sa.Column("tutor_response_locator", sa.String(300), nullable=True),
    )
    # Drop the CASCADE FK and re-add it as SET NULL. Alembic batch mode is used
    # so this also works on SQLite-backed environments.
    with op.batch_alter_table("teaching_reflections") as batch:
        batch.drop_constraint(
            "reflections_evaluation_fk",
            type_="foreignkey",
        )
        batch.create_foreign_key(
            "reflections_evaluation_fk",
            "teaching_evaluations",
            ["evaluation_id"],
            ["id"],
            ondelete="SET NULL",
        )


def downgrade() -> None:
    with op.batch_alter_table("teaching_reflections") as batch:
        batch.drop_constraint("reflections_evaluation_fk", type_="foreignkey")
        batch.create_foreign_key(
            "reflections_evaluation_fk",
            "teaching_evaluations",
            ["evaluation_id"],
            ["id"],
            ondelete="CASCADE",
        )
    op.alter_column(
        "teaching_reflections", "evaluation_id",
        existing_type=sa.String(36),
        nullable=False,
    )
    op.drop_column("teaching_reflections", "run_tag")
    op.drop_column("skill_invocations", "tutor_response_locator")
    op.drop_column("skill_invocations", "executed_at")
    op.drop_column("skill_invocations", "execution_state")
    op.drop_column("skill_invocations", "prompt_injection_verified")
    op.drop_column("skill_invocations", "prompt_hash")
    op.drop_column("skill_invocations", "run_tag")
    op.drop_column("skill_versions", "provenance_json")
    op.drop_column("teaching_evaluations", "run_tag")
