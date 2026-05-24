"""composite index on scheduled_workouts(user_id, start_date_local)

Revision ID: a7b8c9d0e1f2
Revises: aa7b8c9d0e1f, d2e3f4a5b6c7
Create Date: 2026-05-24

Merges two pre-existing parallel heads (`aa7b8c9d0e1f` race_plan_compliance
and `d2e3f4a5b6c7` paired_event_id) so `alembic upgrade head` resolves to a
single revision, and adds the composite index.

The dashboard `/api/training-load` endpoint (plan-aware forecast) filters
scheduled_workouts by both columns on every poll. The existing single-column
`ix_scheduled_workouts_user_id` makes Postgres seq-scan the user's whole
future slate after the user filter — measurable on athletes with hundreds of
planned events. The composite covers this hot path and the existing
`get_last_scheduled_date` lookup for free; `type` is intentionally left out
(cardinality 4, bitmap-OR is cheap and including it would bloat the index).
"""

from __future__ import annotations

from alembic import op

revision: str = "a7b8c9d0e1f2"
down_revision: tuple[str, ...] = ("aa7b8c9d0e1f", "d2e3f4a5b6c7")
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index(
        "ix_scheduled_workouts_user_date",
        "scheduled_workouts",
        ["user_id", "start_date_local"],
    )


def downgrade() -> None:
    op.drop_index("ix_scheduled_workouts_user_date", table_name="scheduled_workouts")
