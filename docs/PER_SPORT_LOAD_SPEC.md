# Per-Sport CTL/ATL + Plan-Aware Forecast — Spec

> Status: **planned, not started**. Bookmark of the design discussion 2026-05-24.

## Goal

1. Per-sport CTL считать на расширенном окне (200 дней вместо 90) для устранения warm-up bias.
2. Добавить per-sport **ATL** рядом с CTL (зеркально, τ=7).
3. Нарисовать plan-aware forecast (CTL + ATL) на графике LoadDetail «By sport» — солидная линия в прошлом, dashed в будущем, тинт forecast-зоны.

## Confirmed decisions (discussion 2026-05-24)

| # | Choice | Rationale |
|---|---|---|
| 1 | Window 200 дней (5τ) | Bias <1% vs ~10-15% на текущих 90д |
| 2 | Storage: `wellness.sport_info[].atl` JSON | Без миграции, симметрично существующему `.ctl` |
| 3 | `Activity.get_windowed(days=90)` параметризовать с default 90 | Banister на 90 остаётся, only enrich-actor зовёт с 200 |
| 4 | TSB per-sport НЕ рисуем | Шум на маленькой карточке |
| 5 | Forecast — **plan-aware** (учитывает scheduled_workouts) | По дизайну `direction-b-halo.jsx::BLoadChart` |
| 6 | Forecast горизонт — `max(scheduled_workouts.date)` (последний запланированный день) | Если плана нет на будущее — forecast не рисуем. Decay-tail после конца плана НЕ рисуем. |
| 7 | Future TSS — `scheduled_workouts.icu_training_load` | Уже в DB |
| 8 | Compute-on-read (НЕ persist) | Один consumer (LoadDetail chart). Persist потребовал бы хуки в 4+ триггера (CALENDAR_UPDATED, suggest_workout, save_workout, enrich-actor) — invalidation hell |
| 9 | `fitness_projection` НЕ трогаем | Это зеркало Intervals (zero-load), наша per-sport проекция — plan-aware, другая семантика |
| 10 | Горизонт **общий для всех спортов** = `max(scheduled_workouts.date)` глобально | Одна X-ось на 3 графика; спорт без планов после последнего своего workout'а decay'ит до общего горизонта |

## Implementation plan

### Step 1 — Backend: per-sport CTL + ATL расчёт

- [data/db/activity.py:156](../data/db/activity.py:156) `get_windowed` — добавить параметр `days: int = 90`.
- [data/metrics.py:313](../data/metrics.py:313):
  - Extract `_calculate_sport_load_ema(activities, tau) -> dict[sport, float]`.
  - Обёртки: `calculate_sport_ctl(activities, tau=42)`, `calculate_sport_atl(activities, tau=7)`.
- [tasks/actors/common.py:30](../tasks/actors/common.py:30) `_actor_enrich_wellness_sport_info`:
  - `Activity.get_windowed(..., days=200)`.
  - Считаем `sport_ctl` и `sport_atl` параллельно из того же среза.
  - Зовём новый `Wellness.update_sport_load(sport_ctl=, sport_atl=)`.
- [data/db/wellness.py:298](../data/db/wellness.py:298) `update_sport_ctl` → переименовать в `update_sport_load(sport_ctl, sport_atl)`. JSON: `{"type": "Run", "ctl": X, "atl": Y, ...}`. Старое имя удалить (no shim).
- [data/utils.py:88](../data/utils.py:88) — добавить параллельную `extract_sport_atl(sport_info)` (5 строк).

### Step 1.5 — CLI backfill `recalc-sport-load`

После того как Step 1 задеплоен, исторические `wellness.sport_info` всё ещё содержат старые CTL (90д окно) и не содержат ATL. Backfill применяет новый алгоритм на последние 200 дней для всех активных атлетов.

**Имя:** `recalc-sport-load`
**Файл:** [cli.py](../cli.py) — новый subparser + handler `_recalc_sport_load`.

**Что делает:**
1. `User.get_active_athletes()` — `is_active=True AND athlete_id IS NOT NULL`. С `--user-id` — один юзер.
2. Для каждого атлета — итерация по последним `--days` (default 200) дням.
3. Для каждого (user, day) → `actor_user_wellness.send_with_options(kwargs={user, dt, force=True})`.

**Почему `actor_user_wellness(force=True)`, а не дёргать enrich напрямую:**
- Inactive атлет может вообще не иметь wellness-row на день N. `actor_user_wellness` сначала зовёт Intervals API → создаёт row → потом дёргает pipeline (включая `_actor_enrich_wellness_sport_info`).
- Если row есть, но не изменился — `force=True` всё равно прогоняет pipeline.
- Один путь, один actor, никакой ветвистости.

**CLI args:**
- `--user-id` (int, optional, default: all active athletes).
- `--days` (int, default: 200) — окно. Параметризовано, чтобы не править команду при будущих изменениях.
- `--dry-run` — печатает план без диспатча.

**Пейсинг:** sequential per user.
- `delay_per_day_ms = 60_000` (3× больше чем у `_sync_wellness`'а 20s).
- `user_i` начинается на `i * days * 60_000` ms (накопительный offset).
- Wall time: `N × days × 60s` (для N=22, days=200 — ~73 часа ≈ 3 суток).
- Почему 60s, а не 20s: actor_user_wellness каскадит HRV/RHR/Banister/recovery analyses с rolling 7/60d baselines — `OAUTH_BOOTSTRAP_SYNC_SPEC.md §17` предупреждает про cross-day race на этих окнах. 20s pacing мог не вмещать pipeline под API-ретраями. 60s — 3× запас. Code-review M2 от 2026-05-24.
- Это one-shot миграция; гипероптимизация не нужна.

**НЕ делаем в команде:**
- Smart skip «не зовём API если row есть» — лишняя ветвь, экономия копеечная.
- Параллельные юзеры — Sentry-шум при rate-limit, сложнее предсказать нагрузку.

### Step 2 — API: добавить ATL (past) в `/api/training-load`

- [api/routers/dashboard.py:72](../api/routers/dashboard.py:72) — `atl_swim/atl_ride/atl_run` параллельно `ctl_*`. Контракт аддитивный.

### Step 3 — Frontend: CTL+ATL на per-sport графике

- [webapp/src/api/types.ts:460](../webapp/src/api/types.ts:460) — добавить `atl_swim/atl_ride/atl_run`.
- [webapp/src/pages/LoadDetail.tsx:287](../webapp/src/pages/LoadDetail.tsx:287) collapsible «By sport»:
  - `LoadLineChart` принимает 2 линии (CTL+ATL).
  - В правом счётчике: `{ctl} CTL · {atl} ATL`.
  - `PerSportCtlCard` snapshot не трогаем (остаётся CTL-only).

### Step 3.5 — Plan-aware forecast (compute-on-read)

- [data/metrics.py](../data/metrics.py) — новая pure-функция:
  ```python
  def project_sport_load_forward(
      today_ctl: float, today_atl: float,
      daily_planned_load: dict[date, float],  # future TSS by date
      horizon_dt: date,
      today: date,
  ) -> tuple[list[tuple[date, float]], list[tuple[date, float]]]:
      """EMA forward с τ=42/7. Дни без workouts = zero load."""
  ```
- [api/routers/dashboard.py](../api/routers/dashboard.py) `/api/training-load`:
  - SELECT `MAX(start_date_local) AS horizon FROM scheduled_workouts WHERE user_id=? AND start_date_local > today`.
  - Если `horizon IS NULL` (нет будущих workouts) — return past-only (как сейчас).
  - SELECT `scheduled_workouts` WHERE `start_date_local > today AND start_date_local <= horizon` для user, group by `(date, sport)`, sum `icu_training_load`.
  - Для каждого спорта: `project_sport_load_forward(today_ctl[sport], today_atl[sport], planned[sport], horizon, today)`.
  - Конкатенация past + future в `ctl_run/atl_run/...`. `dates` тоже расширены до horizon.
  - В ответ добавить `today_date: str` (ISO). Фронт ищет индекс — клиент не зависит от порядка массива.
  - Если `today_ctl[sport] is None` — future-часть для этого спорта остаётся `None` массивом.
- [webapp/src/pages/LoadDetail.tsx](../webapp/src/pages/LoadDetail.tsx) `LoadLineChart`:
  - Перенести из [design-package/endurai/direction-b-halo.jsx:4538](../design-package/endurai/direction-b-halo.jsx:4538) `BLoadChart`: split по `todayIdx`, dashed после today, forecast-band tint, today-rule.
  - Тот же компонент используется и на overall (главная карточка), и на per-sport (By sport карточки).

### Step 4 — Tests

- `tests/metrics/test_project_sport_load_forward.py` — новый:
  - Zero load → чистый decay.
  - Constant load → steady-state.
  - Граничные: today_ctl=None, пустой план, horizon=today.
- `tests/api/test_dashboard.py` — расширенный кейс:
  - С future scheduled_workouts массивы длиннее past.
  - `today_date` корректно.
  - Без активной цели forecast = пуст.
  - `ctl_swim/ctl_ride/ctl_run` + новые `atl_*` keys присутствуют.
- `tests/test_sport_normalization.py` — `extract_sport_atl` (legacy aliases, пустой sport_info).
- `tests/tasks/test_actors.py` — actor зовёт `Wellness.update_sport_load(sport_ctl, sport_atl)`, `Activity.get_windowed(days=200)`.

## What we explicitly DON'T do

- `fitness_projection` не трогаем — это зеркало Intervals (zero-load), наша проекция другая (plan-aware).
- TSB per-sport не рисуем.
- Не кэшируем forecast в Redis (compute дешевле инвалидации).
- Не вешаем хуки в CALENDAR_UPDATED / suggest_workout / save_workout — compute-on-read снимает необходимость.
- Не выносим per-sport projection в отдельный MCP tool / morning report — нет потребителей, только UI chart.

> Backfill для исторических wellness-rows **делаем** через Step 1.5 `recalc-sport-load` — естественная нормализация через activity-обновления медленная (≥200 дней до полной стабилизации для неактивных юзеров).

## Open questions при возврате к работе

- Стоит ли при отсутствии цели всё-таки рисовать zero-load decay-forecast на N дней вперёд? Сейчас договорились: «нет цели — нет forecast». Можно пересмотреть.
- Если расход растёт за счёт плана с большим количеством будущих workouts — стоит ли пагинировать `scheduled_workouts` запрос? Сейчас не нужно (горизонт ≤ race_date обычно < 26 недель).

## Key file references

| File | Role |
|---|---|
| [tasks/actors/common.py:30](../tasks/actors/common.py:30) | `_actor_enrich_wellness_sport_info` — главный writer per-sport CTL |
| [data/metrics.py:313](../data/metrics.py:313) | `calculate_sport_ctl` — текущая функция, рефакторится |
| [data/db/wellness.py:298](../data/db/wellness.py:298) | `update_sport_ctl` — переименовывается в `update_sport_load` |
| [data/db/activity.py:156](../data/db/activity.py:156) | `get_windowed` — добавляется параметр `days` |
| [data/utils.py:88](../data/utils.py:88) | `extract_sport_ctl` — добавляется `extract_sport_atl` |
| [api/routers/dashboard.py:72](../api/routers/dashboard.py:72) | `/api/training-load` — основной endpoint |
| [webapp/src/pages/LoadDetail.tsx:287](../webapp/src/pages/LoadDetail.tsx:287) | «By sport» collapsible — UI consumer |
| [design-package/endurai/direction-b-halo.jsx:4450](../design-package/endurai/direction-b-halo.jsx:4450) | `BLoadChart` — reference design для split actual/forecast |
| [docs/INTERVALS_WEBHOOKS_RESEARCH.md:126](INTERVALS_WEBHOOKS_RESEARCH.md) | FITNESS_UPDATED payload — почему он не годится как trigger |
| [cli.py](../cli.py) | новый `recalc-sport-load` subcommand (Step 1.5) |
| [cli.py:233](../cli.py:233) | `_sync_wellness` — референс для пейсинга и пагинации `actor_user_wellness` |
