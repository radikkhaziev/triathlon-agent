"""Add user_backfill_state table

Revision ID: a7e4c1b8d9f0
Revises: 9f31b33412f8
Create Date: 2026-04-21 09:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a7e4c1b8d9f0"
down_revision: Union[str, None] = "9f31b33412f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_backfill_state",
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False, server_default="running"),
        sa.Column("period_days", sa.Integer(), nullable=False),
        sa.Column("oldest_dt", sa.Date(), nullable=False),
        sa.Column("newest_dt", sa.Date(), nullable=False),
        sa.Column("cursor_dt", sa.Date(), nullable=False),
        sa.Column("chunks_done", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_step_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("user_id"),
    )


def downgrade() -> None:
    op.drop_table("user_backfill_state")
