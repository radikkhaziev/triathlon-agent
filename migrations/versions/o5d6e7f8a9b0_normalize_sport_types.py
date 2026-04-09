"""Normalize sport types to canonical Ride/Run/Swim/Other.

Revision ID: o5d6e7f8a9b0
Revises: fb59b4bef745
Create Date: 2026-04-09

Data-only migration: no schema changes. Updates raw Intervals.icu activity
types (VirtualRide, GravelRide, TrailRun, etc.) to canonical types.
Also renames 'bike' → 'ride' in athlete_goals.per_sport_targets JSON.
"""

import sqlalchemy as sa
from alembic import op

revision = "o5d6e7f8a9b0"
down_revision = "fb59b4bef745"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()

    # --- activities.type ---
    conn.execute(
        sa.text(
            "UPDATE activities SET type = 'Ride' "
            "WHERE type IN ('VirtualRide','GravelRide','MountainBikeRide',"
            "'EBikeRide','EMountainBikeRide','TrackRide','Velomobile','Handcycle')"
        )
    )
    conn.execute(sa.text("UPDATE activities SET type = 'Run' WHERE type IN ('VirtualRun','TrailRun')"))
    conn.execute(sa.text("UPDATE activities SET type = 'Swim' WHERE type = 'OpenWaterSwim'"))
    conn.execute(
        sa.text(
            "UPDATE activities SET type = 'Other' "
            "WHERE type IS NOT NULL AND type NOT IN ('Ride','Run','Swim','Other')"
        )
    )

    # --- activity_hrv.activity_type ---
    conn.execute(
        sa.text(
            "UPDATE activity_hrv SET activity_type = 'Ride' "
            "WHERE activity_type IN ('VirtualRide','GravelRide','MountainBikeRide',"
            "'EBikeRide','EMountainBikeRide','TrackRide','Velomobile','Handcycle')"
        )
    )
    conn.execute(
        sa.text("UPDATE activity_hrv SET activity_type = 'Run' WHERE activity_type IN ('VirtualRun','TrailRun')")
    )

    # --- training_log.sport / actual_sport ---
    for col in ("sport", "actual_sport"):
        conn.execute(
            sa.text(
                f"UPDATE training_log SET {col} = 'Ride' "
                f"WHERE {col} IN ('VirtualRide','GravelRide','MountainBikeRide','EBikeRide')"
            )
        )
        conn.execute(sa.text(f"UPDATE training_log SET {col} = 'Run' WHERE {col} IN ('VirtualRun','TrailRun')"))
        conn.execute(sa.text(f"UPDATE training_log SET {col} = 'Swim' WHERE {col} = 'OpenWaterSwim'"))

    # --- scheduled_workouts.type ---
    conn.execute(
        sa.text(
            "UPDATE scheduled_workouts SET type = 'Ride' "
            "WHERE type IN ('VirtualRide','GravelRide','MountainBikeRide','EBikeRide')"
        )
    )
    conn.execute(sa.text("UPDATE scheduled_workouts SET type = 'Run' WHERE type IN ('VirtualRun','TrailRun')"))
    conn.execute(sa.text("UPDATE scheduled_workouts SET type = 'Swim' WHERE type = 'OpenWaterSwim'"))
    conn.execute(
        sa.text(
            "UPDATE scheduled_workouts SET type = 'Other' "
            "WHERE type IS NOT NULL AND type NOT IN ('Ride','Run','Swim','Other')"
        )
    )

    # --- athlete_goals.per_sport_targets: rename "bike" → "ride" in JSON ---
    conn.execute(
        sa.text(
            "UPDATE athlete_goals "
            "SET per_sport_targets = (per_sport_targets::jsonb - 'bike' "
            "|| jsonb_build_object('ride', per_sport_targets::jsonb->'bike'))::json "
            "WHERE per_sport_targets::jsonb ? 'bike'"
        )
    )


def downgrade() -> None:
    # Data migration — no automatic downgrade.
    pass
