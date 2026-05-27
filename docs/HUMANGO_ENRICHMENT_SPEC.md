# HumanGo workout enrichment — Intervals.icu structured-steps push

> Status: 🟢 **Shipped** — converter, detection, actor, and wiring all in place;
> end-to-end verified on user 1 (Ride 14/05, Swim 15/05, Run 24/05).
>
> Issue: [#375](https://github.com/radikkhaziev/triathlon-agent/issues/375).

---

## 1. Problem

HumanGo (third-party AI coach) pushes workouts to the athlete's Intervals.icu
calendar via shared-calendar sync. The events arrive with **plain-text
description only** — no structured `workout_doc.steps`. Consequences:

- Intervals.icu calendar UI shows a flat description; steps not visible as
  individual rows with target corridors.
- `get_workout_compliance` MCP tool needs structured steps to compute the
  HR/power/pace delta against planned targets — currently unusable on
  HumanGo events.
- Watch/FIT export from Intervals.icu has nothing structured to serialize,
  but Garmin sync still works via HumanGo's **direct** push to Garmin
  (separate path, untouched by this spec).

## 2. Solution overview

Detect HumanGo-sourced calendar events, parse the description into
`WorkoutStepDTO` list with the production HR/power/pace corridor schema
(`{units: "%lthr"|"%ftp"|"%pace", start, end}` — see
[`WORKOUT_ABSOLUTE_TARGETS_SPEC.md`](./WORKOUT_ABSOLUTE_TARGETS_SPEC.md) §12
Attempt 3b), and `PUT /events/{id}` back to Intervals.icu with the populated
`workout_doc.steps`. Description text is left untouched so the HumanGo→Garmin
sync path is never disturbed.

## 3. Detection

Three AND'd checks; any negative → skip the event entirely:

```python
def is_humango_event(description: str | None, workout_doc: dict | None) -> bool:
    if not description or "View on HumanGo" not in description:
        return False  # not a HumanGo event
    if "==========" not in description:
        return False  # HumanGo "rest day" / RPE-only — no structured steps to parse
    if workout_doc and workout_doc.get("steps"):
        return False  # already enriched (idempotency guard)
    return True
```

**Why `View on HumanGo`** — HumanGo embeds `View on HumanGo: https://app.humango.ai/myday?date=YYYY-MM-DD` (or `redirect.humango.ai` domain) in every calendar entry. The string is unique to their source — no other integration writes it. Verified against production descriptions in `tests/bot/test_workout_adapter.py` fixtures.

**Why `==========` separator** — defensive: HumanGo may push «rest day» entries with the View-link but no structured blocks. Without the separator the regex parser would return `[]` and the enrichment would push an empty `steps` list, surfacing zero benefit and risking validator rejection.

**Why idempotency check** — Dramatiq retries and scheduler-driven sweeps may invoke the actor multiple times for the same event. Skipping events that already have `steps` avoids:
- Spurious `PUT` requests against Intervals.icu (rate-limit budget).
- Overwriting our own previously-written steps with newly-recomputed ones (e.g. after the athlete updates their LTHR — old steps would be re-converted with new ratios, harmless but redundant).

## 4. Round-trip math

HumanGo computes its absolute values from the athlete's thresholds (max HR for Run, FTP for Ride, CSS for Swim). We don't know which thresholds HumanGo used, but it doesn't matter:

```
HumanGo:   pct_humango × threshold_humango = abs_value
We see:    abs_value
We compute: pct_ours = abs_value / threshold_ours × 100
We push:   {units: "%X", start: pct_ours_low, end: pct_ours_high}
Intervals: pct_ours × threshold_ours / 100 = abs_value  ✓ round-trip exact
```

Watches see the **original HumanGo absolute corridor** because Intervals translates `%X` back to absolute units using the same `threshold_ours` we divided by. The zone label may differ (HumanGo's Z2 from max HR ≠ our Z2 from LTHR), but Garmin shows raw bpm/watts/sec — what actually matters for the athlete.

## 5. Schema per sport

| Sport | HumanGo target | Threshold (`AthleteThresholdsDTO`) | Pushed units | Pushed shape |
|---|---|---|---|---|
| Run | `low/high: NN bpm` | `lthr_run` | `%lthr` | `{units: "%lthr", start, end}` |
| Run | `low/high: M:SS per km` | `threshold_pace_run` (sec/km) | `%pace` | `{units: "%pace", start, end}` |
| Ride | `low/high: NN W` | `ftp` | `%ftp` | `{units: "%ftp", start, end}` |
| Ride | `low/high: NN bpm` (rare HR-driven ride) | `lthr_bike` | `%lthr` | `{units: "%lthr", start, end}` |
| Swim | `low/high: M:SS per 100 meters` | `css` (Swim CSS, sec/100m) | `%pace` | `{units: "%pace", start, end}` |

**Run target precedence:** when a HumanGo block carries both HR _and_ pace (rare — empirically not observed in production but theoretically possible), HR wins. Rationale: HR is universal (treadmill / no-GPS works), pace is GPS-dependent and falls back to «open distance» on the watch when signal is poor. Pace-only blocks use the second Run row.

**Pace semantics:** HumanGo's `low` = slower pace (higher sec per unit distance), `high` = faster pace. Intervals' `%pace` is a **velocity ratio** (100 = threshold velocity, faster = higher %). The math is unit-agnostic — same formula for Run sec/km vs Swim sec/100m, just plug in the matching threshold:

```
start = threshold_sec / humango_low_sec × 100   # lower velocity bound
end   = threshold_sec / humango_high_sec × 100  # higher velocity bound
```

`start < end` holds because `low_sec > high_sec` (slower has more seconds). Verified empirically 2026-05-16 on Run event 109976490 (threshold_pace_run=287, HumanGo 5:46-6:33/km → `start: 73, end: 83`).

**Round-trip precision:** delta `≤ 1 sec` near threshold (interval/tempo paces), growing to **~2 sec** at slow warmup paces. Rounding-granularity = `threshold_sec / 100` per integer percent, so a 1% boundary maps to a larger sec/km step the further from threshold. Cosmetic only — watches use the % corridor verbatim, no FIT-export precision loss.

## 6. Cold-start fallback

If the relevant threshold(s) for the event's sport are missing, skip enrichment entirely. Per-sport accept-either logic:

| Sport | Required (any of) |
|---|---|
| Run | `lthr_run` OR `threshold_pace_run` |
| Ride | `ftp` OR `lthr_bike` |
| Swim | `css` |


- Log `info`: `"HumanGo enrichment skipped for user %d event %d: missing threshold for sport %s"`.
- Do NOT push absolute units as fallback — `WORKOUT_ABSOLUTE_TARGETS_SPEC` §12 Attempt 1 verified that `{units: "bpm", value, end}` flips FIT export into Lap-HR mode and the watch zone-clamps. Untested whether `{units: "bpm", start, end}` avoids that; defer to a future spike.
- Athlete still gets HumanGo's flat description in the calendar; Garmin sync via HumanGo direct path is unaffected.

## 7. Edge cases

| Case | Behaviour |
|---|---|
| Sport not in `{Run, Ride, Swim}` (e.g. `WeightTraining`, `Other`) | Skip — HumanGo doesn't push these structured anyway. |
| Repeat group inside description (`repeat N times`) | Honor — existing `parse_humango_description` already lifts these to `WorkoutStepDTO(reps=N, steps=[...])`. Sub-steps get the same %X conversion. |
| Step with `distance:` but no `duration:` (interval distance reps) | Preserve `distance` (meters), set `duration=0`. Intervals/Garmin handle distance-based steps natively. |
| Step with no parseable target (RPE-only, e.g. «easy effort») | Emit step with `duration` only — **but** if EVERY step in the workout ends up target-less, the workout is dropped entirely (return `None`). See «fail-closed» below. |
| Threshold value is zero / negative (corrupted DB) | Treat as missing; skip enrichment with the same log line as cold-start. |
| Description contains `View on HumanGo` but no `==========` (rest day) | Skip (detection check 2). |
| Event already has `workout_doc.steps` (we pushed earlier, or another integration did) | Skip (detection check 3, idempotency). |
| Athlete has only `lthr_run` set, HumanGo emits pace-only Run blocks (symmetric: only `threshold_pace_run` set + HR-only description) | Cold-start passes (one threshold ≥ 0), but `_humango_target_for_step` returns `None` for the missing pair → all steps come back target-less → **fail-closed** post-build guard drops the workout (`None`). Pushing target-less steps would lock out future re-enrichment via `is_humango_event` idempotency (which only checks `workout_doc.steps` non-empty, not target presence). Athlete must add the missing threshold; next sync retries. |
| Existing target-less workouts pushed before this fix landed | **Backfill gap, accepted:** `is_humango_event` idempotency returns `False` once `workout_doc.steps` is non-empty, regardless of target presence. Events enriched between «HumanGo parsing initial commit» and «Run pace fix» with pace-only descriptions stay target-less. Single-user, single-digit volume — no backfill planned. |
| Active-recovery bridge between two repeat groups (Swim only) | HumanGo's plain-text format has **no explicit «end of repeat scope» marker** — the parser previously kept eating blocks until the next `repeat`/`cooldown`. Threshold-25s swim sessions (and similar patterns) emit a long `interval` block labelled «Active recovery» (often «PB (Pull Buoy) Active recovery») as the **bridge between** two repeat groups, not inside each rep. Without a structural cue the bridge gets folded into the prior repeat and multiplied N×, inflating total swim distance by `(N−1) × bridge_distance`. **Fix:** `_humango_is_active_recovery_interval` checks the raw block text inside the repeat-collection loop — when an `interval`-typed block contains the phrase «active recovery» AND has `distance:` (no `duration:`) AND at least one sub-step has already been collected AND `sport == "Swim"`, terminate the repeat scope and let the bridge fall through to the outer level. **Swim-scoped** because the pattern was only observed empirically on Swim threshold sessions; distance-based Run/Ride workouts like `400m hard + 200m active recovery` are a legitimate inner-rep composition (Copilot review on PR #425). Empirically observed on event 112634380 (user 1, 2026-05-31 Swim «Threshold 25s-2»): 10× `(25m sprint + 20s rest)` + 200m PB active recovery + 40s rest + 10× `(25m sprint + 20s rest)`. Pre-fix: 3500m / 1h 49m. Post-fix: 1700m / ≈ 50min. Tests: `tests/bot/test_workout_adapter.py::test_active_recovery_bridge_not_swallowed_into_repeat` (positive), `::test_active_recovery_in_run_workout_stays_in_repeat` (sport-scope guard). |
| Repeat-scope overflow not caught by semantic heuristics (Swim only) | **Distance reconciliation via `_humango_shrink_repeats_to_announced`** (auto-fix, not just a log). HumanGo emits `total distance: N meters` (or `N km`) in the raw description header — we use that as ground truth. When the greedy parse overshoots announced by >20%, the algorithm iteratively shrinks repeat groups by lifting trailing sub-step pairs to outer level (drop interval+rest pair → repeat shrinks, dropped pair becomes a standalone outer-level step right after the repeat). Each iteration picks the single shrink across all multi-sub-step repeat groups that reduces drift the most; loop terminates when no shrink improves drift further (greedy minimization, NOT stop-at-tolerance — empirically «Tempo PB-3» needs two shrinks where the first lands within 20% but the second drops drift to 0%). Capped at `max_iter=10` defensively. **Odd-length guard:** repeat bodies with odd sub-step count are skipped — shrinking them would lift a partial pair (lone interval or lone rest) to outer level, breaking the `[interval, rest]` structural contract (per Copilot review on PR #427). **Canonical case:** event 112656655 (user 1, 2026-06-07 Swim «Tempo PB-3») — `repeat 2x` (warmup set) greedily ate 4 warmups+rests instead of intended 1, and `repeat 3x` (main set) ate 2 intervals+rests instead of intended 1. Greedy total 3400m, announced 2300m, shrink converges to 2300m exactly. **No-regression:** legit multi-block repeats where greedy already matches announced (e.g. 2026-05-29 «Catch Work in Open Water» drill session with 6-sub-step repeats matching 738m announced) skip shrink entirely. **Failure mode:** if no shrink can bring drift within tolerance (overshoot is in outer-level blocks, all repeats odd-length, or all repeats already minimal), keep greedy parse and log `WARNING` — slightly-wrong structured push beats falling back to flat description that watches can't structure. Tests: `test_shrink_reconciles_tempo_pb3_pattern`, `test_shrink_leaves_legit_multi_block_repeat_alone`, `test_shrink_unable_to_reconcile_keeps_greedy_and_warns`, `test_shrink_helper_returns_unchanged_when_within_tolerance`, `test_shrink_skips_odd_length_repeat_bodies`, `test_shrink_handles_km_units_in_announced`. |

## 8. Architecture

```
APScheduler tick → actor_user_scheduled_workouts (per active athlete)
  → IntervalsClient.get_events(oldest, newest)
  → ScheduledWorkout.save_bulk(events)
  → for each event matching `is_humango_event`:
       actor_enrich_humango_workout.send(user=UserDTO, event_id=…)

actor_enrich_humango_workout (Dramatiq)
  ├─ IntervalsClient.for_user(user) → get_event(event_id)  (fetch fresh, recheck idempotency)
  ├─ AthleteSettings.get_thresholds(user.id) → AthleteThresholdsDTO
  ├─ humango_to_intervals_steps(description, sport, thresholds)
  │    ├─ if thresholds missing → return None → actor logs + skips
  │    └─ else → list[WorkoutStepDTO]
  ├─ EventExDTO(workout_doc={"steps": […]})  (other fields untouched)
  └─ IntervalsClient.update_event(event_id, event_ex)
```

## 9. Phases

| Phase | Scope | Status |
|---|---|---|
| **1** | Spec + converter (`humango_to_intervals_steps`) + detection (`is_humango_event`) + unit tests | ✅ shipped |
| **2** | Actor (`actor_enrich_humango_workout`) + wiring from `actor_user_scheduled_workouts` + integration tests + tenant guard | ✅ shipped |

**Closed:** cron sweep keeps new HumanGo events covered; the regex parser handles every production sample observed to date. No further phases planned.

## 10. Out of scope

- **Garmin sync** — HumanGo→Garmin direct path stays untouched.
- **Editing HumanGo description text** — we only write `workout_doc.steps`. Description retained verbatim.
- **Push to HumanGo / coach feedback** — read-only on our side.
- **Workout adaptation** based on HumanGo plan — separate flow in `actor_compose_user_morning_report` via `parse_humango_description` (compliance path, untouched here).
- **Run pace targets on the compliance path** — `parse_humango_description` + `_parse_pace_target` only handle Swim sec/100m, NOT Run sec/km. When a Run pace-driven workout reaches morning-report compliance evaluation, pace targets are dropped → Δpace cannot be computed. Push path (this spec) is now sport-symmetric; compliance path is a known gap. Tracked separately when compliance UX needs it.

## 11. Acceptance criteria

- [ ] `is_humango_event` returns True only for HumanGo-sourced events with parseable structure and no existing steps.
- [ ] `humango_to_intervals_steps` round-trips a sample HumanGo description (Run/Ride/Swim) such that pushed `start`/`end` × threshold ≈ HumanGo's `low`/`high` within ±1 unit rounding near threshold (`±2` at slow warmup paces — see §4 «Round-trip precision»).
- [ ] Cold-start athlete (no thresholds) → converter returns `None`, actor skips with `info` log.
- [ ] Idempotent: re-running the actor on an already-enriched event is a no-op (no PUT to Intervals).
- [ ] Verified end-to-end on one real HumanGo event in production: open Intervals.icu calendar after enrichment → structured steps appear with target corridors.

## 12. Related

- [`WORKOUT_ABSOLUTE_TARGETS_SPEC.md`](./WORKOUT_ABSOLUTE_TARGETS_SPEC.md) — §12 Attempt 3b proved the `{units: "%X", start, end}` schema. Enrichment uses the exact same shape.
- [`INTERVALS_NATIVE_WORKOUT_FORMAT.md`](./INTERVALS_NATIVE_WORKOUT_FORMAT.md) — description-field grammar (not used here; we leave description as-is).
- [`ADAPTIVE_TRAINING_PLAN_SPEC.md`](./ADAPTIVE_TRAINING_PLAN_SPEC.md) — compliance check consumer (will benefit from structured steps).
