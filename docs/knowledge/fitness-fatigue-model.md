# Fitness-Fatigue Model (ESS/Banister) — Theory & Methodology

---

## External Stress Score (ESS)

ESS is a single-number daily training load metric based on Banister TRIMP (Training Impulse). It normalises effort so that approximately 1 hour of continuous training at LTHR (Lactate Threshold Heart Rate) equals 100 ESS units. This makes the score sport-agnostic and comparable across days regardless of activity type or duration.

ESS is computed per-activity from average HR and activity duration, then summed across all activities in a day to produce a daily load value. A rest day produces ESS = 0.

---

## Banister Impulse-Response (Recovery) Model

The Banister impulse-response model describes how an athlete's recovery state evolves over time in response to accumulated training load. It treats recovery as a system that decays naturally toward full recovery and is knocked down by each training stimulus.

### Recurrence Formula

```
R(t+1) = R(t) + (100 - R(t)) × (1 − exp(−1/τ)) − k × ESS(t)
```

Where:

| Symbol | Meaning |
|---|---|
| R(t) | Recovery state on day t (0–100, where 100 = fully recovered) |
| τ (tau) | Recovery time constant in days — controls how quickly R drifts back toward 100 on rest days |
| k | Load sensitivity — scales how much a given ESS reduces R |
| ESS(t) | External Stress Score for day t |

The first term `(100 - R(t)) × (1 − exp(−1/τ))` is the natural recovery pull: the further below 100, the stronger the pull back up. The second term `k × ESS(t)` is the training-induced depression of recovery state.

R(t) is clamped to [0, 100] to prevent physically nonsensical values.

### Intuition

- On a complete rest day (ESS = 0), R increases toward 100 at a rate controlled by τ.
- A hard training session (high ESS) depresses R.
- The balance between τ (recovery speed) and k (load sensitivity) determines how the model tracks real fatigue.

---

## Default Parameters

| Parameter | Default Value | Rationale |
|---|---|---|
| k | 0.1 | Conservative load sensitivity; prevents excessive R depression from a single session |
| τ (tau) | 2.0 days | Fast recovery constant; reflects typical short-term fatigue recovery |
| initial_recovery | 100.0 | Assumes athlete starts in a fully recovered state at the beginning of the history window |
| lookback_days | 90 | Rolling history window fed into the recurrence; balances computational cost vs. model stability |

---

## Role in the Combined Recovery Score

Banister recovery R(t) is one of four inputs to the composite Recovery Score (0–100):

| Component | Weight |
|---|---|
| RMSSD (HRV) | 35% |
| Banister R(t) | 25% |
| RHR | 20% |
| Sleep | 20% |

The Banister component contributes a training-load perspective that HRV and RHR alone cannot capture — for example, detecting accumulated fatigue from a high-volume block even when morning HRV appears normal.

---

## Model Calibration

The defaults are deliberately conservative. After accumulating 4–6 weeks of athlete data, k and τ can be calibrated using `scipy.optimize.minimize` by minimising the divergence between the model's R(t) trajectory and the athlete's observed RMSSD-derived recovery status. This produces athlete-specific parameters that more accurately reflect individual recovery kinetics.
