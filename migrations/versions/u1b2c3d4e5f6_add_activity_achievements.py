"""add_activity_achievements

Revision ID: u1b2c3d4e5f6
Revises: t0a1b2c3d4e5
Create Date: 2026-04-26 09:00:00.000000

Persists per-activity achievements from the ``ACTIVITY_ACHIEVEMENTS`` webhook
(power PRs, FTP changes, future milestone types). The webhook arrives ~60s
after ``ACTIVITY_UPLOADED`` with an enriched ``activity`` dict containing
``icu_achievements[]`` plus ``icu_rolling_ftp``/``delta``/``ctl``/``atl``.

Forward-compat: ``type`` is a free string and full raw dict is preserved in
``extra`` (JSON) so new Intervals.icu achievement types are stored without
schema changes. Indexed reads happen by user+activity (UNIQUE) and by user+type
+date (for "list my power PRs" / "list my FTP increases" queries from social-
share UI).

See ``docs/INTERVALS_WEBHOOKS_RESEARCH.md`` ACTIVITY_ACHIEVEMENTS section.
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "u1b2c3d4e5f6"
down_revision: Union[str, None] = "t0a1b2c3d4e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "activity_achievements",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("user_id", sa.Integer(), sa.ForeignKey("users.id"), nullable=False),
        sa.Column(
            "activity_id",
            sa.String(),
            sa.ForeignKey("activities.id", ondelete="CASCADE"),
            nullable=False,
        ),
        # Intervals.icu achievement id (e.g. "ps0_5" for 5-second power, or
        # synthetic "ftp_change" for our FTP_CHANGE row).
        sa.Column("achievement_id", sa.String(), nullable=False),
        # Type tag — BEST_POWER, FTP_CHANGE, ... — kept as free string for
        # forward-compat with new Intervals.icu types we haven't seen yet.
        sa.Column("type", sa.String(), nullable=False),
        # Numeric value (watts for power PRs, ftp value for FTP_CHANGE, ...).
        sa.Column("value", sa.Float(), nullable=True),
        # Duration in seconds for time-based PRs (5s, 1m, 5m, ...). NULL when
        # achievement isn't time-bounded (FTP_CHANGE, milestones).
        sa.Column("secs", sa.Integer(), nullable=True),
        # Snapshot of fitness state when the achievement happened — useful for
        # social-share captions ("5s power PR @ CTL=18, FTP=208W").
        sa.Column("ftp_at_time", sa.Integer(), nullable=True),
        sa.Column("ctl_at_time", sa.Float(), nullable=True),
        # Index span inside the activity stream where the PR occurred (start/
        # end indices + value). Useful for stream-clip generation.
        sa.Column("point_data", sa.JSON(), nullable=True),
        # Raw original achievement dict from Intervals.icu webhook payload.
        # Lossless storage for forward-compat — new fields surface here without
        # a migration. ``FTP_CHANGE`` rows store ``{"delta": <icu_rolling_ftp_delta>}``.
        sa.Column("extra", sa.JSON(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.UniqueConstraint(
            "user_id",
            "activity_id",
            "achievement_id",
            name="uq_activity_achievements_user_activity_achievement",
        ),
    )
    op.create_index(
        "ix_activity_achievements_user_id",
        "activity_achievements",
        ["user_id"],
    )
    # Composite for "list my power PRs" / "list my FTP changes" social-share
    # queries — typically scoped by user + type + recency.
    op.create_index(
        "ix_activity_achievements_user_type_created",
        "activity_achievements",
        ["user_id", "type", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_activity_achievements_user_type_created", table_name="activity_achievements")
    op.drop_index("ix_activity_achievements_user_id", table_name="activity_achievements")
    op.drop_table("activity_achievements")
