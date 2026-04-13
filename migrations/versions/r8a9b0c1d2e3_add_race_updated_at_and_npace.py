"""add_race_updated_at_and_normalized_pace

Revision ID: r8a9b0c1d2e3
Revises: q7f8a9b0c1d2
Create Date: 2026-04-13 00:10:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "r8a9b0c1d2e3"
down_revision: Union[str, None] = "q7f8a9b0c1d2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("races", sa.Column("normalized_pace_sec_km", sa.Float(), nullable=True))
    op.add_column(
        "races",
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )


def downgrade() -> None:
    op.drop_column("races", "updated_at")
    op.drop_column("races", "normalized_pace_sec_km")
