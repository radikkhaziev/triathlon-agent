# Workout Absolute Targets Spec

> Make watch HR alerts independent of the athlete's local Garmin/Coros/Wahoo
> zone config, so a workout corridor displays the bpm/watts/pace we intend.
>
> Status: 🟢 **SHIPPED 2026-05-12 — root cause was a field-name rename, not
> absolute-unit conversion.**

---

## 1. The contract (load-bearing — keep in sync with `bot/prompts.py` + CLAUDE.md)

Per-sport target convention Claude emits and the validator enforces:

| Sport | Target type | Units |
|---|---|---|
| Run | `hr` | `%lthr` |
| Ride | `power` | `%ftp` |
| Swim | `pace` | `%pace` |

**HR/power/pace corridor schema — use `start` (low) + `end` (high), NEVER
`value` (+ `end`).** Production shape:

```python
# data/intervals/dto.py — WorkoutStepDTO target
{
    "text": "Z2 main",
    "duration": 1500,
    "hr": {"units": "%lthr", "start": 85, "end": 89},
}
```

Values stay as percentages (`%lthr` / `%ftp` / `%pace`). Intervals computes the
absolute bpm/watts/pace at FIT-export time using **its own** stored threshold
for the athlete (kept in sync by `actor_update_zones`). No server-side
`%X → absolute` conversion is done on our side, and none is wanted (§5).

### Why `start`/`end` and not `value`/`end` — FIT-export mode switching

Intervals' FIT export switches behavior on the HR-target dict shape:

- `{value, end}` → **Lap-HR / zone-mapped** point target. The watch clamps it
  to its own local zone boundaries → if the watch's LTHR has drifted (Garmin
  auto-detect, per-profile override, stale sync), every corridor silently
  shifts. This was the original "drift" symptom, misdiagnosed as a watch-side
  LTHR mismatch.
- `{start, end}` → **Instant-HR corridor**. The watch displays the raw bpm
  range Intervals computed → drift-immune.

The Intervals UI "Мгновенный HR" vs "Круг ЧСС" toggle maps to exactly this
distinction; it is only observable via API readback, not as a stored field.
Verified end-to-end on user 1's watch face 2026-05-12 (LTHR_intervals=172,
LTHR_watch=168): a `75-82% × 172` target rendered as **129-141 bpm** on the
watch — matching LTHR_intervals, not LTHR_watch. See §4 Attempt 3b.

---

## 2. Code pointers

- **Validator** `PlannedWorkoutDTO._check_steps_have_targets`
  (`data/intervals/dto.py`) — rejects any terminal (non-repeat-group) step
  without `hr/power/pace`, accepting the `start` field. Backstop if the model
  forgets the contract.
- **Native description renderer** `render_native_description` /
  `_render_target` (`data/intervals/dto.py`) — reads `start` for the
  Intervals.icu structured-text description (grammar in
  `docs/INTERVALS_NATIVE_WORKOUT_FORMAT.md`). Emits the zone-label text
  (`75-82% LTHR`), so the corridor change didn't touch coach-readability.
- **Prompt** `bot/prompts.py:_zones_block` — Run/Ride/Swim examples emit
  `start` directly so Claude produces the right shape.

The fix applied 2026-05-12 was a `value` → `start` rename across those three
sites. Existing in-DB AI workouts with the old `value` shape regenerate on the
next cron cycle; already-pushed Intervals calendar events were not modified.

---

## 3. Failed approaches (history — kept so we don't relitigate)

Tested on user 1, 2026-05-12, against a 4-step Run fixture (LTHR=172). Each ran
the full protocol: push → API readback → Garmin Connect sync → watch-face
inspection.

- **Attempt 1 — `{units: "bpm", value, end}`.** API accepted; watch showed
  214-249 bpm (impossible). FIT mis-read `units: "bpm"` as percent-of-something.
- **Attempt 2 — `{start_bpm, end_bpm}` (no `units`).** API accepted; watch
  showed constant `101` (its "no valid HR target" fallback). FIT dropped the
  unknown schema.
- **Attempt 3a — UI dropdown survey.** Misread the absence of an absolute-bpm
  option (`нет`/`%МаксЧСС`/`%LTHR`/`зона`…) as "absolute is impossible". Wrong:
  that dropdown is the *units label* picker, not the value-shape picker.
- **Attempt 3b — `{units: "%lthr", start, end}` — ✅ SUCCESS.** Discovered from
  API readback of a UI-built test workout that the corridor lower bound is
  `start`, not `value`. Watch displayed exact expected ranges (129-141, etc.)
  within ±1 bpm. This is the shipped fix; see §1 for the mechanism.

**Stopgap (now obsolete):** between Attempt 2 and 3b we embedded the bpm
corridor into `step.text` ("Warm-up easy · 129-141 bpm") so the athlete could
read it visually. Reverted once `start`/`end` made both display and alert
correct.

Lesson logged: any future schema spike must be verified on the watch face
**before** the scheduled workout starts.

---

## 4. Rest / Recovery steps — no-target allowed (2026-05-13)

Exceptions to the "every terminal step needs a target" rule, enforced in
`_check_steps_have_targets`:

1. **Sport `Other`** (yoga, stretching, mobility) — listed in
   `_NO_TARGET_SPORTS = frozenset({"Other"})`. Watches don't need intensity
   targets for these; `workout_cards.py` sets its own description for them.
2. **Terminal step labelled `Rest` / `Recovery`** — matched case-insensitively
   against `_NO_TARGET_STEP_LABELS = frozenset({"rest", "recovery"})`.

**Why the Rest exception exists.** With a fake low target (e.g.
`{units: "%pace", start: 40, end: 55}`) on a Rest step, Intervals renders a
**segment of slow Z1 swimming** instead of a **real pool-side stop**. Without a
target, Intervals draws a true flat gap in the chart and excludes it from
active time. Verified on swim event `109954764`: v1 (fake-pace) charted three
solid bars; v2/v3 (target-less Rest) charted a real pause and FIT-exported
valid to Garmin. `render_native_description` already emitted bare `- Rest 20s`
(no `Pace` suffix) for target-less steps, so that path needed no change.

The `suggest_workout` MCP docstring instructs Claude to set `text` to
`"Rest"`/`"Recovery"` and omit `hr/power/pace`. HumanGo imports bypass
`PlannedWorkoutDTO` entirely (`tasks/actors/workout.py:actor_humango_enrichment`
→ `data/workout_adapter.py:humango_to_intervals_steps`), which is why HumanGo
swims always charted pauses correctly; the label match aligns with HumanGo's
`display_names["rest"] == "Rest"` convention.

**Caveat.** `_NO_TARGET_STEP_LABELS` is English-only. If Claude starts writing
ru labels ("Отдых"/"Восстановление") they must be added, or migrate to a
semantic `is_rest: bool` flag on `WorkoutStepDTO`. Arbitrary labels are still
rejected (regression guard intact).

---

## 5. Decision: keep `%X`, never push absolute (confirmed 2026-05-12)

The originally-scoped Phase A ("server-side `%X → absolute` converter") is
**not** built — `start`/`end` closed the root cause without converting to
bpm/watts/sec. Phases B (auto-regen on zone update) and C (dual-write
description) are likewise **moot**: Intervals recomputes absolute targets at
FIT-export time from its own stored threshold, so an `actor_update_zones` LTHR
push propagates to all future events on the next sync, and
`render_native_description` already carries the zone-label text.

| | `%lthr/%ftp/%pace` (current) | Absolute (`bpm/watts/sec`) |
|---|---|---|
| Watch alerts correct | ✅ via `start/end` corridor | ✅ |
| LTHR-update propagation | ✅ auto via FIT sync | ❌ needs backfill regen |
| Coach-readable Intervals UI | ✅ `85-89% LTHR` | 🟡 `146-153 bpm`, no zone semantic |
| Coupling | ✅ renderer pure, no threshold threading | ❌ ~50-100 LoC from `AthleteSettings` |
| Cold-start athletes | ✅ works without synced settings | ❌ needs fallback path |

### Acceptance — Phase A ✅ SHIPPED 2026-05-12

- [x] Garmin watch displays the exact bpm corridor we intend
- [x] Independence from watch-local LTHR confirmed (watch=168, shows 129-141
      for 75-82% × 172 → matches LTHR_intervals)
- [x] Coach-readable Intervals UI preserved (`render_native_description`)
- [x] No regression on power/pace (already used range encoding)

### Close-out checklist

- [x] Code fix applied (`value` → `start` in `bot/prompts.py` +
      `data/intervals/dto.py`)
- [x] User 1 end-to-end watch-face verification (§3 Attempt 3b)
- [x] Tomorrow's Run (`109762340`) + Swim (`109762368`) re-pushed via
      `update_event`; full calendar sweep skipped (Claude regenerates on cron)
- [ ] CLAUDE.md "Intensity target mandate" — document the `start`/`end` key
      convention so future edits preserve it
- [ ] Regression test in `tests/db/test_ai_workouts.py`: ranged HR targets
      serialize with `start`/`end`, not `value`/`end`

---

## 6. Open Intervals UI quirks (not our bugs, not blockers)

- **A. Swim planned-workout pace always shows `min/km`.** Intervals UI hardcode
  for the swim event detail view; ignores `sport_settings.pace_units:
  "SECS_100M"`. Storage is correct (both a manually-built event and our
  AI-pushed one render `XX:XX/km`); render-layer only. Needs an Intervals
  support report.
- **B. `update_event` does NOT trigger async enrichment.** Same payload via
  `POST /events` enriches `workout_doc`
  (zoneTimes/normalized_power/polarization_index/strain_score/…); via
  `PUT /events/{id}` only `steps` come back. For a cosmetic rename like ours
  this is irrelevant (storage + FIT corridor are correct), but if UI/dashboard
  enrichment is needed, use delete+create instead of update.
- **C. ⏳ Bike power discrepancy — pending.** `{units: "%ftp", start, end}`
  already uses the right corridor schema, but needs the same end-to-end
  verification on a real power meter / ERG trainer. Trigger: next scheduled AI
  bike workout the athlete actually rides.

---

## 7. Related

- **CLAUDE.md "Intensity target mandate"** — the convention this spec encodes;
  enforced by `PlannedWorkoutDTO._check_steps_have_targets`.
- **`docs/INTERVALS_NATIVE_WORKOUT_FORMAT.md`** — structured-text description
  grammar + parser quirks emitted by `render_native_description`.
- **`bot/prompts.py:_zones_block`** — Claude prompt; `%lthr`/`%ftp`/`%pace`
  per sport, emitting `start`/`end`.
- **`docs/HUMANGO_ENRICHMENT_SPEC.md`** — the HumanGo import path that bypasses
  `PlannedWorkoutDTO` (relevant to the Rest exception, §4).
