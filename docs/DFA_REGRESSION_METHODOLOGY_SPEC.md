# DFA α1 Threshold Detection — Methodology Spec

> Successor to `docs/RAMP_TEST_BIKE_SPEC.md` (defines the protocol). This spec
> covers the **analytical pipeline** that turns ramp-test FIT data into
> HRVT1/HRVT2 thresholds + power/pace at each. Targets methodological gaps
> identified in code review 2026-05-08.
>
> **Status:** Draft — pending implementation. Hard parts (H1, H2a, H2b) not
> yet coded; easy wins (E1, E2, E3) shipping in the same PR as
> RAMP_TEST_BIKE_SPEC §5 (current branch).
>
> **Revision history:**
> - 2026-05-08 — initial draft from code review.
> - 2026-05-08 (later) — three-round methodology review by external reviewer.
>   Added: post-fit `b`/`L` validation (§3.1.1), seed-quality guard (§3.1),
>   H2b α1 window-edge filter (§3.2.2), ROC-based gate calibration with
>   red-flag check (§3.3), `threshold_method` enum + backfill (§3.4),
>   bootstrap-based gate calibration as Phase 0 (§5).

---

## 1. Context

Current pipeline (`data/hrv_activity.py:detect_hrv_thresholds`) does:

1. Filter `dfa_timeseries` to WORK-segment points (excludes WU/CD).
2. **Linear** regression `α1 vs HR` on raw points.
3. Interpolate HR at α1 = 0.75 (HRVT1) and α1 = 0.50 (HRVT2).
4. **Linear** regression `power vs HR` on raw points (same WORK filter).
5. Interpolate power at HRVT1, HRVT2 HRs.
6. Bound-check HRVT2 HR ∈ (80, 220), HRVT2 power ∈ (50, 800), HRVT1 power ∈ (50, 500).
7. Save `threshold_r_squared`, `threshold_confidence` (single tier from R²).

This works on clean data (R² > 0.85), degrades on noise (R² ~0.6-0.7 typical),
and fails methodologically in three places. The owner's 2026-05-07 ramp test
(R²=0.62, hrvt2_power=182W silently extrapolated below real ~240W) is the
canonical failure case.

## 2. Methodological problems

### 2.1 α1 vs HR is **not linear** — it's sigmoidal (H1)

Physiology of DFA α1 (Rogers et al. 2020-2023):

```
α1 ≈ 1.0  ─┐                          plateau (fully aerobic)
            \___
                \___
α1 ≈ 0.75 ───────●─────                HRVT1 — transition begins
                    \
                     \                 transition zone
                      \
α1 ≈ 0.50 ─────────────●─────          HRVT2 — anaerobic threshold
                          \___
α1 ≈ 0.40 ────────────────────         plateau (anaerobic)
            ───────────────────►
            low HR         max HR
```

Linear fit `α1 = a·HR + b` is a **chord** through this S-curve. Both HRVT1
and HRVT2 interpolations sit on the chord, not the curve. Errors:

- For ramps that don't span the full α1 range (don't reach < 0.5), HRVT2 is
  **extrapolated** off the chord — biased high or low depending on which side
  of the inflection the data ends.
- Top-step HR estimate for HRVT2 inherits the linear bias.
- R² of the linear fit doesn't reflect quality of either threshold's local
  estimate — see §2.3.

Reference: FatMaxxer, AI Endurance, HRV4Training all use sigmoidal /
4-parameter logistic fit. Owner's 2026-05-07 R²=0.62 is partly a linear-fit
artifact — real α1 vs HR shape was nearly fine, but the chord crossed both
crossings poorly.

### 2.2 Power-HR regression mixes transients (H2)

Current code:

```python
p_hr = np.array([p["hr_avg"] for p in points])     # ALL WORK-segment points
p_power = np.array([p["power"] for p in points])
p_coeffs = np.polyfit(p_hr, p_power, 1)
```

Problem: each step's first 30-60 seconds is a **transient** — power instantly
ramps to the new ERG target, but HR lags by ~30-90 sec until cardiac drift
settles. So step 6 (177W target) for the first minute has HR matching step 5
(158 bpm at 166W). These transient (HR_low, P_high) points add noise around
the regression line.

For DFA threshold detection, where 5W shifts FTP-update by 2-3% of zone
width, this is non-negligible.

Fix: average HR + power per step using only steady-state windows (last 60-90
sec of each step). Output: 12 (HR_steady, P_steady) points → cleaner
regression.

### 2.3 Single R² obscures per-threshold reliability (E3 — partial fix in this PR)

A ramp covering HR 130-180 may have HRVT1=156 (mid-range, ~10 points around
α1=0.75) but HRVT2=172 close to top (~3 points around α1=0.50). Aggregate
R² = 0.85 hides that HRVT1 is locally great while HRVT2 is on a cliff.

Per-threshold local reliability:

```python
n_near_hrvt1 = sum(1 for p in points if 0.60 <= p["a1"] <= 0.90)
n_near_hrvt2 = sum(1 for p in points if 0.35 <= p["a1"] <= 0.65)

def _tier(n_local: int, r_squared: float | None) -> str:
    if n_local >= 5 and (r_squared or 0) >= 0.85:
        return "high"
    if n_local >= 3 and (r_squared or 0) >= 0.70:
        return "medium"
    return "low"

result["hrvt1_confidence"] = _tier(n_near_hrvt1, r_squared)
result["hrvt2_confidence"] = _tier(n_near_hrvt2, r_squared)
```

E3 (this PR) computes + persists per-threshold confidence and exposes it via
the MCP tool. Drift detector (this PR) keeps the existing `R² ≥ 0.70 / ≥ 0.85`
gate — switching gates to use per-threshold confidence happens in H1 alongside
the regression rewrite, after validation.

### 2.4 Easy wins shipped in this PR (E1, E2)

- **E1 — Slope sign check.** Linear regression with `slope ≥ 0` means α1 rose
  with HR — physically impossible (DFA falls monotonically). Treat as data
  corruption: log warning + return `None` → diagnose code `positive_slope`
  surfaces failure-mode hint to the athlete (already in
  `_ramp_failure_advice`).
- **E2 — Explicit power bound warning.** Comment said «50 < x < 500/800» but
  the silent skip didn't log. Fix: emit `logger.warning` with `(metric, raw
  value, HR target, slope)` so silent failures show up in Sentry, not just as
  a missing field.

## 3. Implementation plan — H1, H2a, H2b (deferred)

### 3.1 Sigmoid fit (H1)

```python
from scipy.optimize import brentq, curve_fit

def _sigmoid(hr, L, k, hr0, b):
    """4-parameter logistic. Returns α1 at given HR.

    L: amplitude (high-HR plateau to low-HR plateau, ≈ 0.6 typical)
    k: slope at midpoint (>0 for falling curve)
    hr0: HR at inflection (between HRVT1 and HRVT2)
    b: low-HR plateau offset (~0.4 typical, the «α1 floor» in anaerobic state)
    """
    return L / (1 + np.exp(k * (hr - hr0))) + b


def _fit_sigmoid(hr: np.ndarray, a1: np.ndarray) -> tuple | None:
    """Returns (popt, r_squared) or None if fit fails.

    Initial guess from linear pre-fit so curve_fit doesn't wander — but only
    when the pre-fit is itself trustworthy. If linear pre-fit has slope ≥ 0
    (data corruption / no ramp shape) or R² < 0.3 (too noisy to seed
    anything), fall back to physiologically-defaulted seeds. Without this
    guard, mauled-pre-fit seeds drag curve_fit into bad local minima against
    the bounds.
    """
    # Linear pre-fit gives slope+intercept → reasonable hr0, k seeds
    slope, intercept = np.polyfit(hr, a1, 1)
    a1_pred_lin = np.polyval((slope, intercept), hr)
    ss_res_lin = np.sum((a1 - a1_pred_lin) ** 2)
    ss_tot = np.sum((a1 - np.mean(a1)) ** 2)
    linear_r2 = 1 - ss_res_lin / ss_tot if ss_tot > 0 else 0

    if slope >= 0 or linear_r2 < 0.3:
        # Pre-fit untrustworthy → use physiology-grounded defaults.
        # median(hr) is a reasonable hr0 because the ramp protocol places
        # HRVT2 near the upper third of recorded HR; median is conservative.
        p0 = (0.5, 0.05, float(np.median(hr)), 0.4)
    else:
        hr0_seed = (0.625 - intercept) / slope  # midpoint of α1 range 0.4-0.85
        k_seed = -slope * 4  # rough magnitude estimate from chord slope
        p0 = (0.6, k_seed if k_seed > 0 else 0.1, hr0_seed, 0.4)

    bounds = ((0.2, 0.01, 80, 0.0), (1.5, 5.0, 220, 0.7))
    try:
        popt, _ = curve_fit(_sigmoid, hr, a1, p0=p0, bounds=bounds, maxfev=2000)
    except (RuntimeError, ValueError):
        return None
    a1_pred = _sigmoid(hr, *popt)
    ss_res = np.sum((a1 - a1_pred) ** 2)
    r_squared = 1 - ss_res / ss_tot if ss_tot > 0 else 0
    return popt, r_squared


def _solve_hr_at_a1(popt: tuple, target_a1: float) -> float | None:
    """Numerically solve sigmoid(hr) = target_a1 via brentq."""
    L, k, hr0, b = popt
    f = lambda hr: _sigmoid(hr, *popt) - target_a1
    try:
        return brentq(f, 80.0, 220.0)
    except ValueError:
        return None  # no root in interval
```

**Fallback strategy:** if `_fit_sigmoid` returns None (data too noisy or too
sparse for 4-parameter optimization), or if the post-validation in §3.1.1
rejects the parameters, fall back to current linear method. The
`threshold_method` enum (§3.4) records exactly which path each row took, so
we can audit fallback rates after H1 ships.

### 3.1.1 Sigmoid post-fit validation

Bounds in `_fit_sigmoid` allow `b ∈ [0.0, 0.7]` and `L ∈ [0.2, 1.5]` so
curve_fit can converge on noisy data. But not every set of converged
parameters is physiologically meaningful — a fit with `b = 0.55` mathematically
solves the least-squares problem yet implies «α1 floor sits above HRVT2
target = 0.50», so HRVT2 is unreachable in this athlete's data. Bounds-only
gating leaves these cases as silent failures (brentq returns None, an HRVT2
column quietly stays NULL).

Post-fit validation runs **after** `_fit_sigmoid` returns and **before**
`_solve_hr_at_a1` is called. Symmetric three-tier check on `b` and `L`:

```python
def _validate_sigmoid_params(popt: tuple) -> tuple[str, str | None, str | None]:
    """Decide whether sigmoid params are usable.

    Returns (decision, threshold_method_override, diagnostic_code) where
    decision ∈ {"full", "partial", "fallback"}.
    """
    L, k, hr0, b = popt

    # Hard rejections — sigmoid converged on shape that can't yield clean
    # thresholds. Fall back to linear pipeline.
    if b > 0.6:
        return "fallback", "linear_b_high", "sigmoid_b_above_anaerobic"
    if L < 0.15:
        return "fallback", "linear_l_low", "sigmoid_amplitude_too_shallow"

    # Soft rejection — sigmoid OK but specific threshold(s) unreachable.
    if 0.5 < b <= 0.6:
        # Floor sits between HRVT1 (0.75) and HRVT2 (0.50) → HRVT2 not in data.
        return "partial", "sigmoid_partial", "hrvt2_unreachable_floor"

    # Degraded confidence — sigmoid fit valid but shallow amplitude reduces
    # precision of both threshold interpolations. Drop confidence one tier
    # for both HRVT1 and HRVT2 (high → medium, medium → low).
    if 0.15 <= L < 0.2:
        return "full", None, "sigmoid_shallow_amplitude_degraded"

    return "full", None, None
```

Behavior matrix:

| condition          | decision  | threshold_method        | HRVT1 | HRVT2 | confidence shift |
| ------------------ | --------- | ----------------------- | ----- | ----- | ---------------- |
| `b > 0.6`          | fallback  | `linear_b_high`         | from linear | from linear | n/a |
| `L < 0.15`         | fallback  | `linear_l_low`          | from linear | from linear | n/a |
| `0.5 < b ≤ 0.6`    | partial   | `sigmoid_partial`       | OK    | NULL  | n/a              |
| `0.15 ≤ L < 0.2`   | full      | `sigmoid`               | OK    | OK    | both -1 tier     |
| else               | full      | `sigmoid`               | OK    | OK    | unchanged        |

The diagnostic codes feed into `_ramp_failure_advice` so the athlete sees
«ramp didn't penetrate anaerobic floor — repeat with one more step at top»
rather than a silent NULL.

### 3.2 Steady-state filtering (H2a + H2b)

Two related but distinct fixes for the «sliding-window contamination»
problem. **H2a** addresses the power/speed channel; **H2b** addresses the α1
channel via the same protocol-aware window logic. Both go in the same PR
because they share the work-segment boundary logic.

#### 3.2.1 H2a — Per-step steady-state averaging (power/speed channel)

```python
def _per_step_steady_points(
    dfa_timeseries: list[dict],
    work_intervals: list[dict],  # icu_intervals filtered to type=="WORK"
    *,
    steady_window_sec: int = 90,
) -> list[tuple[float, float, float]]:
    """Returns one (hr, power, speed) tuple per work step, averaged over the
    last ``steady_window_sec`` of that step.

    For steps shorter than ``steady_window_sec`` (e.g. push-to-failure final
    that bailed at 60 sec), uses whatever data is present — better than
    skipping a top-end data point.
    """
    out = []
    for iv in work_intervals:
        start, end = int(iv["start_time"]), int(iv["end_time"])
        steady_start = max(start, end - steady_window_sec)
        in_step = [p for p in dfa_timeseries if steady_start <= p.get("t_sec", 0) <= end]
        if not in_step:
            continue
        hr = np.mean([p["hr_avg"] for p in in_step if p.get("hr_avg")])
        power = np.mean([p["power"] for p in in_step if p.get("power")])
        speed = np.mean([p["speed"] for p in in_step if p.get("speed")])
        out.append((hr, power, speed))
    return out
```

Then:

```python
step_points = _per_step_steady_points(dfa_timeseries, work_intervals)
p_hr = np.array([s[0] for s in step_points])
p_power = np.array([s[1] for s in step_points])
p_coeffs = np.polyfit(p_hr, p_power, 1)
```

Note: H2a covers only the power/speed channel. The α1 channel needs its
own boundary-aware filter — see §3.2.2.

#### 3.2.2 H2b — α1 window-edge filter

`calculate_dfa_timeseries` uses a **trailing** window:
`window_start = t - window_sec`, `mask = (cum_time > window_start) & (cum_time <= t)`.
So α1(t) reflects the last 120 sec of RR data **before** t, not centered.

When `t` falls in the first ~120 sec of a new step, the trailing window
straddles the previous step → α1(t) is a blend of two intensities. Including
these blended points in the α1 vs HR regression adds noise — visible mostly
on tests with sharp step transitions (large HR delta between adjacent steps,
inconsistent pacing).

```python
def _alpha1_clean_points(
    dfa_timeseries: list[dict],
    work_intervals: list[dict],
    *,
    window_sec: int = 120,
) -> list[dict]:
    """Drop α1 points whose trailing window straddles a step boundary.

    A point at time t is «clean» only if `t - current_step_start ≥ window_sec`,
    i.e. the entire window fits inside one step.
    """
    out = []
    for p in dfa_timeseries:
        t = p.get("t_sec", 0)
        for iv in work_intervals:
            if iv["start_time"] <= t <= iv["end_time"]:
                if t - iv["start_time"] >= window_sec:
                    out.append(p)
                break  # first match wins; intervals don't overlap
    return out
```

**Floor counts (capacity check):**

| Protocol            | Steps × duration | Clean points / step | Total clean | With top-step bail-out |
| ------------------- | ---------------- | ------------------- | ----------- | ---------------------- |
| Run (current)       | 8 × 180 sec      | 60 sec (12 @ 5s)    | 96          | ~84                    |
| Bike (current)      | 12 × 180 sec     | 60 sec (12 @ 5s)    | 144         | ~132                   |

Both protocols stay above the 20-point floor required by the regression
gate. **No need to extend step duration** — H2b is a pure data-filtering
change, not a protocol redesign. Recording this explicitly so the question
«should ramp steps be 240 sec to give H2b more headroom?» does not resurface
mid-implementation.

Note: H2b filter applies **before** the α1 vs HR sigmoid (or linear) fit.
The full pre-fit pipeline becomes: `WORK-segment filter` (existing) →
`H2b α1-window-edge filter` (new) → sigmoid/linear regression.

### 3.3 Validation pipeline

Before merging H1+H2a+H2b:

1. **Synthetic data test with ROC-based threshold calibration.**
   - Generate sigmoid-shape α1 curve with known HRVT1=150 bpm, HRVT2=170 bpm.
   - Apply layered noise: gaussian RR jitter (SNR sweep), step-edge transients
     (replicating cardiac drift lag), isolated ectopic beats (1-2% of beats),
     low-frequency baseline drift (cooling drift on long tests). Skipping any
     of these produces an optimistically-clean noise model — see red-flag
     check below.
   - For each method (linear, sigmoid), sweep R² threshold ∈ [0.50, 0.99] in
     steps of 0.01. Compute `P(|measured − true| < 3 bpm | R² > threshold)`
     and `P(R² > threshold | true_data)` (recall) at each point.
   - Plot ROC. Pick R² threshold that hits target precision = `DRIFT_GATE_TARGET_PRECISION`
     (default 0.95 — configurable in `data/db/dto.py`, parallel with other
     `DRIFT_*` constants). Same procedure for `medium` tier (lower target,
     e.g. 0.80).
   - **Red-flag check.** If calibrated sigmoid threshold falls below 0.65,
     validation is **NOT passed** — sigmoid model fits the synthetic data too
     cleanly, and the noise model needs enriching before re-running calibration.
     Without this hard-stop, optimistic synthetic data freezes a too-loose
     gate that surfaces as precision regression in production.

2. **Owner historical re-process.** Re-run all owner's `ActivityHrv` rows
   through new pipeline (CLI: `python -m cli reprocess-ramp-test --method
   sigmoid --dry-run`). Print side-by-side comparison: `(activity_id, sport,
   hrvt1_hr_old, hrvt1_hr_new, Δ, hrvt2_hr_old, hrvt2_hr_new, Δ, R²_old,
   R²_new, threshold_method)`. Reject changes if hrvt2 shifts > 5 bpm in any
   test where linear R² was already > 0.85 — exception: clean linear (R²>0.85)
   that produces sigmoid Δhrvt2 = 4 bpm is **adjudicated by intra-test
   bootstrap**, not auto-rejected (see §5).

3. **R²=0.62 case** specifically. Owner's 2026-05-07 i146110855 ramp should
   produce R²>0.7 under sigmoid (improving the failure case that motivated
   this work).

4. **Fixture suite.** Add `tests/metrics/test_dfa_regression.py` with
   pre-recorded synthetic + recorded-real-from-owner `dfa_timeseries` arrays
   and expected outputs.

### 3.4 Migration story

After H1 ships:

- All future ramp tests use sigmoid by default.
- Existing `ActivityHrv` rows have linear-fit thresholds. Two options:
  - **A: Re-process all.** CLI `python -m cli reprocess-all-ramp-tests
    [--method sigmoid]`. Backfill cleanly. **Behavior change**: stored values
    shift slightly; `AthleteSettings.lthr/ftp/threshold_pace` may move on next
    drift detection.
  - **B: Lazy.** Old rows stay, new rows use sigmoid. Drift detector reads
    LIMIT 1 → only newest row matters. Old rows just historical.
- **Recommendation: B.** Re-processing changes user-facing zones for no real
  reason on most clean cases. Let the next ramp naturally update.

#### 3.4.1 `threshold_method` enum

Frozen value set so future model additions (loess, GAM, etc.) extend cleanly
without diluting existing semantics:

```python
class ThresholdMethod(str, Enum):
    linear_legacy = "linear_legacy"      # rows written before this enum existed
    linear = "linear"                    # H1: sigmoid curve_fit failed to converge
    linear_b_high = "linear_b_high"      # H1: sigmoid converged, post-val rejected (b > 0.6)
    linear_l_low = "linear_l_low"        # H1: sigmoid converged, post-val rejected (L < 0.15)
    sigmoid_partial = "sigmoid_partial"  # H1: sigmoid OK but HRVT2 unreachable (0.5 < b ≤ 0.6)
    sigmoid = "sigmoid"                  # H1: full success
```

**NULL semantics:** `threshold_method IS NULL` means «pipeline ran but
rejected before any fit was attempted» — positive slope, too few points,
out-of-range α1. NULL is **not** «unknown method»; it is «no method, by
design, because data was unfit for any model».

**Backfill (one-time, in same Alembic migration as the column add):**

```sql
ALTER TABLE activity_hrv
    ADD COLUMN threshold_method VARCHAR(32);

UPDATE activity_hrv
   SET threshold_method = 'linear_legacy'
 WHERE threshold_method IS NULL
   AND hrvt2_hr IS NOT NULL;
```

Without this backfill, lazy-migration option B would leave NULLs
indistinguishable between «pre-enum row» and «pipeline-rejected row» — a year
from now nobody will remember which was which.

## 4. Implementation plan — E1, E2, E3 (this PR)

Already on track:

- **E1** — slope sign sanity check before threshold interpolation.
- **E2** — explicit `logger.warning` for out-of-range power values.
- **E3** — schema migration + detector populates `hrvt1_confidence` /
  `hrvt2_confidence` (string tier high/medium/low) using R² + n_local point
  density. **Drift detector keeps r2-based gate** (no behavior change yet);
  per-threshold confidence is informational + queryable via MCP tool.
  Switching the gate to use `hrvt2_confidence` is part of H1's behavior
  change (after sigmoid + per-step averaging validation).

Schema migration:

```sql
ALTER TABLE activity_hrv
    ADD COLUMN hrvt1_confidence VARCHAR(16),
    ADD COLUMN hrvt2_confidence VARCHAR(16);
```

Existing `threshold_confidence` field stays — not deleted yet. After H1 ships
and per-threshold confidence is wired into the drift gate, we deprecate the
single field.

## 5. Open questions and Phase 0

### Phase 0 — Drift gate calibration via bootstrap (immediate)

Current gates `DRIFT_LTHR_BPM = 3`, `DRIFT_PACE_SEC_PER_KM = 5`, `DRIFT_FTP_WATTS = 5`
were chosen as a step away from the previous «5% relative» rule that
accepted ~8 bpm dirft on typical LTHR=160. They are not yet measurement-grounded.

A real test-retest study (one athlete, 5-10 controlled retests across stable
training blocks) would take 5-10 months. Bootstrap on existing ramp tests
gives an immediate **lower bound** without new tests:

```
σ_retest² = σ_within² + σ_biological² + σ_protocol²

σ_within   — from bootstrap on ActivityHrv rows with R² ≥ 0.85.
              Resample 80% of regression points, 1000 iterations, take SD of
              hrvt2_hr / hrvt2_pace / hrvt2_power across iterations.
σ_biological — from literature. Rogers (2021) gives ~2-3 bpm for DFA HRVT2
              in trained males. Use 2.5 as point estimate.
σ_protocol  — sport-specific. Bike (ERG, fan, controlled room) ≈ 1 bpm.
              Run (treadmill or outdoor) ≈ 2-3 bpm — variable cooling,
              ground compliance, ambient temperature.

Gate = 1.5 × σ_retest, rounded.
```

**Sport-specific gates from the start.** Unifying bike+run gates was a
shortcut — bike protocol is materially more controlled. Split:

```python
DRIFT_LTHR_BPM_BIKE = ?    # calibrated, expect ~3
DRIFT_LTHR_BPM_RUN = ?     # calibrated, expect ~5
DRIFT_PACE_SEC_PER_KM = ?  # run-only, calibrated
DRIFT_FTP_WATTS = ?        # bike-only, calibrated
```

If calibration produces a run gate of ~5 bpm and bike gate of ~3, that
itself is evidence the unified `= 3` was simultaneously too noisy on run
(wasted updates) and too strict on bike (missed real drift).

A real test-retest study, when feasible, **adjusts** these gates rather than
sets them — the bootstrap estimate is a lower bound that empirical retest
can only widen, not shrink.

### Open question O1 — Sigmoid mid-plateau for very fit athletes

Some ramp tests (very fit athletes, short test duration) don't develop a
low-α1 plateau — α1 keeps falling at top step. Sigmoid bounds clamp
`b ∈ [0.0, 0.7]` and `L ∈ [0.2, 1.5]`, but post-validation §3.1.1 may flag
these as `sigmoid_l_low` even when the underlying physiology is sound (the
ramp just didn't go deep enough to reveal the floor).

Resolution path: validation §3.3 step 1 with synthetic «truncated ramp»
cases (real sigmoid α1 curve, but data ends before α1 plateau is reached).
If sigmoid systematically misclassifies these as `linear_l_low`, the L
threshold needs revisiting OR the protocol needs an extra top step.

### Open question O2 — Direct α1 vs pace regression

Current pipeline: `α1 ~ HR` → HRVT2_hr → `speed ~ HR` → speed at HRVT2_hr.
Two regressions, errors compound. Alternative: regress `α1 ~ pace` directly
for the pace-threshold path, since pace is input (precise) while HR is
observed (noisy).

Argument for current approach: HR-port is needed independently for LTHR
(Intervals.icu's `lthr` field anchors all HR-zones). So `α1 ~ HR` is mandatory
regardless. The `speed ~ HR` regression is an **independent anchor** for
`threshold_pace` (Intervals.icu's separate pace-zone system, not derived
from LTHR). Two regressions ≠ error compounding when each anchors a
different consumer.

Argument for direct: even if both anchors stay independent, pacing-anchor's
intermediate `speed ~ HR` step adds noise that direct `α1 ~ pace` would
skip. Worth a side-by-side validation alongside H1: if direct `α1 ~ pace`
yields tighter HRVT2_pace estimates on the synthetic suite, switch the pace
path to direct (HR path stays unchanged for LTHR).

### Open question O3 — R² recalibration for sigmoid (covered in §3.3)

R² distributes differently under non-linear fits — sigmoid R² is
systematically higher on the same data than linear R² because the model
matches the true shape better. The 0.70 / 0.85 gates were calibrated against
linear; under sigmoid they would auto-update more aggressively without an
actual quality lift.

Resolution: validation §3.3 step 1 produces the ROC. Calibrated thresholds
**replace** 0.70 / 0.85 in `data/db/dto.py` as part of H1 — likely values
land around 0.78 / 0.65 (medium / high precision target = 0.95 / 0.80), but
the ROC is the source of truth.

Behavioral framing: this is a **product question**. ROC at target precision
0.95 maximizes precision at cost of recall (fewer auto-updates, higher
confidence per update); 0.80 trades precision for recall (more
self-calibration, occasional bad updates). Default = 0.95, configurable via
`DRIFT_GATE_TARGET_PRECISION`.

### Open question O4 — Adjudicating clean-but-shifted re-process cases

§3.3 step 2 rejects re-process changes where linear R² > 0.85 but sigmoid
shifts HRVT2 by >5 bpm. The 4-bpm shift is the hardest case: not auto-rejected,
but suspicious. Two possible roots:

- (a) **Real correction**: linear was systematically biased on this athlete
  (chord crossed the curve poorly), sigmoid recovers the truth.
- (b) **Sigmoid artifact**: sigmoid overfit a single outlier in the HRVT2
  region; linear was robust to it via global pull.

Adjudicator: bootstrap the sigmoid fit on the questioned activity (1000
resamples, 80% of regression points). σ_sigmoid_HRVT2 < 1 bpm → case (a),
shift is real, accept. σ_sigmoid_HRVT2 > 3 bpm → case (b), sigmoid not
robust here, keep linear values.

This logic only fires on the disputed re-process cases (rare); it does not
become part of the default sigmoid pipeline.

### Open question O5 — Per-threshold confidence + drift gate interaction

Once per-threshold confidence is the gate (post-H1), what does «low
confidence on HRVT2 but high on HRVT1» mean for FTP drift? FTP push needs
hrvt2_confidence ≥ medium. LTHR push too. So if hrvt2_confidence is low, NO
drift fires — even if HRVT1 is rock-solid. That's correct (HRVT1 doesn't
drive any update directly), but the user-facing message should explain it
explicitly («ramp didn't penetrate LT2, no zone update — repeat ramp going
harder at top»). Wired into `_ramp_failure_advice` at H1 ship time.

## 6. References

- Rogers B. et al. (2020). *DFA α1 as a heart rate variability index for the
  determination of aerobic threshold*. Frontiers in Physiology.
- Rogers B. et al. (2021). *Comparison of HRVT and traditional methods*.
  European Journal of Sport Science.
- Gronwald T., Rogers B. et al. (2020). *Real-time DFA α1 monitoring*.
  International Journal of Sports Physiology and Performance.
- FatMaxxer / HRV4Training / AI Endurance product documentation
  (proprietary methodology pages — reference for sigmoidal-fit precedent).
- `docs/RAMP_TEST_BIKE_SPEC.md` — protocol design context.
