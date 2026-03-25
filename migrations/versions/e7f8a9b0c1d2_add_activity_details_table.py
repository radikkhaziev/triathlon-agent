"""Add activity_details table

Revision ID: e7f8a9b0c1d2
Revises: d08531ba074d
Create Date: 2026-03-25 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "e7f8a9b0c1d2"
down_revision: Union[str, None] = "d08531ba074d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activity_details",
        sa.Column("activity_id", sa.String(), sa.ForeignKey("activities.id"), nullable=False),
        sa.Column("max_hr", sa.Integer(), nullable=True),
        sa.Column("avg_power", sa.Integer(), nullable=True),
        sa.Column("normalized_power", sa.Integer(), nullable=True),
        sa.Column("max_speed", sa.Float(), nullable=True),
        sa.Column("avg_speed", sa.Float(), nullable=True),
        sa.Column("pace", sa.Float(), nullable=True),
        sa.Column("gap", sa.Float(), nullable=True),
        sa.Column("distance", sa.Float(), nullable=True),
        sa.Column("elevation_gain", sa.Float(), nullable=True),
        sa.Column("avg_cadence", sa.Float(), nullable=True),
        sa.Column("avg_stride", sa.Float(), nullable=True),
        sa.Column("calories", sa.Integer(), nullable=True),
        sa.Column("intensity_factor", sa.Float(), nullable=True),
        sa.Column("variability_index", sa.Float(), nullable=True),
        sa.Column("efficiency_factor", sa.Float(), nullable=True),
        sa.Column("power_hr", sa.Float(), nullable=True),
        sa.Column("decoupling", sa.Float(), nullable=True),
        sa.Column("trimp", sa.Float(), nullable=True),
        sa.Column("hr_zones", sa.JSON(), nullable=True),
        sa.Column("power_zones", sa.JSON(), nullable=True),
        sa.Column("pace_zones", sa.JSON(), nullable=True),
        sa.Column("intervals", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("activity_id"),
    )


def downgrade() -> None:
    op.drop_table("activity_details")
