"""Add training_log table

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-03-28 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "training_log",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("sport", sa.String(30), nullable=True),
        # Plan
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column("original_name", sa.Text(), nullable=True),
        sa.Column("original_description", sa.Text(), nullable=True),
        sa.Column("original_duration_sec", sa.Integer(), nullable=True),
        # Adaptation
        sa.Column("adapted_name", sa.Text(), nullable=True),
        sa.Column("adapted_description", sa.Text(), nullable=True),
        sa.Column("adapted_duration_sec", sa.Integer(), nullable=True),
        sa.Column("adaptation_reason", sa.Text(), nullable=True),
        # Pre-context
        sa.Column("pre_recovery_score", sa.Float(), nullable=True),
        sa.Column("pre_recovery_category", sa.String(20), nullable=True),
        sa.Column("pre_hrv_status", sa.String(20), nullable=True),
        sa.Column("pre_hrv_delta_pct", sa.Float(), nullable=True),
        sa.Column("pre_rhr_today", sa.Float(), nullable=True),
        sa.Column("pre_rhr_status", sa.String(20), nullable=True),
        sa.Column("pre_tsb", sa.Float(), nullable=True),
        sa.Column("pre_ctl", sa.Float(), nullable=True),
        sa.Column("pre_atl", sa.Float(), nullable=True),
        sa.Column("pre_ra_pct", sa.Float(), nullable=True),
        sa.Column("pre_sleep_score", sa.Float(), nullable=True),
        # Actual
        sa.Column("actual_activity_id", sa.String(50), nullable=True),
        sa.Column("actual_sport", sa.String(30), nullable=True),
        sa.Column("actual_duration_sec", sa.Integer(), nullable=True),
        sa.Column("actual_avg_hr", sa.Float(), nullable=True),
        sa.Column("actual_tss", sa.Float(), nullable=True),
        sa.Column("actual_max_zone_time", sa.String(10), nullable=True),
        sa.Column("compliance", sa.String(20), nullable=True),
        # Post-outcome
        sa.Column("post_recovery_score", sa.Float(), nullable=True),
        sa.Column("post_hrv_delta_pct", sa.Float(), nullable=True),
        sa.Column("post_rhr_today", sa.Float(), nullable=True),
        sa.Column("post_sleep_score", sa.Float(), nullable=True),
        sa.Column("post_ra_pct", sa.Float(), nullable=True),
        sa.Column("recovery_delta", sa.Float(), nullable=True),
        # Meta
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_training_log_date", "training_log", ["date"])
    op.create_index("idx_training_log_source", "training_log", ["source"])


def downgrade() -> None:
    op.drop_index("idx_training_log_source")
    op.drop_index("idx_training_log_date")
    op.drop_table("training_log")
