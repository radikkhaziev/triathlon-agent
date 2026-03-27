"""Add activity_hrv and pa_baseline tables for DFA alpha 1 pipeline

Revision ID: d6e7f8a9b0c1
Revises: c5d6e7f8a9b0
Create Date: 2026-03-24 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d6e7f8a9b0c1"
down_revision: Union[str, None] = "c5d6e7f8a9b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activity_hrv",
        sa.Column("activity_id", sa.String(), sa.ForeignKey("activities.id"), nullable=False),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("activity_type", sa.String(), nullable=False),
        # Quality
        sa.Column("hrv_quality", sa.String(), nullable=True),
        sa.Column("artifact_pct", sa.Float(), nullable=True),
        sa.Column("rr_count", sa.Integer(), nullable=True),
        # DFA alpha 1 summary
        sa.Column("dfa_a1_mean", sa.Float(), nullable=True),
        sa.Column("dfa_a1_warmup", sa.Float(), nullable=True),
        # Thresholds
        sa.Column("hrvt1_hr", sa.Float(), nullable=True),
        sa.Column("hrvt1_power", sa.Float(), nullable=True),
        sa.Column("hrvt1_pace", sa.String(), nullable=True),
        sa.Column("hrvt2_hr", sa.Float(), nullable=True),
        sa.Column("threshold_r_squared", sa.Float(), nullable=True),
        sa.Column("threshold_confidence", sa.String(), nullable=True),
        # Readiness (Ra)
        sa.Column("ra_pct", sa.Float(), nullable=True),
        sa.Column("pa_today", sa.Float(), nullable=True),
        # Durability (Da)
        sa.Column("da_pct", sa.Float(), nullable=True),
        # Processing status
        sa.Column("processing_status", sa.String(), nullable=False, server_default="processed"),
        # Raw timeseries
        sa.Column("dfa_timeseries", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("activity_id"),
    )
    op.create_index("ix_activity_hrv_date", "activity_hrv", ["date"])

    op.create_table(
        "pa_baseline",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("activity_type", sa.String(), nullable=False),
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("pa_value", sa.Float(), nullable=False),
        sa.Column("dfa_a1_ref", sa.Float(), nullable=True),
        sa.Column("quality", sa.String(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_pa_baseline_type_date", "pa_baseline", ["activity_type", "date"])


def downgrade() -> None:
    op.drop_index("ix_pa_baseline_type_date", table_name="pa_baseline")
    op.drop_table("pa_baseline")
    op.drop_index("ix_activity_hrv_date", table_name="activity_hrv")
    op.drop_table("activity_hrv")
