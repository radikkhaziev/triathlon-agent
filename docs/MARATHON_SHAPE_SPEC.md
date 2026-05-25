# Marathon Shape — Runalyze-style basic endurance metric

> Status: 🟢 **Phase 1 + 1.5 + 1.6 shipped** — full alignment with Runalyze upstream (2026-05-14). Issue: [#95](https://github.com/radikkhaziev/triathlon-agent/issues/95).
>
> Implementation is the source of truth. This spec retains: declarative stance, formulas, decisions log, distance factors, ML integration, out-of-scope boundary.

## 1. Declarative stance

**Mirror Runalyze for the metric itself.** Marathon Shape is empirical and explicitly *not scientifically based* per the [upstream disclaimer](https://runalyze.com/help/article/marathon-shape) — *«rough estimate of whether you are sufficiently trained for a specific target distance»*. We have no basis to "improve" empirical-without-base. Authority belongs to upstream Runalyze.

**Any divergence between our widget and Runalyze UI on the same input data is a bug**, not a design choice — fix toward Runalyze.

**Our single value-add — §5 ML predicted time block.** Instead of porting Daniels' VDOT + empirical penalty, we use `predict_splits_with_ci` (XGBoost per-athlete, 90% CI, bias-corrected). Everything else (formulas, targets, scoring, UI %) mirrors Runalyze 1-to-1.

## 2. Problem

VO2max answers "how fast can you run", not "do you have the endurance for the distance". CTL shows fitness load, but not "are my legs ready for marathon/HM". Marathon Shape (Runalyze) = `% ratio` of weekly volume + long runs vs VO2max-derived targets. Marathon = 100%, HM ≈ 42.5%, 10K ≈ 17%.

Implementation lives in `data/marathon_shape.py` (pure formulas, no IO) + `api/routers/dashboard.py` `/api/marathon-shape` endpoint + `MarathonShapeWidget` in `webapp/src/pages/Progress.tsx` (under `PolarizationWidget` when `sport='run'`).

## 3. Formulas

Source: `inc/core/Calculation/BasicEndurance.php` in Runalyze.

```python
MINIMAL_EFFECTIVE_VO2MAX = 25.0
MIN_KM_FOR_LONGJOG = 13.0
DAYS_FOR_WEEK_KM = 182        # 26-week window for weekly volume
DAYS_FOR_WEEK_KM_MIN = 70     # clamp denominator if athlete trains <70 days
DAYS_FOR_LONGJOGS = 70        # 10-week window
PERCENTAGE_WEEK_KM = 0.67
PERCENTAGE_LONGJOGS = 0.33

target_weekly_km(V)  = max(V, 25) ** 1.135
target_longjog_km(V) = ln(max(V, 25) / 4) * 12 - 13       # SCORING-INTERNAL
required_shape_pct(distance_km) = distance_km ** 1.23
```

### Two distinct "long run target" values

| Name | Formula | Use |
|---|---|---|
| `target_longjog_km` | `ln(V/4)*12 − 13` | **scoring-internal** — only inside `((distance − 13) / target)²` quadratic term. **Not rendered.** |
| `displayed_target_long_run_km` | `ln(V/4)*12` | **UI-displayed** in Components block. For V=37 gives 26.7 km (matches Runalyze "Required Long Run" column). |

Identity: `displayed = scoring + 13` (where 13 = `MIN_KM_FOR_LONGJOG`).

### Shape calculation

```python
total_km_182d = sum(distance_km of runs in [today-182, today])
actual_training_days = (today - earliest_run_in_window).days + 1
days_for_week = clamp(actual_training_days, 70, 182)
actual_weekly = total_km_182d * 7 / days_for_week
weekly_ratio = actual_weekly / target_weekly_km(V)

longjog_score = sum over runs in [today-70, today] with distance >= 13:
    weight = 2 - (2/70) * days_ago    # 2 today, 0 at 70d
    weight * ((distance_km - 13) / target_longjog_km(V)) ** 2
longjog_ratio = (longjog_score * 7) / 70

shape_pct = 100 * (0.67 * weekly_ratio + 0.33 * longjog_ratio)
```

**`actual_training_days` note.** Runalyze PHP uses days since account creation. We use `today − earliest_run_in_182d_window`. For continuously training athletes always 182. **Pause side-effect:** after a 2+ month break, `actual_training_days` collapses, `clamp(70, 182)` kicks in at lower bound, `weekly_ratio` temporarily inflates ("shape grows fast after return"). Acceptable behavior — athlete gets credit for restoring volume, isn't penalized for past gap.

### Distance-adjusted Components targets (empirical factor table)

Runalyze "Other distances" table shows per-distance Weekly + Long Run targets. Exact scaling formula lives in `RunalyzePluginPanel_Rechenspiele/` PHP plugin — verified NOT trivially derivable from `V^1.135` (linear scaling `marathon × required/100` produces 25 km for HM, actual 33 km).

Empirical factor table calibrated on V≈37 screenshot (2026-05-14):

```python
_RUNALYZE_DISTANCE_FACTORS = {
    "10K":      {"weekly": 0.26, "longjog": None},
    "HM":       {"weekly": 0.57, "longjog": 0.69},
    "Marathon": {"weekly": 1.00, "longjog": 1.00},
}
```

Client-side in `Progress.tsx` (no JSON duplication). Components block recomputes effective targets on picker change without re-fetch.

**Calibration caveat:** factors derived from a single screenshot V≈37. Drift risk for other VO2max brackets unknown. If production reveals material divergence, escalate to D3.B (full PHP-port).

## 4. Decisions log — Runalyze alignment (Phase 1.6, 2026-05-14)

User-surfaced divergence: 10K Achieved 73% in Runalyze vs lower in our widget.

| Divergence | Decision | Why |
|---|---|---|
| **D1** — race-effort inclusion (`is_race=True` runs in `total_km_182d` + longjog scoring) | **D1.A — Include races** | Mirror Runalyze (§1 stance). User isn't competitive racer, taper-artefact concern minor. |
| **D2** — Long Run scoring vs displayed target in Components UI | **D2.A — Switch UI to displayed** (`ln(V/4)*12`) | Implementation bug, not philosophical. Scoring-internal denominator has no physical interpretation as UI label. |
| **D3** — distance-adjusted Components targets | **D3.A — Empirical factor table** | Quick fix on V≈37 calibration. D3.B (full PHP port) is Phase 2 if drift detected. D3.C (table redesign over picker) — rejected. |
| **D4** — activity type filter (`type == "Run"` strict) | **D4.A — Accept divergence** | Low impact (verified zero TrailRun/VirtualRun raw on user 1; `data/utils.py` normalizes ingestion). |

After Phase 1.6:
- **Scoring** (`shape_pct` formula) — correct, matches Runalyze PHP source. Untouched.
- **UI presentation** — full Runalyze alignment via race inclusion, displayed long-run target, distance-adjusted Components.

## 5. ML predicted time block (Phase 1.5)

### Why not Runalyze' VDOT

Runalyze computes "Prognosis" via Daniels' VDOT × empirical Hannes Christiansen shape penalty (saturating sigmoid, plateau ~+48% at very low achieved%, point data in `RunalyzePluginPanel_Rechenspiele/`). Universal (not personalized), no CI, requires PHP-plugin port. We use **`data/ml/race_predict.py:predict_splits_with_ci`** instead — XGBoost per-discipline trained on personal race history, 90% CI via bootstrap residuals, bias-corrected (β2). Stronger than empirical formula, no port required.

### Integration

For each distance (10000 / 21097 / 42195 m), endpoint calls `predict_splits_with_ci(user_id, mode='today', race_date=today.isoformat(), race_distance_run_m=dist_m)` **sequentially** (not `gather` — `_predict_one` is sync blocking even though wrapper is async; `gather` provides no parallelism). Total ~240 ms.

**`race_date` semantics in `mode='today'`** (verified in `_predict_one`): bias correction still applies via `bias_intercept + bias_slope × days_to_race`. Passing `today.isoformat()` → `days_to_race=0` → only intercept (~6 sec/km Run). **Do not pass future `race_date`** — slope term moves pred (~25 sec/km @ 150d), incorrect for "current shape" semantics.

`ModelNotTrained` / `ModelBelowAcceptance` → that distance returns `null`, others may be filled. Widget hides block when picker = null distance, renders for others.

### UI uncertainty awareness

When `(ci_high − ci_low) / center > 0.20` → footnote "model uncertainty high, limited race history". Threshold empirical, ~corresponds to <10 race samples. Without this, athlete with sparse race history sees "1:32 – 1:54" wide-band that looks useless without context.

### Caching

Redis cache key `marathon_shape_pred:{user_id}:{today_iso}`, TTL until midnight Belgrade. Graceful fallback on Redis disabled / unreachable / get-write errors — endpoint never breaks because of cache.

## 6. Edge cases

| Case | Behavior |
|---|---|
| `wellness.vo2max` NULL on week_end | Walk back up to 30 days; if still NULL → `shape_pct: null` for that week |
| Real VO2max <25 (de-trained / beginner) | Clamp to 25 in formulas. Response `vo2max_used` returns clamped value. |
| Run <13 km | Counted in `total_km_182d`, not in longjog scoring |
| TreadmillRun / TrailRun / VirtualRun | Already normalized to `type='Run'` at ingestion (`data/utils.py` + `ActivityDTO` validator) |
| All `shape_pct` NULL in 12-week chart | Chart hidden entirely |
| Cold-start (no race history) — all `predicted_times` null | Block hidden, rest of widget unaffected |

## 7. Out of scope (permanent boundaries)

- **MCP tool `get_marathon_shape`** — viewer-only widget, AI doesn't need it.
- **Morning report / prompt enrichment integration** — separate decision after widget validation.
- **Distance picker beyond 10K / HM / Marathon** — 70.3 run-leg = HM mathematically, IM-run = Marathon (formulas identical, no point duplicating). 5K / Ultra — only if explicitly requested.
- **Daniels' VDOT port + Hannes Christiansen penalty** — see §5 rationale. Our ML pipeline supersedes.
- **Per-discipline shape for bike / swim** — Runalyze does run only. See [`BIKE_READINESS_SPEC.md`](BIKE_READINESS_SPEC.md) for bike side.
- **Historical MS chart >12 weeks** — `weeks` param hard-capped at 24.
- **Race-day prediction mode** — widget shows "current form", not "where I'll be on race day". `predict_splits_with_ci(mode='race_day')` belongs on race-prep page.

## 8. Related

- [Runalyze BasicEndurance.php](https://github.com/Runalyze/Runalyze/blob/master/inc/core/Calculation/BasicEndurance.php) — upstream source of `shape_pct`, `target_weekly_km`, `target_longjog_km` formulas.
- [Marathon Shape help article](https://runalyze.com/help/article/marathon-shape) — UI screenshots, formal "not scientifically based" disclaimer, distance-adjusted target examples.
- `data/marathon_shape.py` — pure formulas, no IO.
- `api/routers/dashboard.py` `/api/marathon-shape` — endpoint + `_compute_predicted_times` Redis cache wrapper.
- `data/ml/race_predict.py:predict_splits_with_ci` — ML pipeline for Phase 1.5 Predicted time/pace.
- `webapp/src/pages/Progress.tsx` `MarathonShapeWidget` — placement + factor table + UI renderer.
- [`BIKE_READINESS_SPEC.md`](BIKE_READINESS_SPEC.md) — bike parallel widget.
