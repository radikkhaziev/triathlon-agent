"""add hrvt2_pace to activity_hrv

Revision ID: v2c3d4e5f6a7
Revises: c4d5e6f7a8b9
Create Date: 2026-05-08 12:00:00.000000

Stores pace at HRVT2 (anaerobic threshold, DFA a1 = 0.50) for Run activities.
Computed parallel to hrvt1_pace via the same speed↔HR linear regression on
WORK segments. Used by the drift detector to push pace at the *anaerobic*
threshold to Intervals.icu's `threshold_pace` field — Intervals' threshold
pace conceptually corresponds to LTHR (= HRVT2), not HRVT1.

Existing rows stay NULL; the next ramp-test re-process populates them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "v2c3d4e5f6a7"
down_revision: Union[str, None] = "c4d5e6f7a8b9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("activity_hrv", sa.Column("hrvt2_pace", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("activity_hrv", "hrvt2_pace")
