"""Add exercise_cards and workout_cards tables

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-03-28 14:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "exercise_cards",
        sa.Column("id", sa.String(50), nullable=False),
        sa.Column("name_ru", sa.String(200), nullable=False),
        sa.Column("name_en", sa.String(200), nullable=True),
        sa.Column("muscles", sa.String(200), nullable=True),
        sa.Column("equipment", sa.String(100), nullable=True),
        sa.Column("group_tag", sa.String(50), nullable=True),
        sa.Column("default_sets", sa.Integer(), nullable=False, server_default="2"),
        sa.Column("default_reps", sa.Integer(), nullable=False, server_default="15"),
        sa.Column("default_duration_sec", sa.Integer(), nullable=True),
        sa.Column("steps", sa.JSON(), nullable=False),
        sa.Column("focus", sa.Text(), nullable=True),
        sa.Column("breath", sa.String(100), nullable=True),
        sa.Column("animation_html", sa.Text(), nullable=False),
        sa.Column("animation_css", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )

    op.create_table(
        "workout_cards",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("date", sa.String(10), nullable=False),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("exercises", sa.JSON(), nullable=False),
        sa.Column("total_duration_min", sa.Integer(), nullable=True),
        sa.Column("equipment_summary", sa.String(200), nullable=True),
        sa.Column("intervals_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_workout_cards_date", "workout_cards", ["date"])


def downgrade() -> None:
    op.drop_index("idx_workout_cards_date")
    op.drop_table("workout_cards")
    op.drop_table("exercise_cards")
