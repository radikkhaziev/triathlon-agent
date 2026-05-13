"""add paired_event_id to activities

Revision ID: d2e3f4a5b6c7
Revises: c1d2e3f4a5b6
Create Date: 2026-05-13 22:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "d2e3f4a5b6c7"
down_revision: Union[str, None] = "c1d2e3f4a5b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Intervals.icu's native planned-vs-actual pairing. References
    # `scheduled_workouts.id`, stored as plain Integer with NO foreign key —
    # Intervals rotates / deletes calendar events independently of activities,
    # so a hard FK would either cascade-delete activities on plan cleanup or
    # 23503 on sync. Webhook source: ACTIVITY_UPLOADED `paired_event_id`.
    op.add_column("activities", sa.Column("paired_event_id", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("activities", "paired_event_id")
