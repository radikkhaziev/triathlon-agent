"""Add zone time columns to activity_details

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-03-29 18:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("activity_details", sa.Column("hr_zone_times", sa.JSON(), nullable=True))
    op.add_column("activity_details", sa.Column("power_zone_times", sa.JSON(), nullable=True))
    op.add_column("activity_details", sa.Column("pace_zone_times", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.drop_column("activity_details", "pace_zone_times")
    op.drop_column("activity_details", "power_zone_times")
    op.drop_column("activity_details", "hr_zone_times")
