"""Add composite index on training_log(user_id, date).

Revision ID: p6e7f8a9b0c1
Revises: o5d6e7f8a9b0
Create Date: 2026-04-09

Fixes BUG-001: get_training_log MCP timeout. The query filters on
(user_id, date) but only had individual indexes — PostgreSQL had to
scan one index then filter by the other column. Composite index
allows a single efficient index scan.
"""

from alembic import op

revision = "p6e7f8a9b0c1"
down_revision = "o5d6e7f8a9b0"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_index("ix_training_log_user_date", "training_log", ["user_id", "date"])


def downgrade() -> None:
    op.drop_index("ix_training_log_user_date", table_name="training_log")
