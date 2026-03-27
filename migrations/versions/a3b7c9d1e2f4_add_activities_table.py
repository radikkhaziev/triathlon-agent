"""Add activities table for per-sport CTL calculation

Revision ID: a3b7c9d1e2f4
Revises: 109064c8f4df
Create Date: 2026-03-24 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a3b7c9d1e2f4"
down_revision: Union[str, None] = "109064c8f4df"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("start_date_local", sa.String(), nullable=False),
        sa.Column("type", sa.String(), nullable=True),
        sa.Column("icu_training_load", sa.Float(), nullable=True),
        sa.Column("moving_time", sa.Integer(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_activities_start_date", "activities", ["start_date_local"])


def downgrade() -> None:
    op.drop_index("ix_activities_start_date", table_name="activities")
    op.drop_table("activities")
