"""Phase 2.0β2 — Pool constants for post-hoc ML residual bias correction.

Cold-start fallback for athletes with `<MIN_RACES_FOR_PER_ATHLETE_BIAS` race
records. Per-athlete bias fit lives in `race_train.py:_fit_bias_model` and
overrides these values when enough data is available.

Constants derived from user 1 simulation 2026-05-12 (`tools/race_bias_correction.py`
LOO cross-validation, n=18 Run races × 5 horizons = 90 points). Linear fit:

  bias(days_to_race) = 6.178 + 0.126 × days_to_race    (sec/km)

Decision gate result (spec §10.5.6): 🟢 GREEN — MAE drop +5.00 sec/km overall
(+6.65 on horizons ≥90d), z=+2.63 (p<0.01). See spec §10.5 for full results.

**Caveat**: pool constants are single-athlete-derived (n=22 races, half-marathon
heavy). Per-athlete fit (when n_races ≥ 5) is the principled path; pool
constants are honest cold-start strategy, not «universal truth».
"""

# Minimum race count for per-athlete bias fit. Below this, fall back to pool
# constants. Calibration logic: with n<5 races × 5 horizons = <25 points, a
# 2-parameter linear fit is dominated by noise. Threshold matches user-side
# directive «cold-start fallback for n_races < 5».
MIN_RACES_FOR_PER_ATHLETE_BIAS = 5

# Horizons sampled for per-athlete bias mini-simulation (days before race).
# Matches `tools/race_blend_simulation.py` defaults — apples-to-apples with
# the validation that produced the pool constants.
BIAS_FIT_HORIZONS = [30, 60, 90, 120, 150]

# Pool fallback constants (Phase 2.0β2 v1, derived from user 1).
# To be replaced with cross-athlete pool fit when more athletes have race data.
POOL_BIAS_INTERCEPT = 6.178  # sec/km — constant offset (model overestimate even at days_out=0)
POOL_BIAS_SLOPE = 0.126  # sec/km per day — growth with horizon (~19 sec/km at 150d)
