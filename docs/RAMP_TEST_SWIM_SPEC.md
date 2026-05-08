# Swim CSS Test Protocol Specification

**Version:** 1.0
**Date:** 2026-05-08
**Status:** Draft — pending implementation
**Owner:** radik
**Project:** triathlon-agent

---

## 1. Goal

Define a unified swim Critical Swim Speed (CSS) test protocol that produces accurate threshold pace for use in Intervals.icu zone calibration. CSS replaces DFA a1 thresholds as the primary swimming threshold metric — RR-interval recording is impractical underwater, so a pace-based time trial method is used instead.

This spec parallels `RAMP_TEST_SPEC.md` (run/bike) but uses fundamentally different methodology because of the swim-specific constraints.

## 2. Method overview — Critical Swim Speed (Ginn formula)

**CSS** = pace at which an athlete can sustain steady-state lactate balance, equivalent in concept to FTP (cycling) or threshold pace (running). Defined as the pace sustainable for ~30 minutes or ~1500m at maximum effort.

Computed from two time trials of different distances using the **Ginn formula**:

```
CSS (sec per 100m) = (T400 - T200) / 2
```

Where:

- `T400` = time to swim 400m at maximum sustainable pace (seconds)
- `T200` = time to swim 200m at maximum sustainable pace (seconds)

**Logic:** The difference (T400 − T200) represents the "additional 200m" at threshold effort. Dividing by 2 normalizes to per-100m pace at which lactate steady state can be maintained.

## 3. Universal protocol principles

1. **Pace is measured directly from time trials.** No HR or DFA a1 — water suppresses HR significantly, RR recording requires waterproof chest strap and is unreliable.
2. **Two distances, single test session.** 400m TT + 200m TT, with rest between. Both required for the formula.
3. **Maximum effort, but paced — not sprinted.** Athlete must sustain a steady, hard-but-controlled effort. Sprinting first 50m and dying produces invalid data.
4. **Proper warm-up critical.** Cold start gives inflated T200/T400. ~400m warm-up minimum.
5. **Adequate rest between TTs.** 5-10 min recovery; HR should fall below 120 bpm before second TT.
6. **Sanity checks post-test.** T200 pace must be faster than T400 pace by 8-15 sec/100m. Outside this range → test invalid.
7. **Pool length consistency.** 25m pool standard; 50m or other lengths require lap configuration in Garmin.

## 4. CSS test protocol

### 4.1 Parameters

| Parameter        | Value                      |
| ---------------- | -------------------------- |
| Total distance   | 1400m                      |
| Total duration   | ~35-40 min                 |
| Warm-up          | 400m (~10 min)             |
| Pre-set 1        | 50m build                  |
| **Time Trial 1** | **400m maximum effort**    |
| Rest             | 5-10 min (HR < 120)        |
| Pre-set 2        | 100m re-warmup + 50m build |
| **Time Trial 2** | **200m maximum effort**    |
| Cool-down        | 200m (~5 min)              |
| Pool length      | 25m (default)              |
| Stroke           | Freestyle only             |

### 4.2 Detailed structure

```
WARM-UP (400m, ~10 min)
├── 200m  easy freestyle
├── 100m  drills (catch-up or fingertip drag)
└── 4 × 25m  build, 15 sec rest between
    Total: 400m

PRE-SET 1 (50m)
└── 50m  build to race pace

⏱️ TIME TRIAL 1 — 400m MAXIMUM EFFORT
└── Record T400 (seconds)
    Pacing: first 50m controlled, 100-300m steady, last 100m can accelerate

REST 5-10 MIN
└── Exit pool, drink, breathe
    HR must drop below 120 bpm

PRE-SET 2 (150m)
├── 100m  easy re-warmup
└──  50m  build to race pace

⏱️ TIME TRIAL 2 — 200m MAXIMUM EFFORT
└── Record T200 (seconds)
    Pacing: first 50m don't sprint, then steady all-out

COOL-DOWN (200m, ~5 min)
├── 100m  easy mixed strokes
└── 100m  super easy freestyle
```

**Total:** 400 + 50 + 400 + 150 + 200 + 200 = **1400m**

### 4.3 Pacing strategy

**T400 (4 minutes of focus):**

- 0-50m: deliberately hold back, ~5 sec/100m slower than perceived "max"
- 50-300m: steady threshold effort, do not accelerate prematurely
- 300-400m: optional final push if energy remains

Most common error: starting too fast and dying after 200m → inflated T400 → falsely low CSS.

**T200 (2 minutes of focus):**

- 0-50m: do not sprint; build into pace
- 50-200m: steady all-out effort
- Last 50m: full effort, finish strong

Easier to pace correctly than T400 due to shorter duration.

### 4.4 Workout description template

```
SWIM CSS TEST PROTOCOL

EQUIPMENT:
- Garmin Fenix / Forerunner with Pool Swim activity
- Pool length 25m (verify in watch settings)
- Optional: kickboard, fins for warm-up drills

WARM-UP (400m, 10 min):
- 200m easy freestyle
- 100m drills (catch-up / fingertip drag)
- 4×25m build, 15 sec rest

PRE-SET 1: 50m build to race pace, ⏸️ press LAP

TIME TRIAL 1: 400m MAX EFFORT
- ⏸️ press LAP at start
- Pacing: controlled first 50m, steady 100-300m, push last 100m
- ⏸️ press LAP at finish — record T400

REST 5-10 minutes:
- Exit pool, hydrate
- HR < 120 before continuing

PRE-SET 2: 100m re-warmup + 50m build, ⏸️ press LAP

TIME TRIAL 2: 200m MAX EFFORT
- ⏸️ press LAP at start
- Don't sprint first 50m, steady all-out after
- ⏸️ press LAP at finish — record T200

COOL-DOWN: 200m easy

POST-TEST:
- Record T400 and T200 in seconds
- CSS = (T400 - T200) / 2 in sec/100m
- Update via update_zones(sport='swim', threshold_pace=CSS_sec)
```

## 5. Watch configuration

### 5.1 Garmin setup (Fenix / Forerunner)

**Activity profile:** Pool Swim (NOT Open Water Swim)

**Settings:**

- Pool length: **25m** (or actual length used)
- Lap key: Manual (allows pressing Lap button to mark TT segments)
- Auto-lap: OFF (avoid distance-based auto-laps interfering with manual laps)
- Stroke detection: ON

**Lap button discipline:**

- Press Lap before each TT segment start
- Press Lap immediately at finish wall touch
- This isolates TT data into discrete laps for clean extraction

If lap button is missed, T400 and T200 must be extracted manually from activity record (interval analysis in Intervals.icu UI).

### 5.2 Backup timing

Always have a secondary timing method as backup:

- Pool clock (visual reference)
- Mental count of laps + average pace estimate
- Phone stopwatch on pool deck (if permitted)

Watch glitches in pool environment are common (length miscount, missed laps). Manual record on paper/memory protects against data loss.

## 6. CSS calculation

### 6.1 Formula

```python
def calculate_css(t400_seconds: float, t200_seconds: float) -> float:
    """
    Calculate Critical Swim Speed using Ginn formula.

    Args:
        t400_seconds: Time for 400m time trial (seconds)
        t200_seconds: Time for 200m time trial (seconds)

    Returns:
        CSS in seconds per 100m
    """
    css_sec_per_100m = (t400_seconds - t200_seconds) / 2
    return css_sec_per_100m


def format_pace(sec_per_100m: float) -> str:
    """Format CSS as M:SS/100m"""
    minutes = int(sec_per_100m // 60)
    seconds = int(sec_per_100m % 60)
    return f"{minutes}:{seconds:02d}/100m"
```

### 6.2 Example calculation

```
T400 = 8:04 = 484 sec
T200 = 3:38 = 218 sec

CSS = (484 - 218) / 2 = 266 / 2 = 133 sec/100m
CSS formatted = 2:13/100m
```

## 7. Sanity checks (MUST run before accepting CSS)

### 7.1 Check 1: T200 pace must be faster than T400 pace

```python
def check_pace_relationship(t400_sec: float, t200_sec: float) -> bool:
    t400_pace = t400_sec / 4  # per 100m
    t200_pace = t200_sec / 2  # per 100m
    return t200_pace < t400_pace
```

If T200 pace ≥ T400 pace:

- Athlete didn't push hard enough on T200
- Excessive fatigue from T400 (rest was too short)
- Test invalid → retest

### 7.2 Check 2: Pace difference within physiological range

```python
def check_pace_difference(t400_sec: float, t200_sec: float) -> dict:
    t400_pace = t400_sec / 4
    t200_pace = t200_sec / 2
    diff = t400_pace - t200_pace

    if diff < 8:
        return {"valid": False, "reason": "T200 not max effort (diff < 8 sec/100m)"}
    elif diff > 15:
        return {"valid": False, "reason": "T400 too slow / not at threshold (diff > 15 sec/100m)"}
    else:
        return {"valid": True, "diff_sec_per_100m": diff}
```

Expected difference: **8-15 sec/100m** between T200 pace and T400 pace.

| Diff      | Interpretation                                     |
| --------- | -------------------------------------------------- |
| < 8 sec   | T200 was sub-maximal — retest with proper effort   |
| 8-12 sec  | Normal range, test valid                           |
| 12-15 sec | Wide range but acceptable; check pacing on T400    |
| > 15 sec  | T400 was paced too slow OR T200 too fast — invalid |

### 7.3 Check 3: CSS consistent with training history

```python
def check_against_history(css_sec: float, recent_avg_pace: float) -> dict:
    if css_sec > recent_avg_pace + 5:
        return {
            "valid": False,
            "reason": f"CSS {css_sec}s is slower than recent training avg {recent_avg_pace}s "
                      "— physiologically impossible"
        }
    elif css_sec < recent_avg_pace - 20:
        return {
            "valid": "warning",
            "reason": f"CSS {css_sec}s is significantly faster than training avg {recent_avg_pace}s "
                      "— verify test was at maximum effort"
        }
    else:
        return {"valid": True}
```

Logic: an athlete cannot have CSS slower than their typical training pace — that violates the definition of CSS as threshold. If this happens, the test was sub-maximal.

Use `get_efficiency_trend(sport='swim')` to retrieve recent average pace for comparison.

## 8. Code — full pipeline

```python
def analyze_swim_css_test(
    activity_id: str,
    t400_lap_index: int | None = None,
    t200_lap_index: int | None = None,
) -> dict:
    """
    Analyze swim CSS test from Garmin activity.

    Args:
        activity_id: Intervals.icu activity ID
        t400_lap_index: Lap number containing 400m TT (optional, will try to detect)
        t200_lap_index: Lap number containing 200m TT (optional, will try to detect)

    Returns:
        {
            "css_sec_per_100m": float,
            "css_formatted": str,
            "t400_sec": int,
            "t200_sec": int,
            "validation": {
                "pace_relationship_ok": bool,
                "pace_diff_ok": bool,
                "history_ok": bool,
                "diff_sec_per_100m": float,
                "warnings": list[str]
            },
            "recommendation": "update" | "review" | "retest"
        }
    """
    activity = get_activity_details(activity_id)

    # Auto-detect TT laps if not specified
    if t400_lap_index is None or t200_lap_index is None:
        t400_lap, t200_lap = autodetect_tt_laps(activity.laps)
    else:
        t400_lap = activity.laps[t400_lap_index]
        t200_lap = activity.laps[t200_lap_index]

    t400_sec = t400_lap.duration_seconds
    t200_sec = t200_lap.duration_seconds

    # Calculate CSS
    css_sec = calculate_css(t400_sec, t200_sec)

    # Run sanity checks
    pace_rel = check_pace_relationship(t400_sec, t200_sec)
    pace_diff = check_pace_difference(t400_sec, t200_sec)

    recent_trend = get_efficiency_trend(sport='swim', days_back=14)
    recent_avg = recent_trend.metrics.pace_100m.mean
    history = check_against_history(css_sec, recent_avg)

    # Decision
    if pace_rel and pace_diff["valid"] is True and history["valid"] is True:
        recommendation = "update"
    elif pace_rel and pace_diff["valid"] in (True, "warning") and history["valid"] in (True, "warning"):
        recommendation = "review"
    else:
        recommendation = "retest"

    return {
        "css_sec_per_100m": css_sec,
        "css_formatted": format_pace(css_sec),
        "t400_sec": t400_sec,
        "t200_sec": t200_sec,
        "validation": {
            "pace_relationship_ok": pace_rel,
            "pace_diff_ok": pace_diff["valid"],
            "history_ok": history["valid"],
            "diff_sec_per_100m": pace_diff.get("diff_sec_per_100m"),
            "warnings": [pace_diff.get("reason"), history.get("reason")],
        },
        "recommendation": recommendation,
    }


def autodetect_tt_laps(laps: list) -> tuple:
    """
    Heuristic: find laps with distance ≈ 400m and 200m,
    duration consistent with hard effort (faster than easy threshold).

    Returns (t400_lap, t200_lap).
    """
    candidates_400 = [l for l in laps if 380 <= l.distance_m <= 420]
    candidates_200 = [l for l in laps if 180 <= l.distance_m <= 220]

    if not candidates_400 or not candidates_200:
        raise ValueError(
            "Could not auto-detect TT laps. "
            "Please specify t400_lap_index and t200_lap_index manually."
        )

    # Among candidates, pick fastest (TT was max effort)
    t400_lap = min(candidates_400, key=lambda l: l.duration_seconds / l.distance_m)
    t200_lap = min(candidates_200, key=lambda l: l.duration_seconds / l.distance_m)

    return t400_lap, t200_lap
```

## 9. Auto-update zones logic

| Recommendation | Action                                                              |
| -------------- | ------------------------------------------------------------------- |
| `update`       | Auto-update `threshold_pace` in `pace_zones_swim`                   |
| `review`       | Suggest update with explicit confirmation, show validation warnings |
| `retest`       | No update; explain failure mode and recommend retest after rest     |

Drift threshold for triggering update: **≥3 sec/100m** difference between current and measured CSS. Smaller drifts within measurement noise.

```python
def update_swim_threshold_if_needed(
    measured_css_sec: float,
    current_css_sec: float,
    recommendation: str,
) -> dict:
    DRIFT_THRESHOLD = 3.0  # sec/100m

    drift = abs(measured_css_sec - current_css_sec)

    if drift < DRIFT_THRESHOLD:
        return {"action": "no_change", "reason": f"Drift {drift:.1f}s within noise threshold"}

    if recommendation == "update":
        update_zones(sport="swim", threshold_pace=measured_css_sec)
        return {"action": "updated", "old": current_css_sec, "new": measured_css_sec, "drift": drift}
    elif recommendation == "review":
        return {"action": "pending_confirmation", "old": current_css_sec, "new": measured_css_sec}
    else:
        return {"action": "blocked", "reason": "Test failed validation, retest required"}
```

## 10. Test cadence

| Phase                | Frequency                                  |
| -------------------- | ------------------------------------------ |
| Build phase          | Every 4-6 weeks                            |
| Base phase           | Every 6-8 weeks                            |
| Peak / taper         | No testing                                 |
| After race / illness | Retest before resuming structured training |

CSS tends to drift more slowly than run/bike thresholds because swim adaptation is heavily technique-driven (SWOLF improvement) rather than purely physiological. Frequent retesting is less critical than for run/bike.

**Race week protocol:** no CSS test in race week or 7 days before race.

## 11. Open dependencies

- **`get_activity_details` MCP tool** must expose laps with duration, distance, and pace per lap. Verify schema includes `laps[].distance_m` and `laps[].duration_seconds`.
- **`get_efficiency_trend(sport='swim')`** already exposes `pace_100m.mean` for recent activities — use for sanity check 3.
- **`update_zones(sport='swim', threshold_pace=...)`** write path verified for swim sport.
- **MCP CSS tool — proposed**: `analyze_swim_css_test(activity_id, ...)` to wrap the full pipeline. Currently CSS calculation is manual; consider automating in Phase 2 of swim implementation.

## 12. Migration path

### Phase 1 — Manual CSS workflow (no code change)

- Athlete performs CSS test per §4 protocol
- Manually records T400 and T200
- Reports values to triathlon-agent (chat or CLI)
- Agent calculates CSS, runs sanity checks (§7), recommends action
- Manual call to `update_zones` if validated

### Phase 2 — Automated CSS analysis

- Implement `analyze_swim_css_test` MCP tool per §8
- Auto-detect TT laps from activity data
- Run validation pipeline automatically
- Surface recommendation to athlete (update / review / retest)
- Auto-update zones if `recommendation == "update"`

### Phase 3 — Workout template integration

- Add CSS test to `create_ramp_test_tool` API: `create_ramp_test_tool(sport='swim')`
- Generate Intervals.icu-compatible swim workout (1400m, structured laps)
- Pre-fill workout description with protocol from §4.4

### Phase 4 — Continuous monitoring

- Track CSS drift over time using monthly average pace from `get_efficiency_trend`
- Alert when measured pace consistently exceeds CSS (suggests CSS underestimated)
- Recommend retest when drift > 5 sec/100m persists 3+ weeks

## 13. Decision log

1. **CSS over DFA a1 for swim.** RR-interval recording requires waterproof chest strap (rare equipment), and water suppresses HR variability. Pace-based time trial is the practical standard.
2. **400+200 over 1500m TT.** Single 1500m TT is psychologically harder, requires more pool space, and gives single threshold without redundancy. Two-trial method allows internal validation (pace_diff sanity check).
3. **1400m total volume.** Compromise between full classic protocol (~1900m) and minimum viable (~1100m). Provides adequate warm-up and re-warmup without making test exhausting.
4. **5% drift threshold for zone updates.** Smaller than run/bike (which use ≥3 bpm or ≥5 sec/km). Swim improvements often slow but technique-driven — small drifts are real adaptation, not noise.
5. **Sanity checks BEFORE auto-update.** Swim TT execution is more error-prone than run/bike ramp test (pacing on max effort is hard, watch errors common). Validation gates prevent corrupt data from polluting zones.
6. **No HR data in CSS analysis.** Swim HR is suppressed and inconsistent. Including it would add noise without information.

## 14. References

- Ginn E. (1993). _Critical Swim Speed: A Pacing Indicator for Swimming Training._ University of Canberra. — original CSS formula derivation.
- Wakayoshi K. et al. (1992). _Determination and validity of critical velocity as an index of swimming performance in the competitive swimmer._ European Journal of Applied Physiology.
- Swim Smooth methodology — practical CSS test protocol references.
- TrainerRoad / Intervals.icu CSS workflow documentation.

## 15. Companion documents

- `RAMP_TEST_BIKE_SPEC.md` — run/bike DFA a1 ramp test specification
