# Training Data Hygiene

How an athlete's logging discipline shapes what the ML models can learn. Server-side filters can clean noise up to a point — beyond that, the data quality is decided by what shows up in `activities` and how it's tagged. This document captures what helps, what hurts, and why.

Aimed at: athletes onboarding, coaches reviewing data quality, AI skill that generates training plans.

---

## Why this matters — the 2026-05-12 calibration story

The race-projection ML pipeline ships with a Run «recovery jog» filter that drops activities where Z1 ≥ 70% of recorded HR time **AND** TSS < 40 (see `docs/ML_RACE_PROJECTION_SPEC.md` §6.3, `docs/knowledge/decoupling.md` for related zone semantics). The two conditions are needed in tandem — calibration across 5 real athletes:

| Cohort | Profile | Pre-filter R² | After filter | What we learned |
|---|---|---|---|---|
| Athlete A | 60% Z1-dominated runs, jogs avg TSS ~25 | **−75** | **−0.06** | Filter correctly removed noise — almost all his Run-typed activities were unstructured recovery jogs |
| Athlete B (pro, 80/20 base) | 58% Z1-dominated runs, base avg TSS ~70 | **+0.44** | **+0.44** (only with TSS gate) | Structured Z1-base **is** signal; without the TSS gate, filter would have killed his model down to R²=+0.04 |
| Athlete C (you) | Mixed walks, recoveries, occasional structured | **+0.35** | **+0.22** | Filter helped but ceiling is data-quality limited — long Z1 walks with TSS 40-60 fit «structured base» bucket but aren't actual training signal |

**Bottom line:** the model can only learn what the data shows. If the data shows «athlete ran 7:30/km @ HR 110 while walking the dog», the model concludes «at this state, this athlete runs 7:30/km» — and the next race-day prediction inherits that contamination.

---

## What hurts the signal

| Source | Effect on the model | Avoidance |
|---|---|---|
| **Walks logged as Run** | TSS 40-60 in Z1, but pace 7:00-8:00/km at HR ~100 doesn't reflect aerobic capacity. Model averages it with real easy runs and learns slower pace at low HR. | Use `type=Other` or `type=Walk` in Intervals. Better: don't sync casual walks to Intervals at all. |
| **Optical-HR recovery sessions** | Wrist-based HR drifts ±10-15 bpm at low intensities → `target_hr` feature becomes noisy. | Always wear a chest strap on Run activities you want to count. Watch optical is fine for *measuring* recovery but the *recording* should use the strap. |
| **TrailRun mixed with Run** | At the same HR, trail pace is 30-60 sec/km slower. Mixing them makes the model see conflicting signal. | Use `type=TrailRun` in Intervals.icu (project filter already drops it from race-projection train-set). |
| **Brick run (Run after Bike)** | Pace 20-40 sec/km slower at the same HR due to bike fatigue — but the feature set doesn't know about yesterday's bike. | Tag with `sub_type=BRICK` so future training-prep features can use it. Currently treated as regular Run — adds noise. |
| **Race rehearsal at race pace** | High intensity, race-grade signal — **but** without `is_race` marker the model bundles it with normal training. | Use Intervals «race» calendar tag for upcoming rehearsals (project propagates `is_race=True`). |
| **Fasted morning vs fueled afternoon** | Pace 10-15 sec/km slower fasted at the same HR. Modifier the model can't see. | Run quality sessions at consistent fueling state. Recovery / easy runs are less sensitive. |
| **One-off ultra-long or one-off short** | Outliers move the regression weight disproportionately when n is small (<100 examples). | Build volume gradually. Avoid «I did 32 km today» one-shot before season — keep the typical training distribution coherent. |

---

## What gives clean signal

| Session type | Why model loves it | Suggested cadence |
|---|---|---|
| **Steady-state cruise** ≥30 min at one intensity | One HR = one pace = clean point in feature space | 1-2× per week |
| **Tempo / threshold** 20-40 min main set | Upper part of HR-pace curve — teaches the model the «edge» of capacity | 1× per week (in build phase) |
| **Structured intervals** with main set HR/pace target | High intensity + intent label = high-signal data | 1× per week alternating with tempo |
| **Race rehearsal at race pace** | `is_race=True` feature + race-grade speed = the gold-standard data point | 1× per 4-6 weeks in peak phase |
| **Long run on a familiar loop** | Elevation/wind effects average out over many repeats | 1× per week, same route when possible |
| **Race itself** (`is_race=True`) | Maximum signal — actual race pace at actual race state | 3-6× per season |

---

## How models use these signals

The project trains three model families that all eat the same `activities` table:

- **Race projection** (`docs/ML_RACE_PROJECTION_SPEC.md`) — learns pace/power vs state for each discipline. Every clean steady-state session reduces MAE; every noisy walk widens CI. The TSS-gated z1-filter catches the obvious junk; everything else is data hygiene.
- **Progression model** (`docs/TRAINING_PROGRESSION_SPEC.md`) — learns Δ EF (efficiency factor week-over-week). Already filters `decoupling < 10%` and `Z2-only` (see `docs/knowledge/decoupling.md`), so it's less sensitive to walks, but mixed-intensity sessions still confuse it.
- **HRV / recovery prediction** (`docs/ML_HRV_PREDICTION_SPEC.md`, planned) — driven by wellness rows, not activities directly, but TSS distribution feeds CTL/ATL → CTL stale on walks-as-Run misclassification.

The same data that makes the model better makes the **AI training generator** (chat path) better too — Claude sees the same `get_activities` output and reasons about it. Clean data → cleaner workout suggestions.

### Persisted noise tag — Phase 1.6 (2026-05-12)

The server-side noise check now persists its verdict on each Run activity as
`activities.noise_reason` — set once by the webhook pipeline immediately after
zone/pace data arrives from Intervals.icu, not re-evaluated on every retrain.
See `docs/ML_RACE_PROJECTION_SPEC.md` §6.4.

| `noise_reason` | Meaning |
|---|---|
| `NULL` (with `noise_scored_at` set) | Checked at webhook time, signal kept |
| `'run_walk'` | Walk-paced low-HR Run — `pace > threshold_pace × 1.6 AND avg_hr < lthr × 0.65` |
| `'run_recovery_jog'` | Z1 ≥ 70% AND TSS < 40 — fluff recovery session |
| `NULL` (with `noise_scored_at = NULL`) | Legacy row before Phase 1.6 — falls back to live check |

Two consequences for the athlete:

1. **The chat assistant can talk about it.** Claude reads the `noise_reason`
   on each activity, so it can say «I see 3 of your last 7 Runs are tagged as
   recovery jogs — they're excluded from race-projection retrain. If you want
   them counted, switch to `Walk` type in Intervals for casual walks and keep
   `Run` only for actual running». This wasn't possible when the filter lived
   only in the retrain pipeline.

2. **Tag is sticky.** Once webhook classifies a row, retrain reuses that verdict.
   If you re-edit an activity in Intervals (rename, change RPE), the noise tag
   doesn't get re-evaluated — zone times and pace don't change on rename, so
   the classification stays valid. Force re-classification only via the manual
   backfill CLI: `python -m cli classify-noise --user-id=N --since-days=365`.

**Thresholds are personalized.** The walk-vs-jog gate uses YOUR LTHR and
threshold_pace from Intervals.icu — not a global constant. A sub-3 marathoner's
recovery jog at 6:00/km @ HR 130 is fine (above their 5:36/km × 0.65×LTHR floor);
a 60yo athlete's 7:30/km recovery jog at HR 120 is also fine (above their 8:00/km
× 0.65×LTHR floor). Walks below these floors get tagged automatically — the
system adapts to your physiology.

---

## Practical checklist per athlete

Mark off what's currently true; the more boxes, the better the model's prediction.

- [ ] Chest strap (not optical) on every quality session.
- [ ] Walks tagged `type=Other` or `Walk`, not Run.
- [ ] TrailRun strictly separate from Run (project already filters).
- [ ] 1× tempo / threshold per week (build phase) or 1× per 2 weeks (base phase).
- [ ] Long runs roughly same route when possible.
- [ ] Race rehearsals marked as race in Intervals calendar.
- [ ] No one-off casual walks pushed to Intervals as exercise.
- [ ] Recovery jogs <40 TSS — let the filter drop them, don't fight it.
- [ ] Indoor rides marked indoor (project uses `is_indoor` feature).

---

## What this is NOT

- **Not a training plan.** That's the ATP / coaching layer — see `docs/ADAPTIVE_TRAINING_PLAN_SPEC.md`. This document is about what to *log* to Intervals.icu and how, not what workouts to do.
- **Not a fix for low-volume athletes.** With n < 100 activities the model is fundamentally under-fed; data hygiene improves a tight ceiling but doesn't lift it. Build volume gradually.
- **Not a fix for atypical training.** If you're rehabbing, traveling, or in offseason, the model will be uncertain regardless — that's correctly surfaced as wider CI in race-projection envelope (`docs/ML_RACE_PROJECTION_SPEC.md` §10.2).

---

## When to expect improvements

Models retrain weekly (Sun 16:00 Belgrade, migrating to Sun 03:00 — see `docs/ML_RACE_PROJECTION_SPEC.md` §12.2). Cleaner data starts feeding next Sunday's retrain. A 4-6 week window of disciplined logging typically shows up as:

- Run MAE: 30+ sec/km → 15-20 sec/km
- R² on Run: ~0.3 → 0.5+
- CI width on race-day prediction: ±5 min → ±2-3 min on a half-marathon

Below the `acceptance floor` (R² ≥ 0.20 for Run/Ride, ≥ 0.05 for Swim — see `docs/ML_RACE_PROJECTION_SPEC.md` §14.2 «Quality gate») the model returns `available=False, reason=model_below_acceptance` — that's the safety net while data quality is climbing.

---

## Related

- `docs/ML_RACE_PROJECTION_SPEC.md` §6.3 — implementation of the recovery-jog filter (the server-side companion to this athlete-side guide).
- `docs/knowledge/decoupling.md` — Pa:Hr drift filter used by progression model.
- `docs/knowledge/aerobic-efficiency.md` — EF metric the progression model targets.
- `docs/ADAPTIVE_TRAINING_PLAN_SPEC.md` — coaching layer that generates the workouts. The chat skill that surfaces this knowledge to athletes lives at the intersection.
