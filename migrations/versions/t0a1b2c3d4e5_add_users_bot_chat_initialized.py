"""add_users_bot_chat_initialized

Revision ID: t0a1b2c3d4e5
Revises: c9e2f5a8b1d4
Create Date: 2026-04-26 06:00:00.000000

Adds ``users.bot_chat_initialized`` — true once the user has actually opened a
chat with the Telegram bot (sent /start or any message). The Login Widget on
the webapp authenticates a user without requiring a bot chat to exist, so
``chat_id`` alone is not enough to know whether ``sendMessage`` will succeed.

Backfill assumes every existing row has a bot chat — every account predating
this migration must have entered via /start (the widget flow is recent).
Per-tenant exceptions (e.g. a known widget-only signup that slipped through)
are handled out-of-band via the operational runbook below, NOT in the
migration — schema migrations should be reproducible across environments
and free of environment-specific data fixes.

Operational runbook for known-broken users (run via psql or
``python -m cli shell`` after upgrade):

    UPDATE users SET bot_chat_initialized = FALSE WHERE chat_id = '<chat_id>';

The OAuth-init gate + frontend banner will then steer that user through
/start automatically. See issue #266.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "t0a1b2c3d4e5"
down_revision: Union[str, None] = "c9e2f5a8b1d4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("bot_chat_initialized", sa.Boolean(), nullable=False, server_default=sa.false()),
    )
    op.execute("UPDATE users SET bot_chat_initialized = TRUE")
    # Drop the server_default — new rows must opt in explicitly via /start
    # or the equivalent code path; leaving the default would silently mask
    # the same bug for any future widget-only signups.
    op.alter_column("users", "bot_chat_initialized", server_default=None)


def downgrade() -> None:
    op.drop_column("users", "bot_chat_initialized")
