"""add teaching_reflections table (METAMIND module 3)

Revision ID: 20261114_0028
Revises: 20261114_0027
Create Date: 2026-11-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261114_0028"
down_revision: Union[str, None] = "20261114_0027"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "teaching_reflections",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", sa.String(length=36),
                  sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("evaluation_id", sa.String(length=36),
                  sa.ForeignKey("teaching_evaluations.id", ondelete="CASCADE"), nullable=False),
        sa.Column("objective_slug", sa.String(length=64), nullable=True),
        sa.Column("objective_id", sa.String(length=36), nullable=True),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("strategy_version", sa.String(length=64), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("rubric_version", sa.String(length=32), nullable=True),
        sa.Column("problem_statement", sa.Text(), nullable=True),
        sa.Column("problem_severity", sa.String(length=20), nullable=True),
        sa.Column("problem_dimension", sa.String(length=24), nullable=True),
        sa.Column("cause_statement", sa.Text(), nullable=True),
        sa.Column("cause_confidence", sa.String(length=10), nullable=True),
        sa.Column("cause_hypothesis_json", sa.JSON(), nullable=True),
        sa.Column("improvement_json", sa.JSON(), nullable=True),
        sa.Column("verification_plan_json", sa.JSON(), nullable=True),
        sa.Column("evidence_json", sa.JSON(), nullable=True),
        sa.Column("input_package_json", sa.JSON(), nullable=True),
        sa.Column("input_hash", sa.String(length=64), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("model_provider", sa.String(length=64), nullable=True),
        sa.Column("model_name", sa.String(length=128), nullable=True),
        sa.Column("raw_llm_response", sa.Text(), nullable=True),
        sa.Column("error_code", sa.String(length=50), nullable=True),
        sa.Column("error_message", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_teaching_reflection_user_course_time",
                    "teaching_reflections", ["user_id", "course_id", "created_at"])
    op.create_index("ix_teaching_reflection_evaluation",
                    "teaching_reflections", ["evaluation_id"])


def downgrade() -> None:
    op.drop_index("ix_teaching_reflection_evaluation", table_name="teaching_reflections")
    op.drop_index("ix_teaching_reflection_user_course_time", table_name="teaching_reflections")
    op.drop_table("teaching_reflections")
