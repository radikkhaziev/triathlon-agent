"""add last_synced_at to scheduled_workouts

Revision ID: 8996b20a4adf
Revises: d6e7f8a9b0c1
Create Date: 2026-03-25 12:50:26.069776

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8996b20a4adf"
down_revision: Union[str, None] = "d6e7f8a9b0c1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("scheduled_workouts", sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.drop_column("scheduled_workouts", "last_synced_at")
