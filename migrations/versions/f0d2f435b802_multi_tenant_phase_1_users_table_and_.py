"""Multi-tenant Phase 1: add user_id to all data tables

Adds user_id FK to all 13 data tables (exercise_cards excluded — shared library),
migrates wellness/iqos_daily PK to autoincrement, drops activity_hrv.date
(redundant with activities.start_date_local), updates unique constraints for multi-tenant.

All existing data is backfilled with user_id=1 (owner).

Revision ID: f0d2f435b802
Revises: 268670b22cd7
Create Date: 2026-03-31 18:04:27.677193

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f0d2f435b802"
down_revision: Union[str, None] = "268670b22cd7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _add_user_id(table: str, *, index: bool = True) -> None:
    """Helper: add user_id column, backfill=1, set NOT NULL, FK, optional index."""
    op.add_column(table, sa.Column("user_id", sa.Integer(), nullable=True))
    op.execute(f"UPDATE {table} SET user_id = 1 WHERE user_id IS NULL")
    op.alter_column(table, "user_id", nullable=False)
    op.create_foreign_key(f"fk_{table}_user_id", table, "users", ["user_id"], ["id"])
    if index:
        op.create_index(f"ix_{table}_user_id", table, ["user_id"])


def _drop_user_id(table: str, *, index: bool = True) -> None:
    """Helper: reverse of _add_user_id."""
    if index:
        op.drop_index(f"ix_{table}_user_id", table)
    op.drop_constraint(f"fk_{table}_user_id", table, type_="foreignkey")
    op.drop_column(table, "user_id")


def upgrade() -> None:
    # Insert owner so FK backfill works (users table created in 268670b22cd7)
    import os

    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "0").replace("'", "''")
    op.execute(
        f"INSERT INTO users (id, chat_id, role, created_at, updated_at) "
        f"VALUES (1, '{chat_id}', 'owner', now(), now()) ON CONFLICT DO NOTHING"
    )

    # =====================================================================
    # 1. wellness: rename id → date, autoincrement PK, add user_id
    # =====================================================================
    op.drop_constraint("hrv_analysis_date_fkey", "hrv_analysis", type_="foreignkey")
    op.drop_constraint("rhr_analysis_date_fkey", "rhr_analysis", type_="foreignkey")

    op.drop_constraint("wellness_pkey", "wellness", type_="primary")
    op.alter_column("wellness", "id", new_column_name="date")
    op.add_column("wellness", sa.Column("id", sa.Integer(), autoincrement=True))
    op.execute("CREATE SEQUENCE IF NOT EXISTS wellness_id_seq OWNED BY wellness.id")
    op.execute("SELECT setval('wellness_id_seq', COALESCE((SELECT MAX(id) FROM wellness), 0) + 1, false)")
    op.execute("ALTER TABLE wellness ALTER COLUMN id SET DEFAULT nextval('wellness_id_seq')")
    op.execute("UPDATE wellness SET id = nextval('wellness_id_seq') WHERE id IS NULL")
    op.alter_column("wellness", "id", nullable=False)
    op.create_primary_key("wellness_pkey", "wellness", ["id"])

    _add_user_id("wellness")
    op.create_unique_constraint("uq_wellness_user_date", "wellness", ["user_id", "date"])

    # =====================================================================
    # 3. hrv_analysis: composite PK (user_id, date, algorithm)
    # =====================================================================
    op.drop_constraint("hrv_analysis_pkey", "hrv_analysis", type_="primary")
    _add_user_id("hrv_analysis", index=False)
    op.create_primary_key("hrv_analysis_pkey", "hrv_analysis", ["user_id", "date", "algorithm"])

    # =====================================================================
    # 4. rhr_analysis: composite PK (user_id, date)
    # =====================================================================
    op.drop_constraint("rhr_analysis_pkey", "rhr_analysis", type_="primary")
    _add_user_id("rhr_analysis", index=False)
    op.create_primary_key("rhr_analysis_pkey", "rhr_analysis", ["user_id", "date"])

    # =====================================================================
    # 5. scheduled_workouts
    # =====================================================================
    _add_user_id("scheduled_workouts")

    # =====================================================================
    # 6. activities
    # =====================================================================
    _add_user_id("activities")

    # =====================================================================
    # 7. activity_hrv: drop redundant date column
    # =====================================================================
    op.drop_column("activity_hrv", "date")

    # =====================================================================
    # 8. pa_baseline: user_id + new unique constraint
    # =====================================================================
    _add_user_id("pa_baseline")
    op.drop_constraint("uq_pa_baseline_activity_type_date", "pa_baseline", type_="unique")
    op.drop_index("ix_pa_baseline_type_date", "pa_baseline")
    op.create_unique_constraint("uq_pa_baseline_user_type_date", "pa_baseline", ["user_id", "activity_type", "date"])

    # =====================================================================
    # 9. mood_checkins
    # =====================================================================
    _add_user_id("mood_checkins")

    # =====================================================================
    # 10. iqos_daily: autoincrement PK + user_id + unique(user_id, date)
    # =====================================================================
    op.drop_constraint("iqos_daily_pkey", "iqos_daily", type_="primary")
    op.add_column("iqos_daily", sa.Column("id", sa.Integer(), autoincrement=True))
    op.execute("CREATE SEQUENCE IF NOT EXISTS iqos_daily_id_seq OWNED BY iqos_daily.id")
    op.execute("ALTER TABLE iqos_daily ALTER COLUMN id SET DEFAULT nextval('iqos_daily_id_seq')")
    op.execute("UPDATE iqos_daily SET id = nextval('iqos_daily_id_seq')")
    op.alter_column("iqos_daily", "id", nullable=False)
    op.create_primary_key("iqos_daily_pkey", "iqos_daily", ["id"])

    _add_user_id("iqos_daily")
    op.create_unique_constraint("uq_iqos_daily_user_date", "iqos_daily", ["user_id", "date"])

    # =====================================================================
    # 11. training_log
    # =====================================================================
    _add_user_id("training_log")

    # =====================================================================
    # 12. ai_workouts
    # =====================================================================
    _add_user_id("ai_workouts")

    # =====================================================================
    # 13. workout_cards
    # =====================================================================
    _add_user_id("workout_cards")


def downgrade() -> None:
    _drop_user_id("workout_cards")
    _drop_user_id("ai_workouts")
    _drop_user_id("training_log")

    # --- iqos_daily: restore date as PK ---
    op.drop_constraint("uq_iqos_daily_user_date", "iqos_daily", type_="unique")
    _drop_user_id("iqos_daily")
    op.drop_constraint("iqos_daily_pkey", "iqos_daily", type_="primary")
    op.drop_column("iqos_daily", "id")
    op.create_primary_key("iqos_daily_pkey", "iqos_daily", ["date"])

    _drop_user_id("mood_checkins")

    # --- pa_baseline: restore old unique ---
    op.drop_constraint("uq_pa_baseline_user_type_date", "pa_baseline", type_="unique")
    _drop_user_id("pa_baseline")
    op.create_unique_constraint("uq_pa_baseline_activity_type_date", "pa_baseline", ["activity_type", "date"])
    op.create_index("ix_pa_baseline_type_date", "pa_baseline", ["activity_type", "date"])

    # --- activity_hrv: restore date column ---
    op.add_column("activity_hrv", sa.Column("date", sa.String(), nullable=True))
    op.execute(
        """
        UPDATE activity_hrv SET date = a.start_date_local
        FROM activities a WHERE a.id = activity_hrv.activity_id
    """
    )
    op.alter_column("activity_hrv", "date", nullable=False)

    _drop_user_id("activities")
    _drop_user_id("scheduled_workouts")

    # --- rhr_analysis: restore PK ---
    op.drop_constraint("rhr_analysis_pkey", "rhr_analysis", type_="primary")
    _drop_user_id("rhr_analysis", index=False)
    op.create_primary_key("rhr_analysis_pkey", "rhr_analysis", ["date"])

    # --- hrv_analysis: restore PK ---
    op.drop_constraint("hrv_analysis_pkey", "hrv_analysis", type_="primary")
    _drop_user_id("hrv_analysis", index=False)
    op.create_primary_key("hrv_analysis_pkey", "hrv_analysis", ["date", "algorithm"])

    # --- wellness: restore date as PK ---
    op.drop_constraint("uq_wellness_user_date", "wellness", type_="unique")
    _drop_user_id("wellness")
    op.drop_constraint("wellness_pkey", "wellness", type_="primary")
    op.drop_column("wellness", "id")
    op.alter_column("wellness", "date", new_column_name="id")
    op.create_primary_key("wellness_pkey", "wellness", ["id"])

    # --- Restore FKs ---
    op.create_foreign_key("hrv_analysis_date_fkey", "hrv_analysis", "wellness", ["date"], ["id"])
    op.create_foreign_key("rhr_analysis_date_fkey", "rhr_analysis", "wellness", ["date"], ["id"])

    # --- Clean up owner placeholder ---
    op.execute("DELETE FROM users WHERE id = 1 AND chat_id = '0'")
