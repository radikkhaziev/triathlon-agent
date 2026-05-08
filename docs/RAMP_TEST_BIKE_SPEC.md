# Ramp Test Protocol Specification — DFA a1 Method

**Version:** 1.0
**Date:** 2026-05-08
**Status:** Draft — pending implementation
**Owner:** radik
**Project:** triathlon-agent

---

## 1. Goal

Define a unified ramp test protocol for run and bike that produces high-confidence HRVT1 / HRVT2 thresholds via DFA a1 analysis, suitable for automated zone calibration in Intervals.icu.

Replaces current `RAMP_STEPS_RIDE` (top 103% FTP, 6 steps, uneven 7-8% increment) and aligns bike protocol with the methodologically-correct run protocol.

## 2. Method overview — DFA a1

Detrended Fluctuation Analysis (alpha-1 scaling exponent) of RR-intervals in sliding 120-beat windows. Core thresholds:

- DFA a1 ≥ 1.0 — fully aerobic
- DFA a1 = 0.75 — **HRVT1 = LT1 = aerobic threshold** (Z2/Z3 boundary)
- DFA a1 = 0.5 — **HRVT2 = LT2 = anaerobic threshold** (Z3/Z4 boundary)
- DFA a1 < 0.5 — anaerobic / VO2max territory

Output: pair of (HR, power|pace) coordinates for both HRVT1 and HRVT2 with R² and confidence score → maps to LTHR + threshold pace/FTP in Intervals.icu.

## 3. Universal protocol principles

1. **Load is controlled, HR is observed.** Power for bike, pace for run. Never HR-target on work steps.
2. **Anchor against current threshold.** All steps as % of `threshold_pace` (run) or FTP (bike). Self-calibrating.
3. **3-minute steps for DFA a1 stabilization.** ≥3 DFA a1 windows per step. Standard in literature (Rogers 2020-2023).
4. **5% increment for resolution.** 3-5 bpm HR delta per step.
5. **Top must penetrate HRVT2.** Otherwise HRVT2 is extrapolated, not measured.
6. **Cover both thresholds with margin.** Min 2-3 points below HRVT1, in gray zone, above HRVT2.
7. **Natural failure as stop signal.**
8. **Chest HR strap mandatory.** Optical sensors do not produce valid RR data.

## 4. Run protocol

### 4.1 Parameters

| Parameter            | Value                                           |
| -------------------- | ----------------------------------------------- |
| Anchor               | `threshold_pace` (sec/km from `pace_zones_run`) |
| Control unit         | pace (km/h)                                     |
| Start                | 80% threshold                                   |
| Step                 | 5% threshold (rounded to 0.5 km/h)              |
| Top                  | 115% threshold                                  |
| Number of work steps | 8                                               |
| Step duration        | 180 sec (3 min)                                 |
| Warm-up              | 600 sec, `hr={units:"%lthr", value:70}`         |
| Cool-down            | 600 sec, `hr={units:"%lthr", value:70}`         |
| Total duration       | ~44 min                                         |

### 4.2 Step ladder (threshold pace 4:47/km)

| Step | %threshold | km/h | min/km | Expected zone     |
| ---- | ---------- | ---- | ------ | ----------------- |
| WU   | by feel    | —    | —      | Z1                |
| 1    | 80%        | 10.0 | 6:00   | Z1-Z2             |
| 2    | 85%        | 10.5 | 5:43   | Z2                |
| 3    | 90%        | 11.5 | 5:13   | Z2-Z3 (~HRVT1)    |
| 4    | 95%        | 12.0 | 5:00   | Z3                |
| 5    | 100%       | 12.5 | 4:48   | Z3-Z4 (threshold) |
| 6    | 105%       | 13.0 | 4:37   | Z4                |
| 7    | 110%       | 14.0 | 4:17   | Z4-Z5 (~HRVT2)    |
| 8    | 115%       | 14.5 | 4:08   | Z5+ (failure)     |
| CD   | by feel    | —    | —      | Z1                |

### 4.3 Code

```python
def build_ramp_steps_run(
    threshold_pace_sec_per_km: float | None = None,
) -> tuple[list[WorkoutStepDTO], str]:
    DEFAULT_THRESHOLD_PACE = 295.0  # 4:55/km — average amateur fallback
    START_PCT = 0.80
    STEP_PCT = 0.05
    N_STEPS = 8
    STEP_DURATION = 180

    warnings = []
    if threshold_pace_sec_per_km is None:
        threshold_pace_sec_per_km = DEFAULT_THRESHOLD_PACE
        warnings.append(
            f"⚠️ Threshold pace not found. Used default {DEFAULT_THRESHOLD_PACE}s/km (4:55/km). "
            "If your actual threshold differs significantly, update sport-settings first."
        )

    threshold_speed = 3600 / threshold_pace_sec_per_km

    steps = [
        WorkoutStepDTO(
            text="Warm-up — easy jog by feel, build to Z1 upper",
            duration=600,
            hr={"units": "%lthr", "value": 70},
        )
    ]

    for i in range(N_STEPS):
        pct = START_PCT + i * STEP_PCT
        speed = round(threshold_speed * pct * 2) / 2  # round to 0.5 km/h
        steps.append(WorkoutStepDTO(
            text=f"Step {i+1} ({int(pct*100)}% threshold)",
            duration=STEP_DURATION,
            pace={"units": "km/h", "value": speed},
        ))

    if steps[-1].pace["value"] > 20.0:
        warnings.append(
            f"⚠️ Top step is {steps[-1].pace['value']} km/h. "
            "Most home treadmills cap at 18-20 km/h."
        )

    steps.append(WorkoutStepDTO(
        text="Cool-down — easy jog, let HR fall below 70% LTHR",
        duration=600,
        hr={"units": "%lthr", "value": 70},
    ))

    return steps, "\n".join(warnings)
```

### 4.4 Notes on warm-up / cool-down

WU/CD remain HR-targeted (`%lthr 70`) in the DTO for Intervals.icu TSS calculation, but **Garmin will ignore HR targets when `event.target=PACE`** is set on work steps — this is desired. Athlete runs WU/CD by feel without watch prompts.

## 5. Bike protocol

### 5.1 Parameters

| Parameter            | Value                            |
| -------------------- | -------------------------------- |
| Anchor               | bike FTP (watts)                 |
| Control unit         | watts (ERG mode)                 |
| Start                | 60% FTP                          |
| Step (1-11)          | 5% FTP                           |
| Step 12 (final)      | 120% FTP (10% jump from step 11) |
| Number of work steps | 12                               |
| Step duration (1-11) | 180 sec (3 min)                  |
| Step duration (12)   | 240 sec (4 min, push to failure) |
| Warm-up              | 300s @ 50% + 300s @ 60% FTP      |
| Cool-down            | 600s @ 50% FTP                   |
| Total duration       | ~57 min                          |

### 5.2 Rationale for 120% top — calibration trap

If FTP is undercalibrated (typical case), 110% top may not penetrate real HRVT2 → DFA a1 = 0.5 not crossed → HRVT2 extrapolated → low R².

Example with current data:

- DB FTP = 208W, real HRVT2_power ≈ 240W (last test R² = 0.62)
- 110% × 208 = 229W → below real HRVT2 → α1 doesn't cross 0.5
- 115% × 208 = 239W → at real HRVT2 → borderline α1 ≈ 0.5
- **120% × 208 = 250W → above real HRVT2 → clean α1 < 0.5**

After first calibrated test updates FTP, top 120% scales correctly: 120% × 240 = 288W (Z6, achievable).

### 5.3 Step ladder (FTP = 208W)

| Step     | %FTP | Watts | Coggan zone          | Duration |
| -------- | ---- | ----- | -------------------- | -------- |
| WU easy  | 50%  | 104W  | Z1                   | 5 min    |
| WU build | 60%  | 125W  | Z2 low               | 5 min    |
| 1        | 60%  | 125W  | Z2 endurance         | 3 min    |
| 2        | 65%  | 135W  | Z2 mid               | 3 min    |
| 3        | 70%  | 146W  | Z2 high              | 3 min    |
| 4        | 75%  | 156W  | Z3 tempo             | 3 min    |
| 5        | 80%  | 166W  | Z3 tempo             | 3 min    |
| 6        | 85%  | 177W  | Z3-Z4 (~HRVT1)       | 3 min    |
| 7        | 90%  | 187W  | Z4 sub-threshold     | 3 min    |
| 8        | 95%  | 198W  | Z4 sub-threshold     | 3 min    |
| 9        | 100% | 208W  | Z4 threshold (FTP)   | 3 min    |
| 10       | 105% | 218W  | Z5 super-threshold   | 3 min    |
| 11       | 110% | 229W  | Z5+ VO2max           | 3 min    |
| 12       | 120% | 250W  | Z6 — push to failure | 4 min    |
| CD       | 50%  | 104W  | Z1 recovery          | 10 min   |

**Total:** 10 + 33 + 4 + 10 = **57 min**.

### 5.4 Code

```python
def build_ramp_steps_ride(
    bike_ftp_watts: float | None = None,
) -> tuple[list[WorkoutStepDTO], str]:
    DEFAULT_BIKE_FTP = 200.0
    START_PCT = 0.60
    STEP_PCT = 0.05
    N_STEPS_REGULAR = 11      # 1-11 at 60-110% with 5% increments
    FINAL_STEP_PCT = 1.20     # step 12 at 120%
    STEP_DURATION = 180
    FINAL_STEP_DURATION = 240

    warnings = []
    if bike_ftp_watts is None:
        bike_ftp_watts = DEFAULT_BIKE_FTP
        warnings.append(
            f"⚠️ Bike FTP not found. Used default {int(DEFAULT_BIKE_FTP)}W."
        )

    steps = [
        WorkoutStepDTO(text="Warm-up easy spin",
                       duration=300, power={"units": "%ftp", "value": 50}),
        WorkoutStepDTO(text="Warm-up build",
                       duration=300, power={"units": "%ftp", "value": 60}),
    ]

    for i in range(N_STEPS_REGULAR):
        pct = START_PCT + i * STEP_PCT  # 0.60, 0.65, ..., 1.10
        watts = round(bike_ftp_watts * pct)
        steps.append(WorkoutStepDTO(
            text=f"Step {i+1} ({int(pct*100)}% FTP, {watts}W)",
            duration=STEP_DURATION,
            power={"units": "%ftp", "value": int(pct * 100)},
        ))

    final_watts = round(bike_ftp_watts * FINAL_STEP_PCT)
    steps.append(WorkoutStepDTO(
        text=(
            f"Step {N_STEPS_REGULAR+1} (120% FTP, {final_watts}W) "
            "— push to failure, ok if you stop at 60-90 sec. "
            "ERG lockout / cadence collapse = end of test."
        ),
        duration=FINAL_STEP_DURATION,
        power={"units": "%ftp", "value": int(FINAL_STEP_PCT * 100)},
    ))

    steps.append(WorkoutStepDTO(
        text="Cool-down — easy spin",
        duration=600,
        power={"units": "%ftp", "value": 50},
    ))

    return steps, "\n".join(warnings)
```

### 5.5 Notes on cadence and cooling

**Cadence:** 85-90 rpm fixed across all steps. ERG handles power, athlete handles cadence consistency. Drift adds noise to DFA a1 curve.

**Cooling:** mandatory powerful fan, cold water, ventilation. Without cooling, cardiac drift dominates by minute 30-40 → false high HRVT2.

**ERG lockout:** cadence below 70 rpm = failure signal. 60-90 sec at 120% gives one valid DFA a1 window.

## 6. Workout description templates

### 6.1 Run

```
RAMP TEST PROTOCOL (DFA a1 method)

EQUIPMENT:
- Chest HR strap MANDATORY (HRM-Dual / Polar H10)
- Treadmill recommended (or flat outdoor)
- RR interval recording enabled

WARM-UP (10 min, by feel):
- Easy jog, build to ~70-75% LTHR
- Watch will not show pace — run by feel

RAMP (8 steps × 3 min):
- Hold each pace step for full 3 minutes
- DO NOT slow down to control HR
- STOP when you cannot hold pace; skip remaining steps

PACING:
- Step 1 should feel almost trivially easy
- Real test starts around Step 5-6
- Final 2-3 steps are where you find your edge

COOL-DOWN (10 min, by feel):
- 1-2 min walk, then easy jog
- HR falls naturally below 70% LTHR
```

### 6.2 Bike

```
RAMP TEST PROTOCOL — BIKE (DFA a1 method)

EQUIPMENT:
- Chest HR strap MANDATORY
- Smart trainer in ERG mode
- Powerful fan + cold water + ventilation
- RR recording enabled

WARM-UP (10 min, ERG):
- 5 min @ 50% FTP, 5 min @ 60% FTP
- Establish cadence 85-90 rpm

RAMP (11 × 3 min + 1 × 4 min):
- Hold cadence 85-90 rpm THROUGHOUT
- ERG holds watts; you maintain cadence
- Drink every 10 min

FINAL STEP (120% FTP, 4 min):
- Push to failure
- Ok to stop at 60-90 sec
- ERG lockout / cadence < 70 rpm = end of test

COOL-DOWN (10 min, ERG):
- 50% FTP easy spin
```

## 7. Output analysis pipeline

Existing `get_activity_hrv` MCP tool:

1. **Quality check** — RR artifact %, beat count, hrv_quality (good/acceptable/poor/unusable)
2. **DFA a1 sliding window** — 120-beat window with 50% overlap
3. **Threshold detection** — find DFA a1 = 0.75 (HRVT1) and 0.5 (HRVT2) crossings; HR/power/pace ±30s averages
4. **Confidence scoring** — R², zone coverage, stability → high / medium / low / not_detected
5. **Drift alerts** — if measured differs from configured by > threshold

## 8. Auto-update zones logic

| Confidence   | R²        | Action                           |
| ------------ | --------- | -------------------------------- |
| high         | ≥0.85     | Auto-update LTHR + threshold/FTP |
| medium       | 0.70-0.85 | Suggest with confirmation        |
| low          | <0.70     | No update; recommend retest      |
| not_detected | n/a       | No update; flag protocol issue   |

Drift thresholds for triggering update:

- LTHR Run/Bike: ≥3 bpm
- threshold_pace Run: ≥5 sec/km
- FTP Bike: ≥5W

## 9. Test cadence

- Build phase: every 6 weeks
- Base phase: every 8 weeks
- Peak/taper: no testing
- After race/illness: retest before resuming structured training

Run and bike tests separated by ≥2-3 days.

## 10. Open dependencies

- **Issue #313** (`get_zones`) must be fixed before bike protocol works through MCP
- **`update_zones`** write path audit — verify writes persist
- **`create_ramp_test_tool`** must consume new build functions

## 11. Migration path

**Phase 1 — Run protocol** (no MCP dependency)

- Implement `build_ramp_steps_run`, integrate into `create_ramp_test_tool(sport='run')`
- Validate with athlete (radik) — first run ramp test

**Phase 2 — MCP fix (#313)**

- Expose `power_zones_bike` / `power_zones_run` separately
- Audit `update_zones` write/read consistency

**Phase 3 — Bike protocol**

- Implement `build_ramp_steps_ride`, integrate into `create_ramp_test_tool(sport='bike')`
- Validate with athlete

**Phase 4 — Auto-update integration**

- Implement decision matrix (§8) in `actor_update_zones`
- Add drift alerting
- Validate self-calibration cycle

## 12. Decision log

1. **Pace/power control, not HR.** HR has 30-60s lag; HR-target creates feedback loop.
2. **3-minute steps.** ≥3 DFA a1 windows per step, sufficient stabilization, compact total.
3. **5% increment.** 3-5 bpm HR delta per step.
4. **Run start 80%, bike start 60%.** Both yield genuine Z1-Z2 entry without walk/spin balast.
5. **Run top 115%, bike top 120%.** Run threshold stable; bike has calibration trap → margin needed.
6. **Bike final step 4 min, regular 3 min.** Buffer for early failure to still produce valid window.
7. **WU/CD by feel for run, ERG-targeted for bike.** Bike on ERG can hold low-power without burden.
8. **8 run steps, 12 bike steps.** Run impact-fatigue caps; bike permits longer ladder. Both n ≥ 8.

## 13. References

- Rogers B. et al. (2020). Frontiers in Physiology — DFA a1 aerobic threshold.
- Rogers B. et al. (2021). Eur J Sport Sci — DFA a1 vs blood lactate.
- Gronwald T., Rogers B. et al. (2020). Int J Sports Physiol Perform.
- AI Endurance / FatMaxxer / HRV4Training documentation.
- Friel J. — comparison reference for traditional FTHR methods.
