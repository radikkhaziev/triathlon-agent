"""replace users.primary_sport (String) with users.sports (JSON list)

Revision ID: y5f6a7b8c9d0
Revises: x4e5f6a7b8c9
Create Date: 2026-05-08 22:00:00.000000

`primary_sport` was a single-value String(20) ("triathlon"|"run"|"ride"|...) but
in practice only one place reads it (data/db/athlete.py:179) and nothing
downstream branches on its value. To support athletes who don't do all three
disciplines (e.g. runners-only) we move to a multi-select list of sports
({"swim","ride","run"}).

All existing rows are dropped without backfill — every user passes through
the new SportsPicker gate on next webapp open. Explicit UX-verification
choice (USER_SPORTS_SPEC §4 + 2026-05-08 confirmation): the gate is the
source of truth going forward, and re-prompting the seven legacy users
who had `primary_sport` populated is acceptable. Until they pass through
the gate the morning-report ramp-test path falls back to the conservative
``["Run"]`` only — see ``tasks/utils.user_ramp_sports``.

Downgrade is symmetrically destructive: ``users.sports`` is dropped and the
restored ``primary_sport`` column comes back NULL for everyone. Athletes
will need to re-pick on the next webapp open if we ever roll back. Worth
noting before triggering ``alembic downgrade`` in any environment that has
seen real picker traffic.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "y5f6a7b8c9d0"
down_revision: Union[str, None] = "x4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("users", "primary_sport")
    op.add_column("users", sa.Column("sports", sa.JSON(), nullable=True))


def downgrade() -> None:
    op.add_column("users", sa.Column("primary_sport", sa.String(20), nullable=True))
    op.drop_column("users", "sports")
