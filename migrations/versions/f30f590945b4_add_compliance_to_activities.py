"""add compliance to activities

Revision ID: f30f590945b4
Revises: aab8c9d0e1f2
Create Date: 2026-05-13 16:43:50.543828

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f30f590945b4"
down_revision: Union[str, None] = "aab8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Intervals.icu's native `compliance` (% match between planned workout_doc
    # and actual recorded data). Captured via webhook-fed ActivityDTO sync.
    # Distinct from `race_plan_compliance` table (post-race race-plan metrics)
    # and `workout_cards.compliance` (per-card adherence label).
    op.add_column("activities", sa.Column("compliance", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("activities", "compliance")
