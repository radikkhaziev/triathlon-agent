"""drop disciplines column from athlete_goals (orphan field)

Revision ID: z6a7b8c9d0e1
Revises: y5f6a7b8c9d0
Create Date: 2026-05-09

`athlete_goals.disciplines` was set by `suggest_race` for Triathlon/Duathlon/
Aquathlon events but never read anywhere — `sport_type` (single enum) is the
canonical «what kind of race» field. See issue #323 Strand A for context.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "z6a7b8c9d0e1"
down_revision: str | None = "y5f6a7b8c9d0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    with op.batch_alter_table("athlete_goals") as batch_op:
        batch_op.drop_column("disciplines")


def downgrade() -> None:
    with op.batch_alter_table("athlete_goals") as batch_op:
        batch_op.add_column(sa.Column("disciplines", sa.JSON(), nullable=True))
