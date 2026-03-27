"""add last_synced_at to activities

Revision ID: d08531ba074d
Revises: 8996b20a4adf
Create Date: 2026-03-25 13:41:36.295797

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d08531ba074d"
down_revision: Union[str, None] = "8996b20a4adf"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("activities", sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("activities", "last_synced_at")
