"""Add athlete_settings, athlete_goals tables and age/primary_sport to users

Revision ID: k1f2a3b4c5d6
Revises: j0e1f2a3b4c5
Create Date: 2026-04-05 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "k1f2a3b4c5d6"
down_revision: Union[str, None] = "j0e1f2a3b4c5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # --- athlete_settings ---
    op.create_table(
        "athlete_settings",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("sport", sa.String(30), nullable=False),
        sa.Column("lthr", sa.Integer(), nullable=True),
        sa.Column("max_hr", sa.Integer(), nullable=True),
        sa.Column("ftp", sa.Integer(), nullable=True),
        sa.Column("threshold_pace", sa.Float(), nullable=True),
        sa.Column("pace_units", sa.String(20), nullable=True),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "sport", name="uq_athlete_settings_user_sport"),
    )

    # --- athlete_goals ---
    op.create_table(
        "athlete_goals",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False, index=True),
        sa.Column("category", sa.String(10), nullable=False),
        sa.Column("event_name", sa.String(), nullable=False),
        sa.Column("event_date", sa.Date(), nullable=False),
        sa.Column("sport_type", sa.String(20), nullable=False),
        sa.Column("disciplines", sa.JSON(), nullable=True),
        sa.Column("ctl_target", sa.Float(), nullable=True),
        sa.Column("per_sport_targets", sa.JSON(), nullable=True),
        sa.Column("intervals_event_id", sa.Integer(), nullable=True),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true")),
        sa.Column("synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    # --- users: add age, primary_sport ---
    op.add_column("users", sa.Column("age", sa.Integer(), nullable=True))
    op.add_column("users", sa.Column("primary_sport", sa.String(20), nullable=True))

    # --- Seed data for user_id=1 (skip if user doesn't exist, e.g. test DB) ---
    conn = op.get_bind()
    user_exists = conn.execute(sa.text("SELECT 1 FROM users WHERE id = 1")).fetchone()
    if not user_exists:
        return

    conn.execute(
        sa.text(
            """
        INSERT INTO athlete_settings (user_id, sport, lthr, max_hr, ftp, threshold_pace, pace_units)
        VALUES
            (1, 'Ride', 153, 179, 233, NULL, NULL),
            (1, 'Run', 153, 179, NULL, 295, 'MINS_KM'),
            (1, 'Swim', NULL, 179, NULL, 141, 'SECS_100M')
    """
        )
    )

    conn.execute(
        sa.text(
            """
        INSERT INTO athlete_goals (user_id, category, event_name, event_date,
            sport_type, disciplines, ctl_target, per_sport_targets, is_active)
        VALUES (1, 'RACE_A', 'Ironman 70.3', '2026-09-15', 'triathlon',
            '["Swim", "Ride", "Run"]'::json, 75,
            '{"swim": 15, "bike": 35, "run": 25}'::json, true)
    """
        )
    )

    conn.execute(sa.text("UPDATE users SET age = 43, primary_sport = 'triathlon' WHERE id = 1"))


def downgrade() -> None:
    op.drop_column("users", "primary_sport")
    op.drop_column("users", "age")
    op.drop_table("athlete_goals")
    op.drop_table("athlete_settings")
