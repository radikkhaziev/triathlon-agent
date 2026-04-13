# Aerobic Efficiency Metrics — EF, Pace Trends, SWOLF

Tracking aerobic progress across the three triathlon disciplines. The guiding principle: at the same relative effort (HR), a fitter athlete produces more power / covers more distance / swims with fewer strokes. Different sports require different proxies because the signals we can measure in each environment are different.

---

## Bike — Efficiency Factor (EF)

```
EF = Normalized Power (NP) / Average HR
```

- **Rising EF** = progress (more watts at the same HR).
- Example: EF 1.52 → 1.61 over 8 weeks = growing aerobic base.
- **Comparison filter:** only steady-state Z2 sessions (exclude intervals, exclude races), minimum 30 min.
- Alternative framing: *HR at fixed power* (e.g. average HR at 150 W) — easier to interpret, same information.

EF is computed upstream by Intervals.icu as `icu_efficiency_factor` — no need to recompute the math, only to filter which activities are comparable.

---

## Run — Efficiency Factor (EF)

```
EF = Speed (m/s) / Average HR
```

- **Rising EF** = progress (faster at the same HR).
- Alternative framing: *HR at fixed pace* (e.g. average HR at 6:00/km).
- **Comparison filter:** only Z2 easy runs, minimum 20 min, exclude intervals and hill repeats.
- Also informative: aerobic decoupling (Pa:Hr drift) — see [decoupling.md](decoupling.md). Decoupling < 5% on a Z2 run means the underlying aerobic base is sound at that intensity.

---

## Swim — Pace and SWOLF

HR in water is unreliable (even with a chest strap under a wetsuit), and there is no power meter, so EF is not applicable. Instead we track stroke economy and raw pace.

### Pace trend

Pace over 100 m (stored as `pace` m/s in `activity_details`). Falling time per 100 m at the same perceived effort = progress.

### SWOLF — Swim Golf

SWOLF combines time and stroke count per pool length. Lower is better: either you got faster, or you took fewer strokes, or both.

```
SWOLF (per length) = time_per_length_seconds + strokes_per_length
```

At the activity level we can reconstruct SWOLF from what Intervals.icu already gives us:

```
time_per_length    = pool_length / pace               (seconds)
strokes_per_length = pool_length / average_stride     (strokes)
SWOLF              = time_per_length + strokes_per_length
```

Where `pace` is m/s and `average_stride` is meters per stroke — both are present in `activity_details`. Only `pool_length` has to be known separately (25 m or 50 m).

**Worked example** (25 m pool, stride 0.99 m/stroke, pace 0.74 m/s):

- `time_per_length = 25 / 0.74 ≈ 33.8 s`
- `strokes_per_length = 25 / 0.99 ≈ 25.3 strokes`
- `SWOLF ≈ 59`

### SWOLF at the interval level (more precise)

Each WORK interval in the Intervals.icu `intervals` JSON has its own `average_stride`, `distance` and `moving_time`:

```
time_per_length    = moving_time * pool_length / distance
strokes_per_length = pool_length / average_stride
SWOLF              = time_per_length + strokes_per_length
```

This lets you track SWOLF per set within a session and compare like-with-like across sessions (e.g. the main 100 m sets), which is more meaningful than a session average that mixes drills, easy swim, and the main set.

### CSS — Critical Swim Speed

Periodic test (400 m + 200 m all-out) yields CSS — an estimate of the swim lactate threshold expressed as seconds per 100 m. A falling CSS is a direct indicator of aerobic threshold improvement and is the swim analogue of FTP or LTHR.

### Pace consistency

Spread of splits inside a main set. The tighter the splits, the better the pacing control and the better the aerobic durability at that pace.

---

## Why these three metrics instead of CTL / DFA / HRV

- **CTL trend** shows whether training load is rising, not whether the athlete is getting more efficient at that load.
- **HRVT1 (DFA α1)** pinpoints the aerobic threshold but requires a ramp-style workout and clean RR data — not something every Z2 session can give you.
- **EF / SWOLF / pace** are the only metrics that can be computed from *ordinary easy sessions*, which dominate training volume. That makes them the right tool for continuous progress tracking.
- **Recovery score + EF trend** together give the full picture: recovery on the rest side, adaptation on the training side.
- **Decoupling** complements EF: an EF that looks high but with decoupling > 10% means the athlete can hit the number briefly but can't sustain it — the aerobic base is thinner than the EF alone suggests.

---

## Comparability filters (for trend analysis)

For a trend in EF or SWOLF to be meaningful, compare only sessions that are actually comparable:

1. **Minimum duration:** bike ≥ 30 min, run ≥ 20 min, swim ≥ 15 min.
2. **Steady-state Z2 only:** average HR inside the Z2 band from LTHR (bike 68–83%, run 72–82%; see `BUSINESS_RULES.md`).
3. **Exclude:** interval sessions, races, brick sessions.
4. Optional stricter filter: `variability_index < 1.05` to exclude interval sessions, `decoupling < 10 %` to exclude sessions with severe cardiac drift.
