"""add_activity_rpe

Revision ID: s9b0c1d2e3f4
Revises: r8a9b0c1d2e3
Create Date: 2026-04-13 17:00:00.000000

Adds Activity.rpe column for Borg CR-10 (1-10) post-workout subjective effort
rating. Stored only on activities; analytics read RPE via JOIN from
training_log.actual_activity_id. See docs/RPE_SPEC.md.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "s9b0c1d2e3f4"
down_revision: Union[str, None] = "r8a9b0c1d2e3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("activities", sa.Column("rpe", sa.Integer(), nullable=True))
    op.create_check_constraint(
        "ck_activities_rpe_range",
        "activities",
        "rpe IS NULL OR (rpe BETWEEN 1 AND 10)",
    )


def downgrade() -> None:
    op.drop_constraint("ck_activities_rpe_range", "activities", type_="check")
    op.drop_column("activities", "rpe")
