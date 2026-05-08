"""add per-threshold confidence to activity_hrv

Revision ID: x4e5f6a7b8c9
Revises: w3d4e5f6a7b8
Create Date: 2026-05-08 19:00:00.000000

Single ``threshold_confidence`` field hides the case where one threshold (say
HRVT1) is locally well-resolved while the other (HRVT2 near top of test) sits
on a sparse tail — aggregate R² is the same but the two thresholds carry
different reliability. New ``hrvt1_confidence`` and ``hrvt2_confidence``
combine R² with local point density (n_points in α1 ∈ ±0.15 of each crossing).

Existing ``threshold_confidence`` field stays for backwards compat. The drift
detector keeps using `r_squared` for now; switching the gate to
`hrvt2_confidence` happens with the sigmoid-fit rewrite (see
``docs/DFA_REGRESSION_METHODOLOGY_SPEC.md`` §4 «E3 → H1»).
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "x4e5f6a7b8c9"
down_revision: Union[str, None] = "w3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Unlimited String matches the convention in data/db/activity.py (28 other
    # string columns also use plain ``String`` without length). VARCHAR(16)
    # would have triggered an Alembic autogen drift on the next mass-rebuild.
    op.add_column("activity_hrv", sa.Column("hrvt1_confidence", sa.String(), nullable=True))
    op.add_column("activity_hrv", sa.Column("hrvt2_confidence", sa.String(), nullable=True))


def downgrade() -> None:
    op.drop_column("activity_hrv", "hrvt2_confidence")
    op.drop_column("activity_hrv", "hrvt1_confidence")
