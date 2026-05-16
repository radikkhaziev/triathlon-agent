# Planned Training-Load (TSS) on Pushed Workouts

> Status: 🟢 **Phase 1+2 merged-pending** (PR #398, branch `dev`) — AC-1 ✅,
> AC-2 ✅; AC-3 pending one live `CALENDAR_UPDATED`→`FITNESS_UPDATED` cycle
> post-deploy. Phase 3 (calibration) deferred.
> Owner-facing trigger: fitness projection on `/progress` understates planned load.

---

## 1. Problem

The Fitness Projection chart (`/api/fitness-projection` → `fitness_projection`
table, fed by Intervals' `FITNESS_UPDATED` webhook) projects CTL/ATL forward
**including planned-workout load** — verified empirically: ATL bumps on exactly
the days that carry planned TSS (2026-05-22 / 24 / 27, all swims with
`icu_training_load`), and decays on every NULL-load day.

But most of our pushed planned workouts arrive at Intervals with
`icu_training_load = NULL`:

- **AI workouts** — we *compute* an estimate (`PlannedWorkoutDTO.target_tss`,
  shown in the bot as «~N TSS») but **drop it at the Intervals boundary**:
  `EventExDTO` (`data/intervals/dto.py:101`) has no load field, and
  `to_intervals_event()` (`:682`) never sends it.
- **HumanGo workouts** — the shared-calendar description we parse carries only
  per-step duration + target corridors (`docs/HUMANGO_ENRICHMENT_SPEC.md` §4);
  **no TSS**. `actor_enrich_humango_workout` (`tasks/actors/workout.py:137`)
  builds `EventExDTO` without load.

Intervals only auto-estimates planned load for some sports (swims here), not
runs/rides, and we have no control over that estimator. Net effect: the
projection trends **down** through a fully-planned training block (e.g.
2026-05-17 → 05-21 daily ride+run, all NULL) because the plan is invisible to
it — directionally misleading on the chart the owner just asked us to make
readable.

## 2. Root cause

We never send planned load to Intervals. `target_tss` exists for AI workouts
and is wired through `AiWorkout` / `mcp_server/tools/ai_workouts.py` / bot
formatter, but terminates at `to_intervals_event()`. There is no estimate at
all for HumanGo. The Intervals write schema **does** accept it:
`EventEx` (`POST/PUT /events`) exposes `icu_training_load` (int32) and
`icu_intensity` (float) — confirmed in `docs/intervals_icu_openapi.json`. Our
`EventExDTO` Pydantic model simply omits both fields.

## 3. Solution overview

Compute TSS ourselves from the structured steps and send it as
`icu_training_load` on every push (AI + HumanGo). One estimator, both paths.
The estimator itself is **threshold-free** (§4): step targets are already
percentages of threshold (so they *are* the IF), and open-step duration comes
from `moving_time`. Thresholds matter only *upstream* — the `%` corridors were
produced by round-tripping HumanGo's absolute values through
`AthleteSettings.get_thresholds()` — so the chain stays anchored to our
thresholds without the estimator touching them.

Decision (owner, this analysis): **use our own scheme; do not match HumanGo's
number; calibrate empirically later.** Because the corridors are anchored to
our thresholds, the resulting TSS is internally consistent end-to-end. HumanGo's
UI TSS is anchored to a different threshold/model (see §7) and is not ingestible
from the data we receive.

**Webhook-time requirement (owner, explicit).** The estimate must be written at
the moment we process planned workouts **on webhook receipt**, not only on the
AI push. Concrete chain (verified):

```
CALENDAR_UPDATED  (Intervals webhook)
  → api/routers/intervals/webhook.py:_dispatch_calendar
  → actor_user_scheduled_workouts            (tasks/actors/reports.py:56)
  → is_humango_event → actor_enrich_humango_workout.send  (reports.py:86)
  → actor_enrich_humango_workout             (tasks/actors/workout.py:84)
  → EventExDTO(..., icu_training_load=estimate_tss(steps))  ← THE ADD
  → client.update_event(...)
```

The whole point is that the load reaches Intervals during this webhook-driven
enrichment so the **fitness projection recomputes including it**. Note the
honest causal boundary: we do **not** recompute `fitness_projection` ourselves.
We set the *input* on the event; Intervals retains it (**AC-1 PASS,
2026-05-16**) and folds it into its forward curve, emitting a `FITNESS_UPDATED`
webhook which our receiver writes to `fitness_projection` → the Progress chart.
Open residual: the `FITNESS_UPDATED`-actually-fires-and-includes-it leg is
covered by AC-3 (integration), not yet observed end-to-end.

## 4. Estimator

For each **terminal** step (skip repeat-group containers; recurse into them):

```
IF        = mid(start, end) / 100         # start/end are % of threshold:
                                          # %pace | %ftp | %lthr — all unitless % here
dur_step  = step.duration  if > 0  else  share of residual (see below)
tss_step  = dur_step_h * IF**2 * 100
icu_training_load = round( Σ tss_step )    # over the expanded step list

# Residual rule (load-bearing — found via the 2026-05-16 dry-run):
#   timed    = Σ duration of terminal steps with duration > 0
#   residual = max(moving_time - timed, 0)
#   open_steps = terminal steps with duration <= 0 AND a target
#   each open step's dur = residual / len(open_steps)
```

- **Open / unbounded steps.** HumanGo «steady / run until done» blocks arrive as
  `duration: 0` (and frequently **no** `distance` either) — the real length
  lives in the event's `moving_time`, not the step. Without this rule a 105-min
  steady run scored **12 TSS** instead of ~103 (2026-05-24 dry-run) because only
  the timed warmups/cooldown counted. The residual `moving_time − Σ timed` is
  distributed across the untimed targeted steps. **Bonus: needs only
  `moving_time` — no threshold** — so the estimator stays threshold-free even
  for open/distance blocks (supersedes the earlier §8 «distance → 0» fallback).
- **Corridor point = midpoint** of `[start, end]`. The "use the high/low end"
  variant is the empirical-calibration knob (§9 Phase 3), deliberately deferred.
- IF² model (Coggan TSS). Unit-agnostic because every pushed target is already
  expressed as a percentage of the athlete's threshold (CLAUDE.md «Units
  contract»).
- Thresholds source: `AthleteSettings.get_thresholds()` — the *same* call the
  HumanGo converter uses for corridors, so corridors and TSS stay coherent.
- Worked example (2026-05-17 `RUNNING:Short endurance-7`, 3000 s, `%pace`
  50→83 / 73→83 / 58→73): **≈ 48 TSS** under this scheme. (HumanGo UI: 30 —
  out of scope to match, see §7.)

**Empirical back-test (2026-05-16).** The 2026-05-15 actual Run was a
near-identical Z2 endurance session: `moving_time` 2999 s (≈50 min, same as the
05-17 plan), `avg_hr` 143 (LTHR 172). Intervals' **actual** `icu_training_load`
for it = **50**. Our scheme's estimate for the 05-17 twin = **≈48** (Δ ≈ 4 %).
HumanGo's planned 30 undershoots the realised load by ~40 %. Crude HR proxy
(143/172 → IF 0.83 → ~57) overshoots — consistent with §7's note that the
`%lthr` path is the rougher one; the pace-midpoint estimate is the closest to
reality.

Second back-test, structured **power** workout with repeat groups (2026-05-16
`CYCLING:Endurance w/ 12min tempo-8`, 8460 s, `%ftp`, nested 6× + 8× blocks):
our scheme = **≈130**, Intervals' own planned `icu_training_load` = **134**, and
the NP method (NP 166 / FTP 220 → IF 0.755) independently = **≈134**. Δ ≈ 3 %;
repeat-group expansion verified (Σ durations = 8460 ✓). Midpoint slightly under
NP (NP 4th-powers hard efforts) — acceptable for a planning estimate.

Together: pace + power, flat steps + repeat groups, both within ~3-4 % of the
realised/Intervals figure.

**Compliance-gated week back-test (2026-05-11 → 05-16, actual vs estimate).**
`paired_event_id` matching is loose, so only rows with **compliance = 100 %**
(plan executed verbatim → clean estimator isolation) count:

| date | sport | actual | ours | Δ | plan |
|---|---|---|---|---|---|
| 05-14 | Ride | 36 | 33 | −3 (−8 %) | `CYCLING:12min threshold` |
| 05-15 | Run | 50 | 57 | +7 (+14 %) | `AI: Run 9K easy Z2` |

Consistent with the earlier results: power near-exact, pace/easy a mild
**over**-estimate (~+14 %) → the §9 Phase-3 calibration knob, not a blocker.
Non-100 %-compliance / unpaired rows (05-11, 05-12, 05-13, 05-15 swim) are
discarded — Δ there is dominated by execution deviation or loose pairing, not
estimator error.

**Full-window dry-run (2026-05-16 → 05-29, all 23 planned workouts).** Read-only,
no push. Findings:
- vs the 8 workouts Intervals already scored: ride −4 (130/134); swims a
  consistent **+5…+10** (e.g. 41/33, 147/137, 74/69) → ~10-20 % swim
  over-estimate, a Phase-3 calibration item, not a blocker.
- Surfaced the **open-step bug** (§4 residual rule): pre-fix a 105-min steady
  run = 12 TSS; post-fix = **103** (hand-check ≈104). Three runs (05-19/21/24)
  were affected; all sane after the fix.
- Coverage is the whole point, quantified: over the 14 days the projection
  currently sees only **492** load (≈swims only) — hence the decay; our scheme
  feeds **≈1198**, i.e. the actual plan. (One genuinely huge day, 05-24 ≈250
  = 105-min run + 109-min swim — real plan content, not an estimator artefact.)

## 5. Where it plugs in

| Path | File | Change |
|---|---|---|
| Shared | `data/intervals/dto.py` | new `EventExDTO.icu_training_load: int \| None = None` (+ optional `icu_intensity`, deferred) |
| Shared | **`data/intervals/dto.py`** (NOT `workout_adapter.py`/`metrics.py` — those import `dto`, so the helper there would be a cycle; it operates on `WorkoutStepDTO` which lives in `dto.py`) | `estimate_tss(steps, moving_time) -> int \| None` + `_flatten_steps` + `_step_intensity` (the §4 formula) |
| AI | `data/intervals/dto.py` `to_intervals_event()` | `icu_training_load=estimate_tss(self.steps, self.duration_minutes*60)` — shared estimator (single algorithm); `target_tss` stays bot-display-only |
| HumanGo (webhook-time, **core**) | `tasks/actors/workout.py` `actor_enrich_humango_workout` | `icu_training_load=estimate_tss(steps, event.moving_time)` on the `EventExDTO`. Reached via the `CALENDAR_UPDATED` → `actor_user_scheduled_workouts` → `actor_enrich_humango_workout` chain (§3) — owner-required path. |

Open sub-decision: AI path keeps `target_tss` (model-supplied) **or** switches
to the shared `estimate_tss(steps)` for a single algorithm. Recommendation:
shared helper everywhere; keep `target_tss` only as the bot display value.

## 6. Consumption (no change, just the payoff)

Once `icu_training_load` is set on the event, Intervals folds it into the
`FITNESS_UPDATED` forward curve → `fitness_projection` → the windowed Progress
chart (1m/3m/6m toggle). No reader changes; this is purely a producer fix.

## 7. Out of scope

- **Matching HumanGo's TSS.** The divergence is **sport-specific and
  threshold-driven, not a formula gap**, confirmed across both back-tests:
  - **Bike (`%ftp`)** — HumanGo ≈ Intervals ≈ our scheme ≈ **130–134**. Power
    thresholds are aligned (HumanGo FTP ≈ our FTP 220 ≈ Intervals) → all three
    agree.
  - **Run (`%pace`)** — HumanGo 30 vs our ≈48 vs **actual 50**. Reverse-derived
    HumanGo run threshold ≈ 231 s/km (3:51/km) vs our `threshold_pace_run`
    287 s/km (4:47/km); the 24 % gap ≈2× TSS via the IF² square. Our number
    matches the realised load, so **our run threshold is the trustworthy
    anchor**; HumanGo's is the outlier.

  Conclusion: keep our scheme. HumanGo agrees wherever thresholds agree (bike)
  and is wrong where its run threshold disagrees with truth. We are **not**
  back-calibrating to HumanGo. Whether HumanGo's *pace corridors* (which we
  ingest and round-trip through our threshold) are themselves built off a wrong
  run threshold is a separate, deferred question — round-tripping through our
  threshold keeps the pushed targets correct regardless.
- `icu_intensity` per-event (could send `round(overall_IF*100)`) — Phase 3.
- Compliance-path Run-pace parsing — pre-existing gap, see
  `docs/HUMANGO_ENRICHMENT_SPEC.md` §10.
- hrTSS nonlinearity: for `%lthr` steps `IF = %lthr/100` is a cruder proxy than
  the pace/power IF (HR→TSS is nonlinear). Accepted for v1; calibration in §9.

## 8. Edge cases

| Case | Behaviour |
|---|---|
| Repeat group | recurse; `tss = repeats × Σ child tss` |
| Open / unbounded step (`duration:0`, often no `distance`) | **residual rule** (§4): gets a share of `moving_time − Σ timed`. Supersedes the old «distance ÷ target speed» idea — `moving_time` is always present and needs no threshold. |
| **Distance-only targeted step** (`duration:0`, `distance:N`, target set — the enrichment parser `_humango_parse_block_for_enrichment` emits swim/interval distance reps this way, incl. inside repeat groups) | Classified **open** (predicate is `duration≤0 ∧ IF>0`, distance not inspected) → shares the residual **evenly** with any true «run-until-done» blocks, **not** distance-proportionally. Stays threshold-free, never crashes, scores roughly right for a planning estimate. NB the *compliance* path (`workout_adapter.py:194`) instead uses `duration = distance//2` — the two adapter paths diverge here by design. Distance-proportional split is a Phase-3 calibration candidate (would reintroduce a threshold). |
| Rest / Recovery target-less step (`_NO_TARGET_STEP_LABELS`) | IF≈0 → ~0 TSS — correct. (Target-less steps are excluded from `open_steps`, so they don't absorb residual.) Note: `estimate_tss` keys off IF, not the label — a *targeted* step labelled «Recovery» with `duration:0` would still count as open. Near-unreachable (recovery steps carry a fixed short duration); not gated. |
| Sport `Other` (yoga/mobility, no targets) | `estimate_tss` → `None`; leave `icu_training_load` unset (today's behaviour) |
| **Stepless plan** (`moving_time` set but `workout_doc.steps` empty/absent — e.g. `AI: Z2 Аэробная база — 60 мин`, surfaced by the 2026-05-12 back-test) | No step → no IF → `None`. A duration alone can't be scored without an intensity. Out of scope to synthesise an IF from the name/sport-default (Phase 3 at most). Such plans simply don't get a load — same as today. |
| No timed steps **and** no `moving_time` | cannot bound any duration → `None`; do not push a load |
| Missing threshold | not relevant — the estimator is threshold-free (% targets already encode IF; residual rule needs only `moving_time`). Cold-start no longer blocks load. |
| Intervals recomputes/overrides the sent value | **RESOLVED — AC-1 PASS (2026-05-16).** Sparse PUT `{icu_training_load:48}` to a structured planned Run (NULL→explicit) persisted on read-back; `workout_doc.steps` untouched (3→3, partial-merge confirmed). Scope tested: NULL→explicit (= our exact target population). Not tested: overriding a value Intervals itself computed — irrelevant, we only target NULLs. |

## 9. Phases

| # | Scope | Gate |
|---|---|---|
| ~~**0**~~ | ✅ **DONE — AC-1 PASS (2026-05-16).** Event 110387946, sent 48 → read-back 48, steps 3→3 intact. Gate cleared; Phase 1 unblocked. | — |
| ~~**1**~~ | ✅ **DONE.** `EventExDTO.icu_training_load` + `estimate_tss` + `_flatten_steps`/`_step_intensity` in `data/intervals/dto.py` (**placement deviation**: spec said `workout_adapter.py`/`metrics.py`, but `dto.py` is the only circular-import-safe home — `workout_adapter`→`dto`, never the reverse; the helper operates on `WorkoutStepDTO` which lives in `dto.py`). Wired into `to_intervals_event` (AI). 10 unit tests incl. spec anchors 05-17→48, 05-24→103. **AC-2 ✅.** | AC-2 ✅ |
| ~~**2**~~ | ✅ **CODE-DONE.** `actor_enrich_humango_workout` (`workout.py`) now sets `icu_training_load=estimate_tss(steps, event.moving_time)` on the webhook-time `EventExDTO`. 229 push-path tests green, 0 regressions. **AC-3 pending live observation** (one real `CALENDAR_UPDATED` → enrichment → `FITNESS_UPDATED` cycle showing the projection bump). | AC-3 (live) |
| **3** | Empirical calibration: corridor point (mid vs high), `%lthr` proxy, **distance-proportional residual split for distance-only steps** (reintroduces a threshold — only if even-split drifts materially on swim sets), against a sample of completed-vs-planned; optional `icu_intensity` | post-data |

## 10. Acceptance criteria

- **AC-1 (load-bearing) — ✅ PASS (2026-05-16).** Event 110387946 (structured
  Run, `%pace`, load NULL): sparse PUT `{icu_training_load:48}` → GET-back
  returned 48; `workout_doc.steps` 3→3 intact. Intervals retains an explicit
  planned load on a structured workout and the partial PUT does not clobber
  steps. Gate cleared.
- **AC-2 — ✅ PASS.** `tests/test_estimate_tss.py`, 10 deterministic cases:
  midpoint IF², repeat-group expansion, open/residual (single + multi-split),
  target-less → 0, stepless → None, no-`moving_time` → None, all-rest → None,
  plus spec anchors 05-17 → 48 and 05-24 → 103 (exact). flake8 clean.
- **AC-3:** after a HumanGo enrichment run, the event carries a non-NULL
  `icu_training_load`; within one `FITNESS_UPDATED` cycle the
  `fitness_projection` ATL shows a bump on that day (parity with the swim-day
  bumps in §1).
- **AC-4:** `Other`-sport and cold-start (no threshold) pushes still succeed
  with `icu_training_load` simply absent — no regression, no exception.

## 11. Related

- `docs/HUMANGO_ENRICHMENT_SPEC.md` — target-corridor conversion (this builds on
  its `steps`).
- Fitness-projection window + 1m/3m/6m toggle — `api/routers/activities.py`
  `/api/fitness-projection`, `webapp/src/pages/Progress.tsx`
  `FitnessProjectionChart` (the consumer this fix exists to feed).
- CLAUDE.md «Units contract» — why a single % → IF formula is sport-agnostic.
