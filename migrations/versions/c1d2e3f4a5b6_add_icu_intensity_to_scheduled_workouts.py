"""add icu_intensity + icu_training_load to scheduled_workouts

Revision ID: c1d2e3f4a5b6
Revises: f30f590945b4
Create Date: 2026-05-13 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c1d2e3f4a5b6"
down_revision: Union[str, None] = "f30f590945b4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Two Intervals.icu top-level event fields that drive the workout-detail
    # page header strip («Нагрузка» + «Интенсивность»), neither of which lives
    # inside `workout_doc`:
    #
    # - `icu_intensity` — 0-100 percent (NOT 0-1 decimal — diverges from
    #   TrainingPeaks IF convention).
    # - `icu_training_load` — TSS-equivalent integer. The workout_doc-internal
    #   `strain_score` is always None for planned events (Intervals only
    #   populates it for completed activities), so we capture the top-level
    #   value instead.
    #
    # Both pure passthrough from `GET /athlete/{id}/events` — never computed.
    op.add_column("scheduled_workouts", sa.Column("icu_intensity", sa.Float(), nullable=True))
    op.add_column("scheduled_workouts", sa.Column("icu_training_load", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("scheduled_workouts", "icu_training_load")
    op.drop_column("scheduled_workouts", "icu_intensity")
