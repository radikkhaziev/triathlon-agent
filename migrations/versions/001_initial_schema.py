"""initial schema

Revision ID: 001
Revises:
Create Date: 2026-02-22

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "daily_metrics",
        sa.Column("date", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("sleep_score", sa.Integer(), nullable=True),
        sa.Column("sleep_duration", sa.Integer(), nullable=True),
        sa.Column("sleep_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sleep_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("sleep_stress_avg", sa.Integer(), nullable=True),
        sa.Column("sleep_hrv_avg", sa.Integer(), nullable=True),
        sa.Column("sleep_heart_rate_avg", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("date"),
    )


def downgrade() -> None:
    op.drop_table("daily_metrics")
