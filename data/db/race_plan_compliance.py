"""Per-leg post-race compliance metrics for race plans (PR3, spec §14).

Three metrics computed after the race finishes (manual trigger in PR3 — auto
on ``ACTIVITY_UPLOADED`` webhook is Phase 3 work):

- ``hr_compliance_pct`` — % of leg time the athlete's HR stayed at or below
  ``leg.hr_ceiling_bpm``.
- ``band_compliance_pct`` — % of leg time pace/power stayed inside the
  ``[low, cap]`` corridor.
- ``fueling_compliance_pct`` — ``min(actual_g_hr, plan_g_hr) / plan_g_hr * 100``.
  Requires manually-entered ``Race.carbs_consumed_g``.

Storage rationale: separate table (not a JSONB column on ``race_plans``)
because each compliance row is per-leg and lives independently of the plan
generation. A regen wouldn't recompute compliance; a new compute run for a
re-uploaded activity would. Schema-on-disk also makes future BI queries
(`AVG(hr_compliance_pct) GROUP BY leg_name`) trivial without JSONB extraction.

Reads scope by ``user_id`` (denormalised on the row) rather than chaining
through ``race_plans.user_id`` — defense-in-depth multi-tenant pattern,
mirrors the rest of the codebase (see ``data/db/race_plan.py``).
"""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal

from sqlalchemy import DateTime, ForeignKey, Integer, Numeric, String, Text, select, text
from sqlalchemy.orm import Mapped, mapped_column

from data.db.common import Base, Session
from data.db.decorator import dual


class RacePlanCompliance(Base):
    """One row per (race_plan, leg_name). Multiple computes for the same plan
    overwrite via service-side delete-then-insert (caller's choice — the table
    has no unique constraint on (race_plan_id, leg_name); a future actor that
    recomputes after activity-data corrections SHOULD upsert)."""

    __tablename__ = "race_plan_compliance"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    race_plan_id: Mapped[int] = mapped_column(Integer, ForeignKey("race_plans.id", ondelete="CASCADE"), nullable=False)
    # FK to races.id is nullable: a race may be re-tagged (Activity rebuilt) and
    # the original Race row deleted. Compliance row stays so historical analysis
    # survives the re-tag.
    race_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("races.id"), nullable=True)
    user_id: Mapped[int] = mapped_column(Integer, ForeignKey("users.id"), nullable=False)
    leg_name: Mapped[str] = mapped_column(String(32), nullable=False)
    hr_compliance_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    band_compliance_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    fueling_compliance_pct: Mapped[Decimal | None] = mapped_column(Numeric(5, 2), nullable=True)
    leg_duration_sec: Mapped[int | None] = mapped_column(Integer, nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=text("now()"))

    @classmethod
    @dual
    def save_for_leg(
        cls,
        *,
        user_id: int,
        race_plan_id: int,
        race_id: int | None,
        leg_name: str,
        hr_compliance_pct: Decimal | float | None,
        band_compliance_pct: Decimal | float | None,
        fueling_compliance_pct: Decimal | float | None,
        leg_duration_sec: int | None,
        notes: str | None,
        session: Session,
    ) -> RacePlanCompliance:
        """Insert one compliance row for one leg.

        Float-to-Decimal coercion is automatic — pass either, store as Numeric.
        Returns the inserted row (id populated post-flush). Caller is
        responsible for deduplication when re-computing — there's no unique
        constraint on (race_plan_id, leg_name).
        """
        row = cls(
            user_id=user_id,
            race_plan_id=race_plan_id,
            race_id=race_id,
            leg_name=leg_name,
            hr_compliance_pct=hr_compliance_pct,
            band_compliance_pct=band_compliance_pct,
            fueling_compliance_pct=fueling_compliance_pct,
            leg_duration_sec=leg_duration_sec,
            notes=notes,
        )
        session.add(row)
        session.commit()
        session.refresh(row)
        return row

    @classmethod
    @dual
    def get_for_race_plan(
        cls,
        race_plan_id: int,
        *,
        user_id: int,
        session: Session,
    ) -> list[RacePlanCompliance]:
        """All compliance rows for a plan, scoped by ``user_id``.

        Defense-in-depth: even though ``race_plan_id`` already implies a
        single tenant, the WHERE clause filters by ``user_id`` so a leaked
        plan_id can't surface compliance from another athlete's row (mirrors
        ``RacePlan.get_latest_for_race``).
        """
        result = session.execute(
            select(cls).where(cls.race_plan_id == race_plan_id, cls.user_id == user_id).order_by(cls.id.asc())
        )
        return list(result.scalars().all())
