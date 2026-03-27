"""Add average_hr to activities table

Revision ID: c5d6e7f8a9b0
Revises: b4c8d2e3f5a6
Create Date: 2026-03-24

"""

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision = "c5d6e7f8a9b0"
down_revision = "b4c8d2e3f5a6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("activities", sa.Column("average_hr", sa.Float(), nullable=True))


def downgrade() -> None:
    op.drop_column("activities", "average_hr")
