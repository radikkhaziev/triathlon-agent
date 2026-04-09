# DFA Alpha1 — Theory & Methodology

> Extracted from `docs/DFA_ALPHA1_PLAN.md`. Covers the science, physiological basis, mathematical algorithm, and threshold interpretation for DFA alpha1 as used in this project.

---

## What DFA Alpha1 Measures

DFA alpha1 (α1) is the short-term scaling exponent of heart rate variability, computed via Detrended Fluctuation Analysis. It quantifies the fractal correlation structure of RR-interval time series during exercise.

At rest, healthy heart rate variability shows long-range correlations (α1 > 1.0). As exercise intensity increases, these correlations break down in a predictable, monotonic fashion — making α1 a continuous, real-time marker of physiological stress and aerobic demand.

**Key physiological interpretation:**

| α1 value | Intensity zone |
|---|---|
| > 1.0 | Low intensity / rest |
| ≈ 0.75 | Aerobic threshold (HRVT1) |
| ≈ 0.50 | Anaerobic threshold (HRVT2) |
| < 0.50 | Maximal effort |

---

## Physiological Basis

DFA alpha1 tracks the autonomic nervous system's shift from parasympathetic dominance (rest, low intensity) to sympathetic dominance (high intensity). Unlike RMSSD or SDNN, which measure HRV magnitude, α1 measures the *pattern* of variability — specifically, how self-similar the RR time series is across different time scales.

The critical insight (Gronwald et al. 2020, Rogers et al. 2021): the α1 ≈ 0.75 crossing point corresponds robustly to the first ventilatory/lactate threshold across individuals and sports, without requiring a lab test or blood sampling. The α1 ≈ 0.50 crossing maps to the second threshold (HRVT2).

This makes DFA alpha1 a **non-invasive, field-usable threshold detection method** suitable for post-activity analysis of chest-strap HRV data from FIT files.

---

## Mathematical Algorithm

### 1. Detrended Fluctuation Analysis (DFA)

Given a sequence of RR intervals in milliseconds:

1. **Integrate**: compute the cumulative sum of mean-subtracted RR intervals
   ```
   y[i] = Σ(RR[k] - mean(RR))  for k=1..i
   ```

2. **Split into windows**: divide `y` into non-overlapping windows of size `n` beats, where `n` ranges from 4 to 16 (short-term scaling exponent range)

3. **Detrend each window**: fit a linear trend within each window and compute residuals

4. **Fluctuation function**: compute root mean square of residuals across all windows of size `n`
   ```
   F(n) = sqrt(mean(residuals²))
   ```

5. **Scaling exponent**: fit a line to log(n) vs log(F(n)); the slope is α1
   ```
   alpha1 = slope(log(n), log(F(n)))
   ```

Window range `n ∈ [4, 16]` beats is the standard short-term range used in exercise physiology research (as opposed to long-range α2 using larger windows).

### 2. Sliding-Window Timeseries

For per-activity tracking, DFA α1 is computed in a **sliding window** of 2 minutes (standard in the literature), stepped every 5 seconds. Each window produces one α1 value paired with the mean HR of that window.

This gives a continuous α1 vs HR curve across the activity, which is the basis for threshold detection.

### 3. Artifact Correction

Raw RR intervals from chest straps contain artifacts (missed beats, extra beats, noise from movement). Uncorrected artifacts inflate α1 artificially. The **Lipponen & Tarvainen (2019)** method is used: beat-to-beat differences exceeding 10% of local mean are flagged and interpolated.

Quality classification:
- **good**: < 5% artifact rate
- **moderate**: 5–10% artifact rate
- **poor**: > 10% artifact rate → analysis not used

---

## Threshold Detection Theory

### HRVT1 and HRVT2

Thresholds are detected by finding the HR values where the α1 timeseries crosses canonical levels:

- **HRVT1** (α1 = 0.75): aerobic threshold, equivalent to VT1 / first lactate threshold
- **HRVT2** (α1 = 0.50): anaerobic threshold, equivalent to VT2 / second lactate threshold

### Detection Strategy

Detection requires a **ramp segment** — a period of monotonically increasing HR (≥ 10 minutes), such as a progressive warm-up, step test, or ramp test. Steady-state workouts do not yield threshold estimates, but Ra/Da metrics are still computed.

Steps:
1. Find a ramp segment (monotonic HR increase, ≥ 10 min)
2. Fit a linear regression: `DFA_α1 = f(HR)`
3. Interpolate the HR values where α1 crosses 0.75 (HRVT1) and 0.50 (HRVT2)
4. Validate: R² > 0.7, and the α1 range must span from > 1.0 down to < 0.75

Confidence levels: **high** / **moderate** / **low**, reported alongside each threshold estimate.

Indoor (trainer) sessions are more reliable than outdoor rides due to absence of wind, gradient, and pacing variability.

---

## Readiness Index (Ra)

**Ra (Readiness)** compares the athlete's current aerobic output at a fixed DFA α1 level against their personal 14-day rolling baseline.

**Pa** = power (watts, for cycling) or pace (sec/100m or sec/km, for running) at a stable DFA α1 level during the warmup phase (first 15 minutes of activity).

```
Ra = (Pa_today - Pa_baseline) / Pa_baseline × 100%
```

Interpretation:
- **Ra > +5%**: excellent readiness — performing above baseline
- **Ra −5% to +5%**: normal readiness
- **Ra < −5%**: under-recovered — output is suppressed relative to baseline

Ra requires at least 14 days of Pa values to establish a baseline. Before that window, Ra is not computed.

---

## Durability Index (Da)

**Da (Durability)** measures aerobic drift within a single long session — how well the athlete maintains power/pace at a fixed DFA α1 in the second half of the workout compared to the first half.

```
Da = (Pa_second_half - Pa_first_half) / Pa_first_half × 100%
```

Requires ≥ 40 minutes of steady-state activity (not interval workouts).

Interpretation:
- **Da > 0%**: excellent endurance — output improves or holds through the session
- **Da −5% to 0%**: normal durability
- **Da < −5%**: fatigue accumulating during the session
- **Da < −15%**: overreached — significant drift, likely glycogen depletion or accumulated fatigue

---

## Scope and Limitations

**Applicable sports:** Cycling (Ride) and Running (Run). Types are normalized at the DTO layer. Requires RR interval data from a chest-strap HRM (BLE/ANT+).

**Not applicable:**
- Swimming — no RR data in water
- Weight training, walking — not meaningful for DFA threshold detection
- Wrist-based HRM — insufficient RR quality for DFA (artifacts too high)
- Activities shorter than 15 minutes — insufficient data for stable α1 calculation

**FIT file requirement:** RR intervals are embedded in the `HRV` message type of ANT+/FIT files. Activities imported from Strava (without original FIT) or recorded with wrist HRM will have `no_rr_data` status.

---

## References

- Gronwald et al. (2020) — DFA alpha1 as a non-invasive intensity biomarker
- Rogers et al. (2021) — DFA alpha1 for aerobic threshold determination
- Lipponen & Tarvainen (2019) — Artifact correction method for RR intervals
- AIEndurance — Ra (Readiness) and Da (Durability) index definitions
