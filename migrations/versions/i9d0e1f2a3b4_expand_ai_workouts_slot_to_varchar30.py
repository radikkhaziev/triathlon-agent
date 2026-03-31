"""Expand ai_workouts.slot from VARCHAR(10) to VARCHAR(30)

Revision ID: i9d0e1f2a3b4
Revises: h8c9d0e1f2a3
Create Date: 2026-03-31 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "i9d0e1f2a3b4"
down_revision: Union[str, None] = "h8c9d0e1f2a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "ai_workouts",
        "slot",
        existing_type=sa.String(10),
        type_=sa.String(30),
        existing_nullable=False,
    )


def downgrade() -> None:
    op.alter_column(
        "ai_workouts",
        "slot",
        existing_type=sa.String(30),
        type_=sa.String(10),
        existing_nullable=False,
    )
