"""add_weekly_reports

Revision ID: bb8c9d0e1f2a
Revises: aa7b8c9d0e1f
Create Date: 2026-05-10 19:30:00.000000

Backend storage for weekly training summaries previously sent only to Telegram
chat. Telegram has a 4096-char visible-text limit and an opaque spam heuristic
that occasionally drops long messages despite returning ``ok=true``; storing
the full report in DB lets the webapp serve it as a first-class artefact and
the chat becomes a notification (preview + WebApp button).

Schema notes:
- ``week_start`` is the Monday of the summarised week (``today - today.weekday()``
  in ``settings.TIMEZONE``). UNIQUE on ``(user_id, week_start)`` is the
  idempotency anchor — repeated generations for the same week (manual rerun,
  cron coalesce) overwrite via UPSERT instead of piling up duplicates.
- ``content_md`` is the raw Claude markdown — the same string previously sent
  to Telegram. The webapp renders it via react-markdown; the chat-preview
  helper strips bold/italic at extraction time. We do NOT pre-compute / store
  the preview because the format may evolve, and recomputing from content_md
  is O(few hundred bytes).
- No FK ``ON DELETE CASCADE``: weekly reports are auditable history, surviving
  user deactivation. If a user is deleted outright the FK fails and the
  caller knows to clean up explicitly.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "bb8c9d0e1f2a"
down_revision: str | None = "aa7b8c9d0e1f"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "weekly_reports",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("week_start", sa.Date(), nullable=False),
        sa.Column("content_md", sa.Text(), nullable=False),
        sa.Column("model", sa.String(length=64), nullable=False),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("user_id", "week_start", name="uq_weekly_reports_user_week"),
    )
    # Drives the list-history endpoint (PR2) — most recent first per user.
    op.create_index(
        "ix_weekly_reports_user_week_desc",
        "weekly_reports",
        ["user_id", sa.text("week_start DESC")],
    )


def downgrade() -> None:
    op.drop_index("ix_weekly_reports_user_week_desc", table_name="weekly_reports")
    op.drop_table("weekly_reports")
