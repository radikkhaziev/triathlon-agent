"""Add users table for multi-tenant

Revision ID: 268670b22cd7
Revises: i9d0e1f2a3b4
Create Date: 2026-03-31 16:17:57.026203

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "268670b22cd7"
down_revision: Union[str, None] = "i9d0e1f2a3b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("chat_id", sa.String(), nullable=False),
        sa.Column("username", sa.String(), nullable=True),
        sa.Column("display_name", sa.String(), nullable=True),
        sa.Column("role", sa.String(), nullable=False, server_default="viewer"),
        sa.Column("athlete_id", sa.String(), nullable=True, unique=True),
        sa.Column("api_key_encrypted", sa.Text(), nullable=True),
        sa.Column("mcp_token", sa.String(64), nullable=True, unique=True),
        sa.Column("language", sa.String(5), nullable=False, server_default="ru"),
        sa.Column("preferred_model", sa.String(30), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("chat_id", name="uq_users_chat_id"),
    )


def downgrade() -> None:
    op.drop_table("users")
