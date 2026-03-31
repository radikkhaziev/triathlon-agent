"""Seed user 2 (IlnarKa) athlete data: age, primary_sport, goal, settings

Revision ID: m3b4c5d6e7f8
Revises: l2a3b4c5d6e7
Create Date: 2026-04-05 21:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "m3b4c5d6e7f8"
down_revision: Union[str, None] = "l2a3b4c5d6e7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    conn = op.get_bind()
    user_exists = conn.execute(sa.text("SELECT 1 FROM users WHERE id = 2")).fetchone()
    if not user_exists:
        return

    # User 2: age, primary_sport
    conn.execute(sa.text("UPDATE users SET age = 43, primary_sport = 'run' WHERE id = 2"))

    # Goal: Half Marathon
    conn.execute(
        sa.text(
            """
            INSERT INTO athlete_goals (
                user_id, category, event_name, event_date, sport_type,
                disciplines, ctl_target, per_sport_targets, is_active
            )
            VALUES (2, 'RACE_A', 'Half Marathon', '2026-05-26', 'run', '["Run"]'::json, 40, '{"run": 40}'::json, true)
        """
        )
    )


def downgrade() -> None:
    op.execute(sa.text("DELETE FROM athlete_goals WHERE user_id = 2"))
    op.execute(sa.text("UPDATE users SET age = NULL, primary_sport = NULL WHERE id = 2"))
