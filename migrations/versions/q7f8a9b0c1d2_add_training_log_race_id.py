"""add_training_log_race_id

Revision ID: q7f8a9b0c1d2
Revises: 883ad6dc7748
Create Date: 2026-04-13 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "q7f8a9b0c1d2"
down_revision: Union[str, None] = "883ad6dc7748"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("training_log", sa.Column("race_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_training_log_race_id",
        "training_log",
        "races",
        ["race_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_training_log_race_id", "training_log", type_="foreignkey")
    op.drop_column("training_log", "race_id")
