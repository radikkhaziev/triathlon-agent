"""Add mood_checkins table

Revision ID: b6424aa0f41e
Revises: f8a9b0c1d2e3
Create Date: 2026-03-26 10:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b6424aa0f41e"
down_revision: Union[str, None] = "f8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "mood_checkins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("timestamp", sa.DateTime(timezone=True), nullable=False),
        sa.Column("energy", sa.Integer(), nullable=True),
        sa.Column("mood", sa.Integer(), nullable=True),
        sa.Column("anxiety", sa.Integer(), nullable=True),
        sa.Column("social", sa.Integer(), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    op.drop_table("mood_checkins")
