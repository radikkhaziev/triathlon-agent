"""phase1_webhook_data_capture

Revision ID: b3d4e5f6a7b8
Revises: u1b2c3d4e5f6
Create Date: 2026-05-06 20:00:00.000000

Phase 1 of ``docs/WEBHOOK_DATA_CAPTURE_SPEC.md`` — capture three webhook
payload blocks we currently drop on the floor:

1. ``activity_weather`` table — one row per outdoor activity that carried a
   weather block (``has_weather=true`` in the webhook). Indoor / virtual
   rides have no weather, so we keep this in a left-joined optional table
   instead of bloating ``activity_details`` with 12 nullable columns.
2. ``activity_details`` ADD COLUMN — rolling power model (CP/W'/pMax),
   rolling FTP + delta, CTL/ATL snapshot at activity time, carbs_used.
   Skipped: ``trimp`` (already exists on ``activity_details``) and
   ``achievements_json`` (redundant — ``activity_achievements`` table already
   persists the structured achievement records via ``u1b2c3d4e5f6``).
3. ``athlete_settings`` ADD COLUMN — MMP model (critical_power, w_prime,
   p_max, mmp_ftp), populated from ``SPORT_SETTINGS_UPDATED.mmp_model``.
   Run/Swim sport rows leave these NULL (Intervals only sends MMP for Ride).

Spec §7 calls for three separate migrations — bundled here because all three
land together, share the Phase 1 scope, and revert together if Phase 1 is
rolled back. Three separate revisions would only add chain entropy.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "b3d4e5f6a7b8"
down_revision: Union[str, None] = "u1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 1. activity_weather — outdoor weather block, optional left-join.
    op.create_table(
        "activity_weather",
        sa.Column(
            "activity_id",
            sa.String(),
            sa.ForeignKey("activities.id", ondelete="CASCADE"),
            primary_key=True,
        ),
        sa.Column("avg_temp_c", sa.Float(), nullable=True),
        sa.Column("min_temp_c", sa.Float(), nullable=True),
        sa.Column("max_temp_c", sa.Float(), nullable=True),
        sa.Column("avg_feels_like_c", sa.Float(), nullable=True),
        sa.Column("avg_wind_speed_mps", sa.Float(), nullable=True),
        sa.Column("avg_wind_gust_mps", sa.Float(), nullable=True),
        sa.Column("prevailing_wind_deg", sa.Integer(), nullable=True),
        sa.Column("headwind_pct", sa.Float(), nullable=True),
        sa.Column("tailwind_pct", sa.Float(), nullable=True),
        sa.Column("avg_clouds", sa.Float(), nullable=True),
        sa.Column("max_rain_mm", sa.Float(), nullable=True),
        sa.Column("max_snow_mm", sa.Float(), nullable=True),
        sa.Column(
            "captured_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )

    # 2. activity_details — rolling fitness model + CTL/ATL snapshots + carbs.
    with op.batch_alter_table("activity_details") as batch:
        batch.add_column(sa.Column("carbs_used", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("rolling_ftp", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("rolling_ftp_delta", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("rolling_w_prime", sa.Float(), nullable=True))
        batch.add_column(sa.Column("rolling_p_max", sa.Float(), nullable=True))
        batch.add_column(sa.Column("ctl_snapshot", sa.Float(), nullable=True))
        batch.add_column(sa.Column("atl_snapshot", sa.Float(), nullable=True))

    # 3. athlete_settings — MMP model from sport_settings (Ride only is populated).
    with op.batch_alter_table("athlete_settings") as batch:
        batch.add_column(sa.Column("critical_power", sa.Float(), nullable=True))
        batch.add_column(sa.Column("w_prime", sa.Float(), nullable=True))
        batch.add_column(sa.Column("p_max", sa.Float(), nullable=True))
        batch.add_column(sa.Column("mmp_ftp", sa.Integer(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("athlete_settings") as batch:
        batch.drop_column("mmp_ftp")
        batch.drop_column("p_max")
        batch.drop_column("w_prime")
        batch.drop_column("critical_power")

    with op.batch_alter_table("activity_details") as batch:
        batch.drop_column("atl_snapshot")
        batch.drop_column("ctl_snapshot")
        batch.drop_column("rolling_p_max")
        batch.drop_column("rolling_w_prime")
        batch.drop_column("rolling_ftp_delta")
        batch.drop_column("rolling_ftp")
        batch.drop_column("carbs_used")

    op.drop_table("activity_weather")
