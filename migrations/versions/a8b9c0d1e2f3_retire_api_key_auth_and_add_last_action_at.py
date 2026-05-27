"""retire api_key auth, add last_action_at for stale-user deactivation

Revision ID: a8b9c0d1e2f3
Revises: f6e360a8f4fa
Create Date: 2026-05-27

Two intertwined changes that ship together:

1. Retire legacy Intervals.icu api_key auth — only one user remained on this
   path (everyone else migrated to OAuth). That user is deactivated here, and
   all api_key columns are dropped. The OAuth migration prompt UI + 4 legacy
   scheduler jobs (`with_legacy_athletes`) come out in the same PR.

2. Add `users.last_action_at` (timestamp of last bot/webapp interaction) so a
   daily cron can deactivate users who haven't touched the bot in 30 days —
   stops the morning-report token spend on dormant accounts. Backfilled to
   `now() - 14 days` (not `created_at`): gives every existing user a 16-day
   grace window from deploy (30d threshold − 14d backfill) so a user who
   happened to be quiet the week before the deploy doesn't get deactivated
   on the very first cron run.

Kept on purpose:
- `intervals_oauth_scope` — load-bearing for future scope-validation UX
  (e.g. "we can't update your zones because you didn't grant SETTINGS:WRITE").
- `intervals_access_token_encrypted` — primary OAuth credential.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "a8b9c0d1e2f3"
down_revision: str | None = "f6e360a8f4fa"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Deactivate the one remaining api_key user before dropping the column.
    #    Identifying them by intervals_auth_method (the column we're about to
    #    drop) — safe because this is the last migration that can see it.
    op.execute("UPDATE users SET is_active = false " "WHERE intervals_auth_method = 'api_key' AND is_active = true")

    # 2. Drop api_key auth columns + CHECK.
    op.drop_constraint("ck_users_intervals_auth_method", "users", type_="check")
    op.drop_column("users", "intervals_auth_method")
    op.drop_column("users", "api_key_encrypted")
    op.drop_column("users", "preferred_model")

    # 3. Add last_action_at + backfill to "14 days ago" + index for cron query.
    #    See the module docstring for why 14 days, not `created_at`.
    op.add_column(
        "users",
        sa.Column("last_action_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute("UPDATE users SET last_action_at = NOW() - INTERVAL '14 days' " "WHERE last_action_at IS NULL")
    op.create_index("ix_users_last_action_at", "users", ["last_action_at"])


def downgrade() -> None:
    op.drop_index("ix_users_last_action_at", table_name="users")
    op.drop_column("users", "last_action_at")

    op.add_column("users", sa.Column("preferred_model", sa.String(length=30), nullable=True))
    op.add_column("users", sa.Column("api_key_encrypted", sa.Text(), nullable=True))
    op.add_column(
        "users",
        sa.Column(
            "intervals_auth_method",
            sa.String(length=10),
            nullable=False,
            server_default="api_key",
        ),
    )
    op.create_check_constraint(
        "ck_users_intervals_auth_method",
        "users",
        "intervals_auth_method IN ('api_key', 'oauth', 'none')",
    )
    # Cannot recover the encrypted api_key value or know who was the api_key
    # user — manual restore required if you actually need to roll this back.
    # Setting all OAuth users back to 'oauth' so the app keeps working.
    op.execute("UPDATE users SET intervals_auth_method = 'oauth' " "WHERE intervals_access_token_encrypted IS NOT NULL")
    op.execute("UPDATE users SET intervals_auth_method = 'none' " "WHERE intervals_access_token_encrypted IS NULL")
