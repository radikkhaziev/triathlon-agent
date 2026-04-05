"""Fix ai_workouts external_id: global unique → per-user unique

Replace unique constraint on external_id alone with composite
(user_id, external_id) to prevent cross-tenant overwrites.

Revision ID: n4c5d6e7f8a9
Revises: m3b4c5d6e7f8
Create Date: 2026-04-05 22:00:00.000000

"""

from typing import Sequence, Union

from alembic import op

revision: str = "n4c5d6e7f8a9"
down_revision: Union[str, None] = "m3b4c5d6e7f8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint("ai_workouts_external_id_key", "ai_workouts", type_="unique")
    op.create_unique_constraint("uq_ai_workouts_user_external", "ai_workouts", ["user_id", "external_id"])


def downgrade() -> None:
    op.drop_constraint("uq_ai_workouts_user_external", "ai_workouts", type_="unique")
    op.create_unique_constraint("ai_workouts_external_id_key", "ai_workouts", ["external_id"])
