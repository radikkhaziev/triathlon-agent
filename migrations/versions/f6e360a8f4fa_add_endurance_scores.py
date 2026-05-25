"""add_endurance_scores

Revision ID: f6e360a8f4fa
Revises: a7b8c9d0e1f2
Create Date: 2026-05-25 17:00:00.000000

Daily snapshot table for the composite Endurance Score metric — Phase 2 of
``docs/ENDURANCE_SCORE_SPEC.md``. Replaces Phase-1 on-the-fly computation
(~1s/request) with O(N rows) reads from this table.

Why daily granularity (vs weekly): dashboard supports period filters
1M / 3M / 6M / 1Y. Weekly = 4 points on 1M (chart looks like sticks), daily
= 30 (smooth line). Storage cost is trivial — ~365 rows/user/year.

Why a separate table (vs extending ``wellness.sport_info`` JSONB): different
source of truth (wellness = Intervals.icu, ES = our compute), different
write cadence (wellness ≈ 8 syncs/day + webhooks, ES = daily cron + per-write
hooks), time-series queries on a (user_id, snapshot_date DESC) index beat
unnest'ing JSONB on every read. Changing the ES formula regenerates ONE
table without touching wellness.

See spec §4.1 + §4.2 + §11.G/H.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

# revision identifiers, used by Alembic.
revision: str = "f6e360a8f4fa"
down_revision: Union[str, None] = "a7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "endurance_scores",
        sa.Column("id", sa.BigInteger(), primary_key=True, autoincrement=True),
        sa.Column(
            "user_id",
            sa.Integer(),
            sa.ForeignKey("users.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Daily snapshot date in local timezone — matches `wellness.date` shape.
        sa.Column("snapshot_date", sa.Date(), nullable=False),
        # Composite ES score (0..ENDURANCE_MAX=8000). Stored as INTEGER — we
        # round before write in the actor, no fractional values exist.
        sa.Column("score", sa.Integer(), nullable=False),
        # VO2max composite (ml/kg/min). Diagnostic — surfaces in API response.
        # Pure module rounds to 1 dp (`data/endurance_score.py:compute_endurance_score`),
        # storage uses `Numeric(5, 1)` to match — values fit comfortably (0..99.9).
        sa.Column("vo2max_composite", sa.Numeric(5, 1), nullable=True),
        # Components breakdown — {base, long_term, recent, duration, consistency,
        # recovery, per_sport: [...]}. JSONB (not JSON) for drill-down + future
        # GIN-indexed queries (`->>`, `@>`) without text->json reparsing.
        # Matches the project's queryable-JSON convention (FitnessProjection
        # uses JSONB for the same reason — see migration b8c9d0e1f2a3).
        sa.Column("components", JSONB(), nullable=False),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        # Idempotent upsert key — Level-1/2 actors can fire multiple times per
        # day; ON CONFLICT (user_id, snapshot_date) DO UPDATE keeps the row
        # fresh without duplicates. CLI backfill relies on this to skip
        # already-computed dates unless --force.
        sa.UniqueConstraint("user_id", "snapshot_date", name="uq_endurance_scores_user_date"),
    )
    # DESC ordering on snapshot_date matches the canonical query pattern:
    # "latest snapshot", "last N days trend", "history for badge engine".
    op.create_index(
        "ix_endurance_scores_user_date",
        "endurance_scores",
        ["user_id", sa.text("snapshot_date DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_endurance_scores_user_date", table_name="endurance_scores")
    op.drop_table("endurance_scores")
