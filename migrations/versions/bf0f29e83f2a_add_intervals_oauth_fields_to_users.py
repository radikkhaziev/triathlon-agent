"""add intervals oauth fields to users

Revision ID: bf0f29e83f2a
Revises: ad411cd63317
Create Date: 2026-04-15 15:53:45.079975

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "bf0f29e83f2a"
down_revision: Union[str, None] = "ad411cd63317"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("users", sa.Column("intervals_access_token_encrypted", sa.Text(), nullable=True))
    op.add_column("users", sa.Column("intervals_oauth_scope", sa.String(), nullable=True))
    # `server_default='api_key'` is **intentionally permanent**: it backfills
    # existing rows on upgrade AND guarantees any row inserted outside the ORM
    # (psql, admin script, data import) gets a valid enum value. The Python-
    # level default in `User.intervals_auth_method` stays in sync for ORM
    # inserts. Don't drop the server_default in a follow-up — both layers
    # should keep writing the same value.
    op.add_column(
        "users",
        sa.Column("intervals_auth_method", sa.String(length=10), nullable=False, server_default="api_key"),
    )
    # Enum-style CHECK constraint to reject invalid auth methods at the DB
    # level. Without this, a buggy caller could persist e.g. "rubbish" and
    # silently break `IntervalsClient.for_user()` dispatch in Phase 2.
    op.create_check_constraint(
        "ck_users_intervals_auth_method",
        "users",
        "intervals_auth_method IN ('api_key', 'oauth', 'none')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_users_intervals_auth_method", "users", type_="check")
    op.drop_column("users", "intervals_auth_method")
    op.drop_column("users", "intervals_oauth_scope")
    op.drop_column("users", "intervals_access_token_encrypted")
