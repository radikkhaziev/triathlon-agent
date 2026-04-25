"""Add hey_message timestamp to user_backfill_state

Revision ID: c9e2f5a8b1d4
Revises: b8d1c4e7f0a3
Create Date: 2026-04-25 09:30:00.000000

Tracks whether a post-onboarding "hey, you can chat" reminder was sent to
the athlete. NULL = not sent. Lives next to ``started_at`` since both are
onboarding-lifecycle state. ``UserBackfillState.start()`` deliberately
resets this on ``--force`` retry — paired with a ``status='completed'``
filter in the cron, that prevents duplicate reminders. See issue #258.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c9e2f5a8b1d4"
down_revision: Union[str, None] = "b8d1c4e7f0a3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_backfill_state",
        sa.Column("hey_message", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_backfill_state", "hey_message")
