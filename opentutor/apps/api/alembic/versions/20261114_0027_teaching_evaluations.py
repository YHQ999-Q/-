"""add teaching_evaluations table (METAMIND module 2)

Revision ID: 20261114_0027
Revises: 20261028_0026
Create Date: 2026-11-14

Local SQLite deployments bootstrap new tables through
``services.sqlite_migrations.upgrade_sqlite()`` (Base.metadata.create_all).
This migration keeps Alembic-managed deployments in sync with the same shape.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "20261114_0027"
down_revision: Union[str, None] = "20261028_0026"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "teaching_evaluations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column(
            "user_id", sa.String(length=36),
            sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column(
            "course_id", sa.String(length=36),
            sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False,
        ),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("objective_slug", sa.String(length=64), nullable=True),
        sa.Column("objective_id", sa.String(length=36), nullable=True),
        sa.Column("objective_text", sa.Text(), nullable=True),
        sa.Column("rubric_version", sa.String(length=32), nullable=False),
        sa.Column("strategy_version", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="partial"),
        sa.Column("verdict", sa.String(length=32), nullable=True),
        sa.Column("understanding_score", sa.Integer(), nullable=True),
        sa.Column("application_score", sa.Integer(), nullable=True),
        sa.Column("transfer_score", sa.Integer(), nullable=True),
        sa.Column("dimensions_measured", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("rationale_json", sa.JSON(), nullable=True),
        sa.Column("inputs_json", sa.JSON(), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("raw_llm_response", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("model_provider", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True),
            server_default=sa.func.now(), nullable=False,
        ),
    )
    op.create_index(
        "ix_teaching_eval_user_course_time",
        "teaching_evaluations", ["user_id", "course_id", "created_at"],
    )
    op.create_index(
        "ix_teaching_eval_objective",
        "teaching_evaluations", ["user_id", "course_id", "objective_slug"],
    )


def downgrade() -> None:
    op.drop_index("ix_teaching_eval_objective", table_name="teaching_evaluations")
    op.drop_index("ix_teaching_eval_user_course_time", table_name="teaching_evaluations")
    op.drop_table("teaching_evaluations")
