"""add fitness_projection.sport_info JSON column

Revision ID: b8c9d0e1f2a3
Revises: bb8c9d0e1f2a
Create Date: 2026-05-11

Intervals.icu FITNESS_UPDATED webhook ships a per-record ``sportInfo`` array
with future-projected per-sport eFTP / wPrime / pMax. Mode 2 race projection
needs ``current_eftp`` on race day — we read it from this column. Pre-existing
rows stay NULL until next webhook refresh (no backfill actor needed; refresh
cadence covers it within a day).
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "b8c9d0e1f2a3"
down_revision: str | None = "bb8c9d0e1f2a"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # JSONB (not JSON) to match the rest of the project's queryable JSON columns
    # (`race_plans.payload`, etc.) — preserves option of GIN indexing for future
    # per-sport lookups (`payload->'sport_info'->>'Ride'`) without a re-migration.
    with op.batch_alter_table("fitness_projection") as batch_op:
        batch_op.add_column(sa.Column("sport_info", JSONB(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("fitness_projection") as batch_op:
        batch_op.drop_column("sport_info")
