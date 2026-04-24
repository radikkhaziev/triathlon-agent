"""Add user_facts table

Revision ID: b8d1c4e7f0a3
Revises: a7e4c1b8d9f0
Create Date: 2026-04-22 10:00:00.000000

Schema per docs/USER_CONTEXT_SPEC.md §3. No ``confidence`` column — that's
Phase 2 (added only when the extractor ships and actual confidence ranges
are known). No ``superseded_by`` — ``deactivated_reason='topic_cap'`` +
the ``created_at DESC`` index answer cap-chain queries without it.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b8d1c4e7f0a3"
down_revision: Union[str, None] = "a7e4c1b8d9f0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "user_facts",
        sa.Column("id", sa.Integer(), autoincrement=True, nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("topic", sa.String(length=64), nullable=False),
        sa.Column("fact", sa.String(length=300), nullable=False),
        sa.Column("fact_language", sa.String(length=5), nullable=True),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deactivated_reason", sa.String(length=32), nullable=True),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )

    # Partial index over active rows only — activation status is the dominant
    # filter in every read (list_facts, cap eviction, prompt injection). Keeping
    # the index compact (only non-deactivated rows) cuts its size by ~90 % once
    # audit trail grows.  ``created_at DESC`` is included so cap-eviction's
    # "find oldest in this (user, topic)" resolves index-only.
    op.create_index(
        "ix_user_facts_active",
        "user_facts",
        ["user_id", "topic", sa.text("created_at DESC")],
        unique=False,
        postgresql_where=sa.text("deactivated_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("ix_user_facts_active", table_name="user_facts")
    op.drop_table("user_facts")
