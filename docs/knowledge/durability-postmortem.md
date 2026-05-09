# Durability Classifier — Postmortem

> Long-effort fatigue decomposition (CV / metabolic / neuromuscular). Adopted 2026-05-09 from
> Tymewear three-system fatigue framework. **Abandoned 2026-05-09** после прогона Phase 0 на population data.

**Status:** ❌ closed.
**Theory note:** [`durability.md`](durability.md) — переживает закрытие, остаётся standalone reference.

---

## Что планировалось

Классификация длинных тренировок (≥75-90 мин, steady-state) по типу усталости к концу: **CV-drift only / metabolic erosion / neuromuscular / mixed / severe / none**. Использовалось бы:
- В morning / weekly report как «3 из 5 длинных за месяц — metabolic erosion → проверь fueling».
- В AI чат как фактический контекст через MCP tool `get_durability_trend`.
- В webapp на `/activity/:id` — бейдж + объяснение что именно деградировало.
- (Long-term) feed в race-projection model как фича.

Каркас — Tymewear three-system framework (HR drift / V̇E / BR), переложенный на наши прокси (HR / power-or-pace / cadence + RPE + decoupling). Полный design — data model, dispatcher wiring, MCP tool, decision tree, четыре фазы — см. файл `docs/DURABILITY_CLASSIFIER_SPEC.md` в git history (revision до 2026-05-09 cleanup).

---

## Phase 0 findings (2026-05-09)

Скрипт-калибратор (см. ниже) прогнан на 113 eligible activities (90 после full-pipeline filter), 7 athlete'ов, 12 мес истории.

**Acceptance checks:**
- C1 warm-up confound: r=-0.072 (n=32) — passed. `WARMUP_OFFSET_S=600` валиден.
- C2 homogeneity band: p99=0.187 — passed. `±0.20` покрывает 100%.
- A3 rpe_excess validity: r=-0.190 (n=48) — failed. Drop `rpe_excess` из decision tree.

**Категоризация по spec §5 thresholds:**

```
Bike (n=56)                  Run (n=34)
  severe              1.8%     severe              2.9%
  metabolic_erosion   0.0%   ←  metabolic_erosion   5.9%
  cv_drift_only      16.1%     cv_drift_only      11.8%
  neuromuscular      33.9%     neuromuscular       8.8%
  mixed              12.5%     mixed              26.5%
  none               35.7%     none               44.1%
```

**Sanity vs Rothschild 2025 (n=51, 2.5h @ 90% VT1):**
- HR drift mean: ours 2.9% vs paper 6.3%
- Power drop mean: ours 0.84% vs paper 10.0% — drастически меньше.

---

## Почему закрыли

1. **`metabolic_erosion` (главная диагностика по spec'у) — 0% fire-rate на bike (0/56).** Пороги `POWER_DROP_RED=7% AND DECOUPLING_HIGH=10%` одновременно не срабатывают ни у одной активности. Mean power drop 0.84% против 10% baseline у Rothschild. Возможные причины: 90 мин слишком коротко (Rothschild делал 2.5h), пороги жёсткие, или наша когорта реально не доходит до метаболической erosion на текущих длинах. В любой интерпретации — самая важная ветка дерева для нас бесполезна.

2. **RPE сигнал blind из-за data quality.** Dominant contributor (user 62 = 60% всех eligible) ставит `rpe=1` на ВСЕ свои runs (видимо default или игнорит запрос). Получается systematic `rpe_excess` ≈ -8…-10 — не отражает реального усилия. Acceptance check A3 формально провален, реальная причина — плохой сбор RPE, не плохая корреляция метрики. Лечится сбором (mandatory prompt? non-default values?), не алгоритмом.

3. **`neuromuscular` доминирует на bike (34%) подозрительно.** Cadence_drop trigger без gradient-фильтра с большой вероятностью ловит outdoor uphill как neuromuscular fatigue (низкая cadence на подъёмах — естественная адаптация, не утомление). False-positive heavy.

4. **Sample skew.** user 62 = 60% всех eligible. Калибровка де-факто на одном спортсмене.

**Cost-benefit:** Phase 1-3 = ~неделя кода ради feature, главная диагностика которой не fire'ит, вторая false-positive-heavy, третья (RPE) blind. ROI отрицательный.

---

## Triggers для возврата к идее

Перепрогнать Phase 0 скрипт (см. ниже) если **любое** из:

- Сбор RPE стал надёжным: mandatory prompt в боте + non-default values от 3+ юзеров на регулярной основе.
- 6+ мес накопления → ≥150 eligible activities с диверсифицированными контрибьюторами (не один user 62 = 60%).
- Появился gradient-фильтр на cadence (для exclusion uphill false-positives на bike).
- ИЛИ юзеры начали регулярно делать ≥2.5h base rides — длины при которых metabolic erosion физиологически проявляется (Rothschild baseline).

При триггере: re-run скрипт ниже → если `metabolic_erosion` fire-rate стал 5-15% → открываем Phase 1; иначе закрываем повторно.

---

## Phase 0 analysis script (preserved for replay)

Read-only анализатор. Запускается через `poetry run python` или внутри Docker. Не имеет side-effects — только пишет markdown-отчёт в stdout / файл. Сохранён ниже целиком, чтобы при необходимости replay'а можно было собрать обратно из этого markdown'а без копания в git history.

**Usage (when needed):**

```bash
# Save the code block below to scripts/durability_phase0.py, then:
poetry run python scripts/durability_phase0.py --out /tmp/durability_phase0_report.md

# Or inside Docker:
docker compose run --rm api python scripts/durability_phase0.py
```

**Source:**

```python
"""Durability classifier — Phase 0 calibration analysis.

Read-only. Computes per-activity deltas + distributions + acceptance checks
across all active athletes' bike + run history. Output is a markdown report
to stdout intended for spec review.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from collections import defaultdict
from dataclasses import dataclass
from typing import Any

from sqlalchemy import text

from data.db.common import get_sync_session

# --- Config -----------------------------------------------------------------

WARMUP_OFFSET_S = 600
RIDE_MIN_DURATION_S = 5400
RUN_MIN_DURATION_S = 4500
VI_MAX = 1.10
Z12_MIN_FRAC = 0.70
WARMUP_POWER_FRAC = 0.55
HOMOGENEITY_BAND = 0.20
LATE_WINDOW_MIN_SEC = 600
HISTORY_DAYS = 365

HR_DRIFT_HIGH = 5.0
POWER_DROP_YELLOW = 3.0
POWER_DROP_RED = 7.0
PACE_DROP_YELLOW = 4.0
PACE_DROP_RED = 8.0
CADENCE_DROP_YELLOW_BIKE = 2.0
CADENCE_DROP_RED_BIKE = 5.0
CADENCE_DROP_YELLOW_RUN = 2.0
CADENCE_DROP_RED_RUN = 5.0
DECOUPLING_HIGH = 10.0
RPE_EXCESS_HIGH = 1.5

ROTHSCHILD_HR_DRIFT_PCT = 6.3
ROTHSCHILD_POWER_DROP_PCT = 10.0


# --- Helpers ----------------------------------------------------------------


def expected_rpe(intensity_factor: float | None) -> float | None:
    """Linear map: IF 0.65 → RPE 4, 0.75 → 5, 0.85 → 6, 0.95 → 7.

    Intervals.icu stores IF as percentage (e.g. 75.0 for 0.75), confirmed by
    DB sweep. Convert before applying decimal-IF formula.
    """
    if intensity_factor is None:
        return None
    if_decimal = intensity_factor / 100.0
    return 4.0 + (if_decimal - 0.65) * 10.0


def time_weighted_mean(values: list[float | None], weights: list[float]) -> float | None:
    pairs = [(v, w) for v, w in zip(values, weights) if v is not None and w]
    if not pairs:
        return None
    total = sum(w for _, w in pairs)
    if total <= 0:
        return None
    return sum(v * w for v, w in pairs) / total


def percentile(values: list[float], p: float) -> float | None:
    cleaned = sorted(v for v in values if v is not None and not math.isnan(v))
    if not cleaned:
        return None
    idx = (len(cleaned) - 1) * p / 100.0
    lo = int(idx)
    hi = min(lo + 1, len(cleaned) - 1)
    frac = idx - lo
    return cleaned[lo] * (1 - frac) + cleaned[hi] * frac


def pearson_r(xs: list[float | None], ys: list[float | None]) -> tuple[float | None, int]:
    pairs = [(x, y) for x, y in zip(xs, ys) if x is not None and y is not None]
    n = len(pairs)
    if n < 3:
        return None, n
    mean_x = sum(x for x, _ in pairs) / n
    mean_y = sum(y for _, y in pairs) / n
    num = sum((x - mean_x) * (y - mean_y) for x, y in pairs)
    den_x = math.sqrt(sum((x - mean_x) ** 2 for x, _ in pairs))
    den_y = math.sqrt(sum((y - mean_y) ** 2 for _, y in pairs))
    if den_x == 0 or den_y == 0:
        return None, n
    return num / (den_x * den_y), n


def _ge(value: float | None, threshold: float) -> bool:
    return value is not None and value >= threshold


def _lt(value: float | None, threshold: float) -> bool:
    return value is not None and value < threshold


# --- Per-activity computation -----------------------------------------------


@dataclass
class Row:
    activity_id: str
    user_id: int
    sport: str
    date: str
    moving_time: int
    rpe: int | None
    intensity_factor: float | None
    decoupling: float | None
    ftp: int | None
    warmup_sec: int | None
    np_late_over_early_abs: float | None
    hr_drift_pct: float | None
    power_drop_pct: float | None
    pace_drop_pct: float | None
    cadence_drop_pct: float | None
    decoupling_pct: float | None
    rpe_excess: float | None
    early_n: int
    late_n: int
    classification: str
    ineligible_reason: str | None


def window_means(intervals: list[dict], t_start: float, t_end: float) -> tuple[dict | None, int]:
    incl = []
    for iv in intervals:
        iv_st = iv.get("start_time")
        iv_dur = iv.get("moving_time") or 0
        if iv_st is None or iv_dur <= 0:
            continue
        mid = iv_st + iv_dur / 2
        if t_start <= mid < t_end:
            incl.append(iv)
    if not incl:
        return None, 0
    weights = [iv["moving_time"] for iv in incl]
    return {
        "hr": time_weighted_mean([iv.get("average_heartrate") for iv in incl], weights),
        "power": time_weighted_mean(
            [iv.get("weighted_average_watts") or iv.get("average_watts") for iv in incl],
            weights,
        ),
        "cadence": time_weighted_mean([iv.get("average_cadence") for iv in incl], weights),
        "speed_mps": time_weighted_mean([iv.get("average_speed") for iv in incl], weights),
    }, len(incl)


def detect_warmup_sec(intervals: list[dict], ftp: int | None) -> int | None:
    if not ftp:
        return None
    threshold = ftp * WARMUP_POWER_FRAC
    for iv in intervals:
        wap = iv.get("weighted_average_watts") or iv.get("average_watts")
        if wap is not None and wap >= threshold:
            return int(iv.get("start_time") or 0)
    return None


def classify(row: Row) -> str:
    cad_red = CADENCE_DROP_RED_BIKE if row.sport == "Ride" else CADENCE_DROP_RED_RUN
    cad_yellow = CADENCE_DROP_YELLOW_BIKE if row.sport == "Ride" else CADENCE_DROP_YELLOW_RUN

    primary_drop = row.power_drop_pct if row.sport == "Ride" else row.pace_drop_pct
    primary_yellow = POWER_DROP_YELLOW if row.sport == "Ride" else PACE_DROP_YELLOW
    primary_red = POWER_DROP_RED if row.sport == "Ride" else PACE_DROP_RED

    red_signals = sum([
        _ge(primary_drop, primary_red),
        _ge(row.hr_drift_pct, HR_DRIFT_HIGH),
        _ge(row.cadence_drop_pct, cad_red),
        _ge(row.decoupling_pct, DECOUPLING_HIGH),
    ])
    if red_signals >= 3:
        return "severe"

    if _ge(primary_drop, primary_red) and _ge(row.decoupling_pct, DECOUPLING_HIGH):
        return "metabolic_erosion"

    if (
        _ge(row.hr_drift_pct, HR_DRIFT_HIGH)
        and _lt(primary_drop, primary_yellow)
        and _lt(row.cadence_drop_pct, cad_yellow)
    ):
        return "cv_drift_only"

    if _ge(row.cadence_drop_pct, cad_red):
        return "neuromuscular"
    if _ge(row.cadence_drop_pct, cad_yellow) and _ge(row.rpe_excess, RPE_EXCESS_HIGH):
        return "neuromuscular"

    yellow_count = sum([
        _ge(primary_drop, primary_yellow),
        _ge(row.hr_drift_pct, HR_DRIFT_HIGH),
        _ge(row.cadence_drop_pct, cad_yellow),
        _ge(row.decoupling_pct, DECOUPLING_HIGH),
    ])
    if yellow_count >= 2:
        return "mixed"

    return "none"


def compute_row(raw: dict[str, Any]) -> Row:
    base_args = (
        raw["activity_id"], raw["user_id"], raw["sport"], raw["date"],
        raw["moving_time"], raw["rpe"], raw["intensity_factor"],
        raw["decoupling"], raw["ftp"],
    )
    null_metrics = (None,) * 8 + (0, 0)

    intervals_doc = raw["intervals_doc"]
    if not intervals_doc:
        return Row(*base_args, *null_metrics, "ineligible", "no_intervals")
    if isinstance(intervals_doc, str):
        intervals_doc = json.loads(intervals_doc)
    icu_intervals = intervals_doc.get("icu_intervals") or []
    if len(icu_intervals) < 4:
        return Row(*base_args, *null_metrics, "ineligible", "no_intervals")

    moving_time = raw["moving_time"]
    early_start = WARMUP_OFFSET_S
    early_end = WARMUP_OFFSET_S + 0.25 * (moving_time - WARMUP_OFFSET_S)
    late_start = moving_time * 0.75
    late_end = moving_time

    if late_end - late_start < LATE_WINDOW_MIN_SEC:
        return Row(*base_args, *null_metrics, "ineligible", "late_window_too_short")

    early, early_n = window_means(icu_intervals, early_start, early_end)
    late, late_n = window_means(icu_intervals, late_start, late_end)
    if not early or not late or early_n == 0 or late_n == 0:
        return Row(*base_args, *((None,) * 8 + (early_n, late_n)),
                   "inconclusive", "empty_window")

    sport = raw["sport"]
    np_ratio_abs = None
    if sport == "Ride" and early.get("power") and late.get("power"):
        np_ratio_abs = abs(late["power"] / early["power"] - 1)
    elif sport == "Run" and early.get("speed_mps") and late.get("speed_mps"):
        np_ratio_abs = abs(late["speed_mps"] / early["speed_mps"] - 1)

    homogeneity_failed = np_ratio_abs is not None and np_ratio_abs > HOMOGENEITY_BAND

    hr_drift_pct = None
    if early.get("hr") and late.get("hr"):
        hr_drift_pct = (late["hr"] / early["hr"] - 1) * 100

    power_drop_pct = None
    if sport == "Ride" and early.get("power") and late.get("power"):
        power_drop_pct = (1 - late["power"] / early["power"]) * 100

    pace_drop_pct = None
    if sport == "Run" and early.get("speed_mps") and late.get("speed_mps"):
        pace_early = 1000 / early["speed_mps"]
        pace_late = 1000 / late["speed_mps"]
        pace_drop_pct = (pace_late / pace_early - 1) * 100

    cadence_drop_pct = None
    if early.get("cadence") and late.get("cadence"):
        cadence_drop_pct = (1 - late["cadence"] / early["cadence"]) * 100

    decoupling = raw["decoupling"]
    decoupling_pct = abs(decoupling) if decoupling is not None else None

    rpe_exp = expected_rpe(raw["intensity_factor"])
    rpe = raw["rpe"]
    rpe_excess = rpe - rpe_exp if (rpe is not None and rpe_exp is not None) else None

    warmup_sec = detect_warmup_sec(icu_intervals, raw["ftp"])

    metrics = (
        warmup_sec, np_ratio_abs, hr_drift_pct, power_drop_pct,
        pace_drop_pct, cadence_drop_pct, decoupling_pct, rpe_excess,
        early_n, late_n,
    )
    if homogeneity_failed:
        return Row(*base_args, *metrics, "ineligible", "non_homogeneous_intensity")

    row = Row(*base_args, *metrics, "", None)
    row.classification = classify(row)
    return row


# --- Data loading -----------------------------------------------------------


ELIGIBILITY_SQL = text("""
    WITH long_acts AS (
      SELECT a.id          AS activity_id,
             a.user_id     AS user_id,
             a.type        AS sport,
             SUBSTRING(a.start_date_local, 1, 10) AS date,
             a.moving_time AS moving_time,
             a.rpe         AS rpe,
             d.intensity_factor AS intensity_factor,
             d.variability_index AS vi,
             d.decoupling AS decoupling,
             d.hr_zone_times AS hr_zone_times,
             d.intervals AS intervals_doc,
             ats.ftp AS ftp
      FROM activities a
      JOIN activity_details d ON d.activity_id = a.id
      LEFT JOIN athlete_settings ats ON ats.user_id = a.user_id AND ats.sport = 'Ride'
      WHERE a.is_race = false
        AND a.start_date_local >= :since
        AND (
          (a.type = 'Ride' AND a.moving_time >= :ride_min)
          OR (a.type = 'Run' AND a.moving_time >= :run_min)
        )
        AND d.decoupling IS NOT NULL
        AND d.variability_index IS NOT NULL
        AND d.variability_index <= :vi_max
        AND jsonb_typeof(d.hr_zone_times::jsonb) = 'array'
        AND jsonb_array_length(d.hr_zone_times::jsonb) >= 2
        AND jsonb_typeof(d.intervals::jsonb -> 'icu_intervals') = 'array'
        AND jsonb_array_length(d.intervals::jsonb -> 'icu_intervals') >= 4
    )
    SELECT *
    FROM long_acts la
    WHERE (
      ((la.hr_zone_times::jsonb->>0)::numeric + (la.hr_zone_times::jsonb->>1)::numeric)
      / NULLIF((SELECT SUM(v::numeric) FROM jsonb_array_elements_text(la.hr_zone_times::jsonb) v), 0)
    ) >= :z12_min
    ORDER BY la.user_id, la.date
""")


def fetch_rows(since: str) -> list[Row]:
    out: list[Row] = []
    with get_sync_session() as session:
        result = session.execute(
            ELIGIBILITY_SQL,
            {
                "since": since,
                "ride_min": RIDE_MIN_DURATION_S,
                "run_min": RUN_MIN_DURATION_S,
                "vi_max": VI_MAX,
                "z12_min": Z12_MIN_FRAC,
            },
        )
        for raw in result.mappings():
            out.append(compute_row(dict(raw)))
    return out


# --- Reporting --------------------------------------------------------------


DELTA_FIELDS_BIKE = [
    ("hr_drift_pct", "HR drift %"),
    ("power_drop_pct", "Power drop %"),
    ("cadence_drop_pct", "Cadence drop %"),
    ("decoupling_pct", "Decoupling %"),
    ("rpe_excess", "RPE excess"),
]
DELTA_FIELDS_RUN = [
    ("hr_drift_pct", "HR drift %"),
    ("pace_drop_pct", "Pace drop %"),
    ("cadence_drop_pct", "Cadence drop %"),
    ("decoupling_pct", "Decoupling %"),
    ("rpe_excess", "RPE excess"),
]


def render(rows: list[Row], since: str) -> str:
    out: list[str] = []
    p = out.append

    p("# Durability Classifier — Phase 0 Report")
    p("")
    p(f"_Generated from durability_phase0.py on data since `{since}`._")
    p("")

    eligible = [r for r in rows if r.classification not in ("ineligible", "inconclusive")]
    bike = [r for r in eligible if r.sport == "Ride"]
    run = [r for r in eligible if r.sport == "Run"]

    p("## 1. Eligibility breakdown per user")
    p("")
    p("| user_id | bike | run | total |")
    p("|---|---|---|---|")
    by_user: dict[int, dict[str, int]] = defaultdict(lambda: {"Ride": 0, "Run": 0})
    for r in eligible:
        by_user[r.user_id][r.sport] += 1
    for uid in sorted(by_user, key=lambda u: -(by_user[u]["Ride"] + by_user[u]["Run"])):
        b = by_user[uid]["Ride"]
        rn = by_user[uid]["Run"]
        p(f"| {uid} | {b} | {rn} | {b + rn} |")
    p(f"| **total** | **{len(bike)}** | **{len(run)}** | **{len(eligible)}** |")
    p("")

    ineligible_reasons: dict[str, int] = defaultdict(int)
    for r in rows:
        if r.classification in ("ineligible", "inconclusive"):
            ineligible_reasons[r.ineligible_reason or r.classification] += 1
    if ineligible_reasons:
        p("_Excluded post-eligibility-SQL: "
          + ", ".join(f"`{k}`={v}" for k, v in sorted(ineligible_reasons.items())) + "._")
        p("")

    def render_distribution(label: str, group: list[Row], fields: list[tuple[str, str]]) -> None:
        if not group:
            p(f"_No eligible **{label}**._")
            p("")
            return
        p(f"### {label} (n={len(group)})")
        p("")
        p("| signal | min | p25 | median | p75 | p95 | max | n |")
        p("|---|---|---|---|---|---|---|---|")
        for fname, flabel in fields:
            vals = [getattr(r, fname) for r in group]
            non_null = [v for v in vals if v is not None]
            if not non_null:
                p(f"| {flabel} | — | — | — | — | — | — | 0 |")
                continue
            p(
                f"| {flabel} "
                f"| {min(non_null):.2f} "
                f"| {percentile(non_null, 25):.2f} "
                f"| {percentile(non_null, 50):.2f} "
                f"| {percentile(non_null, 75):.2f} "
                f"| {percentile(non_null, 95):.2f} "
                f"| {max(non_null):.2f} "
                f"| {len(non_null)} |"
            )
        p("")

    p("## 2. Distributions per delta")
    p("")
    render_distribution("Bike", bike, DELTA_FIELDS_BIKE)
    render_distribution("Run", run, DELTA_FIELDS_RUN)

    p("## 3. Acceptance checks")
    p("")
    p("### C1 — warm-up confound")
    pairs = [(r.warmup_sec, r.hr_drift_pct) for r in bike if r.warmup_sec is not None]
    r_c1, n_c1 = pearson_r([w for w, _ in pairs], [d for _, d in pairs])
    verdict = "→ insufficient data" if r_c1 is None else (
        "→ |r| < 0.3, WARMUP_OFFSET_S=600 валиден" if abs(r_c1) < 0.3
        else "→ |r| ≥ 0.3, поднять WARMUP_OFFSET_S или перейти на median(early-25-50%)"
    )
    p(f"`corr(hr_drift_pct, warmup_sec)` n={n_c1}: r = "
      + ("None" if r_c1 is None else f"{r_c1:.3f}"))
    p(verdict)
    p("")

    p("### C2 — homogeneity threshold")
    homo = [r.np_late_over_early_abs for r in eligible if r.np_late_over_early_abs is not None]
    if homo:
        ps = {q: percentile(homo, q) for q in (50, 75, 90, 95, 99)}
        p(f"`|np_late/np_early - 1|` n={len(homo)}: "
          + ", ".join(f"p{q}={ps[q]:.3f}" for q in (50, 75, 90, 95, 99)))
        cov = sum(1 for v in homo if v <= HOMOGENEITY_BAND) * 100 / len(homo)
        p(f"→ band ±{HOMOGENEITY_BAND:.2f} покрывает ~{cov:.0f}%")
    p("")

    p("### A3 — rpe_excess validity")
    pairs = [(r.rpe_excess, r.cadence_drop_pct) for r in eligible
             if r.rpe_excess is not None and r.cadence_drop_pct is not None]
    if pairs:
        r_a3, n_a3 = pearson_r([x for x, _ in pairs], [y for _, y in pairs])
        verdict = (
            "→ |r| ≥ 0.3, оставить как confirmer"
            if r_a3 is not None and abs(r_a3) >= 0.3
            else "→ |r| < 0.3, drop rpe_excess из decision tree"
        )
        p(f"`corr(rpe_excess, cadence_drop_pct)` n={n_a3}: r = "
          + ("None" if r_a3 is None else f"{r_a3:.3f}"))
        p(verdict)
    p("")

    p("## 4. Spec §5 thresholds applied → categorization")
    p("")
    for label, group in [("Bike", bike), ("Run", run)]:
        if not group:
            continue
        counts: dict[str, int] = defaultdict(int)
        for r in group:
            counts[r.classification] += 1
        p(f"### {label} (n={len(group)})")
        p("")
        p("| category | count | pct |")
        p("|---|---:|---:|")
        for cat in ("severe", "metabolic_erosion", "cv_drift_only", "neuromuscular", "mixed", "none"):
            c = counts.get(cat, 0)
            p(f"| {cat} | {c} | {c * 100 / len(group):.1f}% |")
        p("")

    p("## 5. Sanity vs Rothschild 2025")
    p("")
    if bike:
        bike_hr = [r.hr_drift_pct for r in bike if r.hr_drift_pct is not None]
        bike_pwr = [r.power_drop_pct for r in bike if r.power_drop_pct is not None]
        if bike_hr:
            p(f"- HR drift mean: ours `{sum(bike_hr) / len(bike_hr):.2f}%` "
              f"vs Rothschild `{ROTHSCHILD_HR_DRIFT_PCT}%`")
        if bike_pwr:
            p(f"- Power drop mean: ours `{sum(bike_pwr) / len(bike_pwr):.2f}%` "
              f"vs Rothschild `{ROTHSCHILD_POWER_DROP_PCT}%`")
    p("")

    return "\n".join(out)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.strip().split("\n")[0])
    parser.add_argument("--out", help="write report to file (default: stdout)")
    parser.add_argument("--days", type=int, default=HISTORY_DAYS,
                        help=f"days back (default {HISTORY_DAYS})")
    args = parser.parse_args()

    from datetime import datetime, timedelta, timezone
    since = (datetime.now(timezone.utc) - timedelta(days=args.days)).strftime("%Y-%m-%d")

    rows = fetch_rows(since)
    report = render(rows, since)

    if args.out:
        with open(args.out, "w") as f:
            f.write(report)
        print(f"Report written to {args.out} ({len(rows)} rows analyzed)", file=sys.stderr)
    else:
        sys.stdout.write(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
```
