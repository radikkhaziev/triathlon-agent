"""add noise_reason + noise_scored_at to activities

Revision ID: aab8c9d0e1f2
Revises: b8c9d0e1f2a3
Create Date: 2026-05-12

Webhook-time noise classification — see `docs/ML_RACE_PROJECTION_SPEC.md` §6.4.

Three-state semantics:
  reason=NULL, scored_at=NULL → not classified yet (legacy / pre-backfill)
  reason=NULL, scored_at=<dt> → classified, clean signal (kept by ML)
  reason='run_*', scored_at=<dt> → noise (dropped from ML train-set)

TEXT not ENUM — adding a new value (Phase 2 `ride_*`) shouldn't need DDL.
Validation lives in Python `data.ml.noise_classifier.NoiseReason` Literal type.
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision: str = "aab8c9d0e1f2"
down_revision: str | None = "b8c9d0e1f2a3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("activities", sa.Column("noise_reason", sa.Text(), nullable=True))
    op.add_column("activities", sa.Column("noise_scored_at", sa.DateTime(timezone=True), nullable=True))
    op.create_index(
        "ix_activities_noise",
        "activities",
        ["user_id", "type", "noise_reason"],
    )


def downgrade() -> None:
    op.drop_index("ix_activities_noise", table_name="activities")
    op.drop_column("activities", "noise_scored_at")
    op.drop_column("activities", "noise_reason")
