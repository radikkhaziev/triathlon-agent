"""add hrvt2_power to activity_hrv

Revision ID: w3d4e5f6a7b8
Revises: v2c3d4e5f6a7
Create Date: 2026-05-08 18:00:00.000000

Stores power at HRVT2 (anaerobic threshold, DFA a1 = 0.50) for Ride activities.
Computed parallel to hrvt1_power via the same power↔HR linear regression on
WORK segments. Used by the drift detector to push power at the *anaerobic*
threshold to Intervals.icu's `ftp` field — Coggan's FTP definition ≈ pow at
LT2 ≈ pow at HRVT2, mirroring the LTHR=HRVT2 mapping landed in v2c3d4e5f6a7.

Existing rows stay NULL; the next ramp-test re-process populates them.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "w3d4e5f6a7b8"
down_revision: Union[str, None] = "v2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("activity_hrv", sa.Column("hrvt2_power", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("activity_hrv", "hrvt2_power")
