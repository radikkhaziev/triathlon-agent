"""phase2_webhook_data_capture

Revision ID: c4d5e6f7a8b9
Revises: b3d4e5f6a7b8
Create Date: 2026-05-07 12:00:00.000000

Phase 2 of ``docs/WEBHOOK_DATA_CAPTURE_SPEC.md`` — three quality-of-life columns
on ``activity_details`` populated from the ACTIVITY_UPLOADED webhook payload:

- ``warmup_time_sec`` (INT) — ``activity.icu_warmup_time``
- ``cooldown_time_sec`` (INT) — ``activity.icu_cooldown_time``
- ``polarization_index`` (REAL) — ``activity.polarization_index``

All nullable, no default. Sourced only from the webhook (not the details API),
so historical activities stay NULL until a backfill PR lands. Spec §6 marks
backfill as deferred ("⚠ Не срочно").
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c4d5e6f7a8b9"
down_revision: Union[str, None] = "b3d4e5f6a7b8"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    with op.batch_alter_table("activity_details") as batch:
        batch.add_column(sa.Column("warmup_time_sec", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("cooldown_time_sec", sa.Integer(), nullable=True))
        batch.add_column(sa.Column("polarization_index", sa.Float(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("activity_details") as batch:
        batch.drop_column("polarization_index")
        batch.drop_column("cooldown_time_sec")
        batch.drop_column("warmup_time_sec")
