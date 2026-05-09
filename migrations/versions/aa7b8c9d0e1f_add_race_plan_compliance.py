"""add_race_plan_compliance

Revision ID: aa7b8c9d0e1f
Revises: c3c3d4e5f6a7
Create Date: 2026-05-09 22:30:00.000000

PR3 — Phase 3 compliance metrics shape (define-not-ship per RACE_PLAN_SPEC §14).

Two changes, one migration:
1. New table ``race_plan_compliance`` — per-leg compliance metrics computed
   post-race (HR-corridor / pace-power band / fueling). Three NUMERIC fields
   per leg, scoped to user_id, FK on race_plans (CASCADE) + races (nullable
   for races that get re-tagged or where the Activity link is broken).
2. New column ``races.carbs_consumed_g INTEGER`` — manual entry, sourced
   from the athlete after the race (no auto-detect on Garmin GDPR exports).
   Required input for ``fueling_compliance_pct`` calculation; NULL when
   athlete didn't log carbs (compliance metric stays NULL too).

This migration ships the SCHEMA so post-race data starts collecting in the
right shape from day one. The actor that auto-fills these rows on the
``ACTIVITY_UPLOADED`` webhook is Phase 3 work — not in this PR.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "aa7b8c9d0e1f"
down_revision: Union[str, None] = "c3c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "race_plan_compliance",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("race_plan_id", sa.Integer(), nullable=False),
        sa.Column("race_id", sa.Integer(), nullable=True),
        # Denormalised user_id — lets us scope all reads via WHERE user_id=?
        # without joining race_plans every time. Defense-in-depth multi-tenant.
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("leg_name", sa.String(length=32), nullable=False),
        # NUMERIC(5,2) fits 0.00 — 999.99; in practice 0-100. NULL means
        # "couldn't compute this metric" (e.g. fueling without carbs_consumed_g).
        sa.Column("hr_compliance_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("band_compliance_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("fueling_compliance_pct", sa.Numeric(5, 2), nullable=True),
        sa.Column("leg_duration_sec", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.ForeignKeyConstraint(["race_plan_id"], ["race_plans.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["race_id"], ["races.id"]),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_race_plan_compliance_user_id", "race_plan_compliance", ["user_id"])
    op.create_index("ix_race_plan_compliance_race_plan", "race_plan_compliance", ["race_plan_id"])

    # Manual entry — the athlete logs total carbs after the race. Auto-detect
    # from Garmin/Strava exports is unreliable (intake events are sparse and
    # often missing); manual gives signal we can trust.
    op.add_column("races", sa.Column("carbs_consumed_g", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("races", "carbs_consumed_g")
    op.drop_index("ix_race_plan_compliance_race_plan", table_name="race_plan_compliance")
    op.drop_index("ix_race_plan_compliance_user_id", table_name="race_plan_compliance")
    op.drop_table("race_plan_compliance")
