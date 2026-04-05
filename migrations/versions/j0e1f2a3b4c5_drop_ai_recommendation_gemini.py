"""Drop ai_recommendation_gemini from wellness

Revision ID: j0e1f2a3b4c5
Revises: i9d0e1f2a3b4
Create Date: 2026-04-04 12:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "j0e1f2a3b4c5"
down_revision: Union[str, None] = "e9b364e82eb8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("wellness", "ai_recommendation_gemini")


def downgrade() -> None:
    op.add_column("wellness", sa.Column("ai_recommendation_gemini", sa.Text(), nullable=True))
