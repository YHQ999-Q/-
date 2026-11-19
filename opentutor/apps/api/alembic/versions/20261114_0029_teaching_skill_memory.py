"""add teaching skill memory tables (METAMIND module 4)

Revision ID: 20261114_0029
Revises: 20261114_0028
Create Date: 2026-11-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20261114_0029"
down_revision: Union[str, None] = "20261114_0028"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "teaching_skills",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("slug", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("origin", sa.String(length=20), nullable=False, server_default="seed"),
        sa.Column("source_reflection_id", sa.String(length=36),
                  sa.ForeignKey("teaching_reflections.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_evaluation_id", sa.String(length=36),
                  sa.ForeignKey("teaching_evaluations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("current_version", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("version_count", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("seed_path", sa.String(length=300), nullable=True),
        sa.Column("metadata_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("slug", name="uq_teaching_skill_slug"),
    )
    op.create_index("ix_teaching_skill_enabled", "teaching_skills", ["enabled"])

    op.create_table(
        "skill_versions",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("skill_id", sa.String(length=36),
                  sa.ForeignKey("teaching_skills.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="draft"),
        sa.Column("trigger_conditions", sa.JSON(), nullable=True),
        sa.Column("applicable_objectives", sa.JSON(), nullable=True),
        sa.Column("target_dimensions", sa.JSON(), nullable=True),
        sa.Column("steps", sa.JSON(), nullable=True),
        sa.Column("expected_effect", sa.Text(), nullable=True),
        sa.Column("verification", sa.JSON(), nullable=True),
        sa.Column("reused_skills", sa.JSON(), nullable=True),
        sa.Column("origin_kind", sa.String(length=24), nullable=True),
        sa.Column("change_summary", sa.Text(), nullable=True),
        sa.Column("source_reflection_id", sa.String(length=36),
                  sa.ForeignKey("teaching_reflections.id", ondelete="SET NULL"), nullable=True),
        sa.Column("source_evaluation_id", sa.String(length=36),
                  sa.ForeignKey("teaching_evaluations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.String(length=40), nullable=True),
        sa.Column("content_hash", sa.String(length=64), nullable=True),
        sa.Column("validation_json", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint("skill_id", "version", name="uq_skill_version"),
    )
    op.create_index("ix_skill_version_skill", "skill_versions", ["skill_id"])

    op.create_table(
        "skill_invocations",
        sa.Column("id", sa.String(length=36), primary_key=True),
        sa.Column("user_id", sa.String(length=36),
                  sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("course_id", sa.String(length=36),
                  sa.ForeignKey("courses.id", ondelete="CASCADE"), nullable=False),
        sa.Column("skill_id", sa.String(length=36),
                  sa.ForeignKey("teaching_skills.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version_id", sa.String(length=36),
                  sa.ForeignKey("skill_versions.id", ondelete="CASCADE"), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("session_id", sa.String(length=64), nullable=True),
        sa.Column("objective_slug", sa.String(length=64), nullable=True),
        sa.Column("scene", sa.String(length=50), nullable=True),
        sa.Column("selection_json", sa.JSON(), nullable=True),
        sa.Column("match_score", sa.Float(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="selected"),
        sa.Column("selected_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.Column("injected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("evaluation_id", sa.String(length=36),
                  sa.ForeignKey("teaching_evaluations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("outcome_json", sa.JSON(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )
    op.create_index("ix_skill_invocation_user_course_time", "skill_invocations",
                    ["user_id", "course_id", "created_at"])
    op.create_index("ix_skill_invocation_session", "skill_invocations", ["session_id"])
    op.create_index("ix_skill_invocation_skill", "skill_invocations", ["skill_id", "version"])


def downgrade() -> None:
    op.drop_index("ix_skill_invocation_skill", table_name="skill_invocations")
    op.drop_index("ix_skill_invocation_session", table_name="skill_invocations")
    op.drop_index("ix_skill_invocation_user_course_time", table_name="skill_invocations")
    op.drop_table("skill_invocations")
    op.drop_index("ix_skill_version_skill", table_name="skill_versions")
    op.drop_table("skill_versions")
    op.drop_index("ix_teaching_skill_enabled", table_name="teaching_skills")
    op.drop_table("teaching_skills")
