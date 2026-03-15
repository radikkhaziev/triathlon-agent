"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-02-22

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_metrics",
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("sleep_score", sa.Integer(), nullable=True),
        sa.Column("sleep_duration", sa.Integer(), nullable=True),
        sa.Column("hrv_last", sa.Float(), nullable=True),
        sa.Column("hrv_baseline", sa.Float(), nullable=True),
        sa.Column("body_battery", sa.Integer(), nullable=True),
        sa.Column("resting_hr", sa.Float(), nullable=True),
        sa.Column("stress_score", sa.Integer(), nullable=True),
        sa.Column("readiness_score", sa.Integer(), nullable=True),
        sa.Column("readiness_level", sa.String(), nullable=True),
        sa.Column("ctl", sa.Float(), nullable=True),
        sa.Column("atl", sa.Float(), nullable=True),
        sa.Column("tsb", sa.Float(), nullable=True),
        sa.Column("ctl_swim", sa.Float(), nullable=True),
        sa.Column("ctl_bike", sa.Float(), nullable=True),
        sa.Column("ctl_run", sa.Float(), nullable=True),
        sa.Column("ai_recommendation", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("date"),
    )

    op.create_table(
        "activities",
        sa.Column("activity_id", sa.Integer(), autoincrement=False, nullable=False),
        sa.Column("date", sa.String(), nullable=True),
        sa.Column("sport", sa.String(), nullable=True),
        sa.Column("duration_sec", sa.Integer(), nullable=True),
        sa.Column("distance_m", sa.Float(), nullable=True),
        sa.Column("avg_hr", sa.Float(), nullable=True),
        sa.Column("max_hr", sa.Float(), nullable=True),
        sa.Column("avg_power", sa.Float(), nullable=True),
        sa.Column("norm_power", sa.Float(), nullable=True),
        sa.Column("tss", sa.Float(), nullable=True),
        sa.Column("synced_at", sa.DateTime(), server_default=sa.func.now(), nullable=False),
        sa.PrimaryKeyConstraint("activity_id"),
    )
    op.create_index("ix_activities_date", "activities", ["date"])

    op.create_table(
        "scheduled_workouts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("scheduled_date", sa.String(), nullable=True),
        sa.Column("sport", sa.String(), nullable=True),
        sa.Column("workout_name", sa.String(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("planned_tss", sa.Float(), nullable=True),
        sa.Column("source", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_scheduled_workouts_scheduled_date", "scheduled_workouts", ["scheduled_date"])

    op.create_table(
        "tss_history",
        sa.Column("date", sa.String(), nullable=False),
        sa.Column("sport", sa.String(), nullable=False),
        sa.Column("tss", sa.Float(), nullable=True),
        sa.PrimaryKeyConstraint("date", "sport"),
    )


def downgrade() -> None:
    op.drop_table("tss_history")
    op.drop_index("ix_scheduled_workouts_scheduled_date", table_name="scheduled_workouts")
    op.drop_table("scheduled_workouts")
    op.drop_index("ix_activities_date", table_name="activities")
    op.drop_table("activities")
    op.drop_table("daily_metrics")
