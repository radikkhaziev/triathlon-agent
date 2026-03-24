"""Change activities.id from Integer to String

Intervals.icu activity IDs are strings with prefix (e.g. "i12345").

Revision ID: b4c8d2e3f5a6
Revises: a3b7c9d1e2f4
Create Date: 2026-03-24 20:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b4c8d2e3f5a6"
down_revision: Union[str, None] = "a3b7c9d1e2f4"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.alter_column(
        "activities",
        "id",
        existing_type=sa.Integer(),
        type_=sa.String(),
        existing_nullable=False,
        postgresql_using="id::text",
    )


def downgrade() -> None:
    op.execute("DELETE FROM activities WHERE id !~ '^[0-9]+$'")
    op.alter_column(
        "activities",
        "id",
        existing_type=sa.String(),
        type_=sa.Integer(),
        existing_nullable=False,
        postgresql_using="id::integer",
    )
