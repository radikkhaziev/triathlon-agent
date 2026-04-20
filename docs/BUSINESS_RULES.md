# Business Rules & Thresholds

> Full implementations are in `data/metrics.py`. This section documents the **design decisions** only.

## TSS by Sport

- **Running**: hrTSS (heart rate based) — `IF = (avg_hr - resting_hr) / (lthr - resting_hr)`
- **Cycling**: power-based TSS — `IF = normalized_power / ftp`
- **Swimming**: ssTSS — `IF = css_per_100m / pace_per_100m`

## CTL / ATL / TSB

**All CTL/ATL/TSB/ramp rate values come directly from the Intervals.icu API.** We do NOT recalculate them — Intervals.icu applies its own impulse-response model (τ_CTL=42d, τ_ATL=7d) and sport-specific TSS formulas. This is important because TrainingPeaks PMC uses different normalization coefficients, so the same athlete's TSB can differ by 5-15 points between platforms. All thresholds in this project are calibrated for Intervals.icu values.

- CTL = 42-day EMA of TSS ("fitness"), ATL = 7-day EMA ("fatigue"), TSB = CTL - ATL ("form")
- TSB > +10: under-training | -10..+10: optimal | -10..-25: productive overreach | < -25: overtraining risk

## HRV Recovery — Dual Algorithm

Both algorithms are **always computed** and stored in `hrv_analysis`. `settings.HRV_ALGORITHM` selects which feeds the recovery score. Minimum 14 days of data required.

| | Flatt & Esco (default) | AIEndurance |
|---|---|---|
| Compares | today vs 7d mean | 7d mean vs 60d mean |
| Bounds | asymmetric −1/+0.5 SD | symmetric ±0.5 SD |
| Response speed | fast (1-2 days) | slow (3-4 days) |
| Best for | acute changes, illness, travel | chronic fatigue accumulation |
| Data needed | 14 days min | 60 days for reliable bounds |

**Status interpretation:**
- `green` (above upper_bound) → train at full load
- `yellow` (between bounds) → train as planned, monitor
- `red` (below lower_bound) → reduce intensity or rest
- `insufficient_data` (< 14 days) → use readiness fallback

**SWC (Smallest Worthwhile Change):** 0.5 × SD_60d. Verdict: within noise / significant improvement / significant decline.

**CV:** < 5% very stable, 5-10% normal, > 10% unreliable (stress/illness/travel)

## Resting HR Analysis

Stored in `rhr_analysis` table. Baselines computed at 3 windows:
- **7-day** — short-term state + CV + trend
- **30-day** — primary bounds (±0.5 SD), status classification
- **60-day** — long-term context

Inverted vs RMSSD: elevated RHR = under-recovered (red), low RHR = well-recovered (green).

## ESS (External Stress Score)

Banister TRIMP-based, normalised so 1 hour at LTHR ≈ 100. Sport-agnostic.

## Banister Recovery Model

`R(t+1) = R(t) + (100 - R(t)) * (1 - exp(-1/τ)) - k * ESS(t)` — defaults: k=0.1, τ=2.0 (conservative).
Re-calibrate every 4-6 weeks via `scipy.optimize.minimize` against actual RMSSD.

## Combined Recovery Score (0-100)

**Weights:**
- RMSSD status 35% | Banister R(t) 25% | RHR status 20% | Sleep 20%

**Status → score:** green=100, yellow=65, red=20, insufficient_data=50

**Modifiers:** late sleep (>23:00) −10, CV>15% −5, RMSSD declining → flag only

**Categories:** excellent >85, good 70-85, moderate 40-70, low <40

**Recommendations:** excellent/good → zone2_ok, moderate → zone1_long, low → zone1_short, red RMSSD → skip (overrides)

**Readiness:** derived from recovery — excellent/good → green, moderate → yellow, low → red.

## Trend Analysis

Linear regression on rolling window. Per-metric thresholds in `TREND_THRESHOLDS` dict.
Directions: rising_fast/rising/stable/declining/declining_fast. Show only if r² ≥ 0.3.

## HR / Power Zones

Zones come from Intervals.icu sport-settings (`athlete_settings.{hr,power,pace}_zones`) —
per-user, typically 5-7 zones. `hr_zones` = absolute bpm; `power_zones` = **%FTP**
(pre-normalized by Intervals, not watts); `pace_zones` = %threshold (100.0 = threshold).

The chat prompt renders these straight into `SYSTEM_PROMPT_CHAT` via
`bot/prompts._zones_block()` so workout generation always uses the athlete's own zones.

Friel fallbacks (applied only when Intervals.icu hasn't been synced yet):

```
Run  (HR %LTHR):    Z1 0-72%, Z2 72-82%, Z3 82-87%, Z4 87-92%, Z5 92-100%
Bike (HR %LTHR):    Z1 0-68%, Z2 68-83%, Z3 83-94%, Z4 94-105%, Z5 105-120%
Ride (power %FTP):  Z1 0-55%, Z2 55-75%, Z3 75-90%, Z4 90-105%, Z5 105-120%
```

## Morning Report — Workout Suggestion Rules

| Condition | Allowed Training |
|---|---|
| Recovery = `excellent` + TSB > 0 | Any intensity, key workout (Z3-Z4, intervals) |
| Recovery = `good`, TSB −10..+10 | Z2 full volume |
| Recovery = `moderate` or sleep < 50 | Z1-Z2 only, 45-60 min |
| Recovery = `low` or RMSSD = `red` | Rest or Z1 ≤30 min |
| TSB < −25 | Z1-Z2 cap, flag overreaching |
| HRV delta < −15% | Z1-Z2 max |
| Ramp rate > 7 TSS/week | Flag risk, low-stress session |
