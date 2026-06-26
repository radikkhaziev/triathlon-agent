# Per-Sport CTL/ATL + Plan-Aware Forecast — Spec

> Status: **shipped 2026-05-24** (Steps 1, 1.5, 2, 3, 3.5, 4). Implementation is the source of truth — see Key entry points below. This spec retains the decisions log + known limitations only.

## What was built

Per-sport CTL (200-day window, τ=42) + ATL (τ=7) written to `wellness.sport_info[]`, plus a plan-aware forecast on the LoadDetail "By sport" chart (solid past / dashed future / forecast-band tint). The rationale for each choice lives in the decisions log below; the math + storage live in code (see Key entry points).

## Decisions log (2026-05-24)

| # | Choice | Rationale |
|---|---|---|
| 1 | Window 200 дней (5τ) | Bias <1% vs ~10-15% на 90д |
| 2 | Storage: `wellness.sport_info[].atl` JSON | Без миграции, симметрично существующему `.ctl` |
| 3 | `Activity.get_windowed(days=90)` параметризован, default 90 | Banister остаётся на 90; only enrich-actor зовёт с 200 |
| 4 | TSB per-sport не рисуем | Шум на маленькой карточке |
| 5 | Forecast plan-aware (учитывает `scheduled_workouts`) | Per design `direction-b-halo.jsx::BLoadChart` |
| 6 | Forecast horizon = `max(scheduled_workouts.date)` | Если плана нет — fallback на decision #13. Decay-tail после конца плана НЕ рисуем |
| 7 | Future TSS = `scheduled_workouts.icu_training_load` | Уже в DB |
| 8 | Compute-on-read (не persist) | Один consumer (LoadDetail chart). Persist потребовал бы хуки в 4+ триггера — invalidation hell |
| 9 | `fitness_projection` не трогаем | Это зеркало Intervals (zero-load); наша per-sport — plan-aware, другая семантика |
| 10 | Horizon **общий для всех спортов** | Одна X-ось на 3 графика; спорт без своих планов decay'ит до общего горизонта |
| 11 | Horizon query narrowing: `type IN (Swim/Ride/Run) AND icu_training_load IS NOT NULL` | WeightTraining без TSS не растягивает ось |
| 12 | Overall `ctl/atl/tsb` тоже проецируются вперёд через `project_sport_load_forward` поверх суммарной TSS | Изначально планировался null-padding, но Form/TSB чарт без продолжения линии терялся в forecast-зоне. Reversal of original decision; see code-review W1 |
| 13 | Fallback горизонт = **28 дней zero-load decay** когда нет future workouts | «Что будет если перестану тренироваться» — полезная инфо. 28d ≈ один мезоцикл (4×τ_ATL + ~half-life CTL) |

## Known limitation

Если последняя wellness-row отстаёт от реальной даты (юзер не синхронизировался N дней), `today_date` указывает на дату той row'ы, и первые N дней forecast получают zero-load decay даже если в промежутке были workouts. Edge case для забросивших юзеров. Не блокер — fix при необходимости через `anchor_dt = min(today_iso_as_date, last_past_date)` + расширение planned-workouts WHERE до anchor.

## Key entry points

| File | Role |
|---|---|
| `tasks/actors/common.py` `_actor_enrich_wellness_sport_info` | Writer per-sport CTL + ATL |
| `data/metrics.py` `calculate_sport_ctl` / `calculate_sport_atl` / `project_sport_load_forward` | Math |
| `data/db/wellness.py` `update_sport_load` | JSON write |
| `api/routers/dashboard.py` `/api/training-load` | Reader endpoint, plan-aware forecast |
| `webapp/src/pages/LoadDetail.tsx` `LoadLineChart` | UI consumer (overall + per-sport) |
| `cli.py` `recalc-sport-load` | Backfill command (Step 1.5) |
