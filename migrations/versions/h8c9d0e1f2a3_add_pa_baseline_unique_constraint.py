"""Add unique constraint on pa_baseline(activity_type, date)

Revision ID: h8c9d0e1f2a3
Revises: g7b8c9d0e1f2
Create Date: 2026-03-29 23:30:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "h8c9d0e1f2a3"
down_revision: Union[str, None] = "g7b8c9d0e1f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Keep the newest row per (activity_type, date) before adding uniqueness.
    op.execute(
        sa.text(
            """
            DELETE FROM pa_baseline p
            USING (
                SELECT id
                FROM (
                    SELECT
                        id,
                        ROW_NUMBER() OVER (
                            PARTITION BY activity_type, date
                            ORDER BY id DESC
                        ) AS rn
                    FROM pa_baseline
                ) ranked
                WHERE ranked.rn > 1
            ) dups
            WHERE p.id = dups.id
            """
        )
    )

    op.create_unique_constraint(
        "uq_pa_baseline_activity_type_date",
        "pa_baseline",
        ["activity_type", "date"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_pa_baseline_activity_type_date",
        "pa_baseline",
        type_="unique",
    )
