# Webhook Data Capture Spec

> Расширяем schema + dispatchers для Intervals.icu webhooks, чтобы перестать
> терять поля которые уже прилетают: weather, achievements, MMP model, TRIMP,
> rolling FTP, carbs, warmup/cooldown, RPE с часов, per-activity polarization.
>
> **Status: Phase 1 + Phase 2 shipped** (live). Only the historical **backfill**
> остаётся deferred — schema на месте, новые webhooks пишут все поля; старые
> строки остаются NULL до отдельного backfill-PR (см. §6, §10).
>
> Прямой enabler для `ML_HRV_PREDICTION_SPEC.md` §5 и `ML_RACE_PROJECTION_SPEC.md` §6.

**Related / code truth:**

| Spec / code | Связь |
|---|---|
| `docs/INTERVALS_WEBHOOKS_RESEARCH.md` | Инвентарь payload'ов — источник всех полей (10/10 event types) |
| `api/routers/intervals/webhook.py` | `_dispatch_activity_uploaded`, `_dispatch_activity_updated`, `_dispatch_achievements`, `_dispatch_sport_settings` |
| `data/db/activity.py` | `ActivityDetail.patch` (`_UNSET` sentinel, module scope `:44`), `ActivityWeather` (`:786`, `upsert_from_dto`) |
| `data/db/athlete.py:AthleteSettings` | MMP model columns (CP/W'/pMax/mmp_ftp on Ride rows) |
| `data/intervals/dto.py` | `ActivityDTO` (weather + rolling + trimp fields), `MmpModelDTO` (`:70`), `SportSettingsDTO.mmp_model` (`:98`) |
| migrations | `b3d4e5f6a7b8_phase1_webhook_data_capture`, `c4d5e6f7a8b9_phase2_webhook_data_capture` |
| `docs/ML_HRV_PREDICTION_SPEC.md` §5.1, §5.7 | Фичи (PR/achievement flag, TRIMP, carbs, weather) зависят от этой спеки |
| `docs/ML_RACE_PROJECTION_SPEC.md` §6.2, §8 | CP/W'/pMax и rolling FTP зависят от этой спеки |
| `docs/RPE_SPEC.md` | `icu_rpe` auto-fill — reference, эта спека не меняет |

---

## 1. Мотивация

Intervals.icu webhooks доставляют **~30 полей на activity** и **полный MMP model** на sport settings, но dispatcher'ы сохраняли только subset. Данные **уже прилетают** — мы их выбрасывали через `extra='allow'` в Pydantic.

Прямые последствия (до этой спеки):
- HRV-модель (§5 ML_HRV) не могла построить фичи `yesterday_had_pr`, `yesterday_temp_c`, `yesterday_trimp`, `yesterday_carbs_used`.
- Race-projection Ride-модель (§6.2 ML_RACE) не могла использовать `critical_power`/`w_prime`/`p_max`.
- `rolling_ftp` / `carbs_used` / achievements приходили в каждом ACTIVITY_ACHIEVEMENTS, но dispatcher только слал Telegram-notification.

Спека закрывала **8 gaps** без изменения архитектуры — schema-расширение + правки в существующих dispatcher'ах.

---

## 2. Scope

**Phase 1 (shipped):** weather (новая таблица `activity_weather`), MMP model (CP/W'/pMax → `athlete_settings` Ride rows), achievements + rolling FTP + CTL/ATL snapshots + carbs (`activity_details` columns), TRIMP.

**Phase 2 (shipped):** `warmup_time_sec` / `cooldown_time_sec` / `polarization_index` в `activity_details`.

### Non-goals — осознанные skip-решения (НЕ TODO)

- **ACTIVITY_DELETED — deliberate skip.** Если атлет удалил activity в Intervals UI, у нас в `activities` она **остаётся**. Обоснование: полезная история для ML train-set'а; CTL-расхождение с Intervals приемлемо (мы считаем свои recovery-метрики поверх `icu_training_load`, не дублируем их). Webhook event приходит, dispatcher намеренно не подписан.
- **ACTIVITY_ANALYZED — deliberate skip** (зафиксировано в `CLAUDE.md` Next Steps). Rare, только re-analysis уже-обработанной activity; данные уже captured через UPLOADED/UPDATED. Не стоит дублирующего write-path.
- **`icu_rpe` auto-fill — не меняем, уже работает.** Telegram inline-кнопка RPE показывается только если `icu_rpe` пуст (атлет не rate'нул на часах) → auto-source уже first-class путь. Логика в `tasks/actors/activities.py` / `RPE_SPEC.md`.
- **Отклонённые поля** (`skyline_chart_bytes`, `interval_summary` text, `stream_types`, `icu_intensity`, `session_rpe`, `strain_score`) — дубли/шум, см. `INTERVALS_WEBHOOKS_RESEARCH.md` секция «Что не записывать».
- **Per-sport CTL из webhook** — **не приходит** через API, считаем сами (`ML_RACE_PROJECTION_SPEC.md` §6.1).

---

## 3. Data model — что captured

Полные column-спеки в ORM (`data/db/activity.py`, `data/db/athlete.py`); миграции `b3d4e5f6a7b8` (Phase 1) + `c4d5e6f7a8b9` (Phase 2). Краткий обзор:

- **`activity_weather`** (новая таблица, PK = `activity_id` FK→`activities` ON DELETE CASCADE) — temp/feels-like/wind/gust/wind-dir/headwind-tailwind-%/clouds/rain/snow. Отдельная таблица, а не колонки в `activity_details`: outdoor-only (indoor/trainer/treadmill weather нет), селективный backfill, read path без weather не платит за nullable-колонки.
- **`activity_details`** (+ columns, все nullable) — Phase 1: `trimp` (уже существовал, теперь пишется и из webhook-пути), `carbs_used`, `rolling_ftp`/`rolling_ftp_delta`/`rolling_w_prime`/`rolling_p_max`, `ctl_snapshot`/`atl_snapshot`. Phase 2: `warmup_time_sec`, `cooldown_time_sec`, `polarization_index`.
- **`athlete_settings`** (+ MMP columns на **Ride rows only**) — `critical_power`, `w_prime`, `p_max`, `mmp_ftp`. Run/Swim sport_settings не содержат `mmp_model` блока → NULL by design.
- **`achievements_json`** — НЕ добавлена в `activity_details` (см. Deviations): уже покрыто таблицей `activity_achievements` (per-achievement rows, idempotent UNIQUE).

---

## 4. Dispatcher routing — per-event handling

Все три write-path'а уже в `api/routers/intervals/webhook.py`. ORM helpers — `@dual` (auto sync/async): webhook-dispatchers `await`, sync-actors зовут без `await`.

- **ACTIVITY_UPLOADED** → `_dispatch_activity_uploaded`: `ActivityWeather.upsert_from_dto(dto)` при `dto.has_weather`, плюс единый `ActivityDetail.patch(**upload_patch)` с `trimp` + Phase 2 полями. Один try/except + Sentry capture; failure не блокирует downstream `actor_update_activity_details`.
- **ACTIVITY_UPDATED** → `_dispatch_activity_updated` (тот же activity write-path).
- **ACTIVITY_ACHIEVEMENTS** → `_dispatch_achievements`: после `ActivityAchievement.save_bulk` (source-of-truth для PR-rows) добавляет `ActivityDetail.patch(rolling_ftp, rolling_ftp_delta, rolling_w_prime, rolling_p_max, ctl_snapshot, atl_snapshot, carbs_used)`. Читает `event.activity.get(...)` напрямую, без полного `ActivityDTO.model_validate` (см. Deviations). Существующая логика «`rolling_ftp_delta != 0` → sync athlete_settings» сохранена.
- **SPORT_SETTINGS_UPDATED** → `_dispatch_sport_settings` → `actor_sync_athlete_settings`: на Ride-payload извлекает `mmp_model` и роутит `critical_power`/`w_prime`/`p_max`/`mmp_ftp` через `AthleteSettings.upsert` (COALESCE-on-conflict сохраняет старые значения при partial payload).

**`ActivityDetail.patch` sentinel-контракт:** omitted field → no-op, explicit `None` → clear. Нужно потому что `trimp` приходит в UPLOADED, а rolling/achievements — в ACHIEVEMENTS через ~60s; patch не должен стирать уже заполненное. `_UNSET` на module scope (`data/db/activity.py:44`), как `AthleteGoal.update_local_fields` в `data/db/athlete.py`.

---

## 5. Backfill strategy (deferred)

Webhook'и стабильно пишут **с 2026-04-11**; активности до этой даты имеют NULL в новых колонках. Backfill **ещё не прогнан** — описание стратегии сохранено для будущего PR.

Что бэкфилить (приоритеты): weather (outdoor-only, критично для Run race-projection + HRV heat_stress), TRIMP / rolling FTP / w_prime / p_max / CTL-ATL snapshots (дёшево, в том же REST response), MMP model (один snapshot через `actor_sync_athlete_settings`). Опционально / по стоимости API-calls: `carbs_used` (до апреля 2026 скорее пусто), `achievements_json` (retroactive PRs — только последние 6 мес = train-set HRV-модели), warmup/cooldown/polarization (не срочно).

Механика: новый CLI `backfill-webhook-data <user_id> [--period 2Y] [--fields ...]` → итерация по `activities` → `GET /api/v1/athlete/{athlete_id}/activity/{id}` → patch `activity_details` + upsert `activity_weather`. Rate-limit 10 req/s. Owner (~900 activities) ≈ 90s, разовый прогон.

---

## 6. Multi-tenant / security

- `activity_weather.activity_id` FK→`activities` — tenant-isolated транзитивно через `activities.user_id`.
- `ActivityDetail.patch` scoped через `activity_id` (→ user_id).
- MMP колонки на `athlete_settings` (уже per-user FK) — наследуют tenant isolation.
- Webhook-поля (weather, achievements) — athlete-provided, не PII. `achievements_json` в Sentry breadcrumbs **не логируем body** (как факты в `USER_CONTEXT_SPEC §12`) — только наличие + тип.

---

## 7. Acceptance criteria

### Phase 1

- [x] **Migration applied** — bundled в один revision `b3d4e5f6a7b8_phase1_webhook_data_capture` (deviation от §7 «3 separate migrations»: cohesive Phase 1 reverts as a unit). Round-trip up/down verified. Dispatcher tests pass.
- [x] **`ActivityDTO` / `SportSettingsDTO` extended** — `ActivityDTO`: `trimp`, `carbs_used`, `icu_rolling_*`, `icu_ctl/atl`, full weather block (13 fields). `SportSettingsDTO.mmp_model: MmpModelDTO | None` (camelCase aliases). Old payloads parse с new fields = `None` (regression test).
- [x] **ACTIVITY_UPLOADED writes weather + trimp** — `_dispatch_activity_uploaded` → `ActivityWeather.upsert_from_dto(dto)` при `dto.has_weather` + `ActivityDetail.patch(trimp=...)`. try/except + Sentry; failure не блокирует downstream dispatch.
- [x] **ACTIVITY_ACHIEVEMENTS writes rolling + snapshot + carbs** — `_dispatch_achievements` → `ActivityDetail.patch(rolling_ftp, rolling_ftp_delta, rolling_w_prime, rolling_p_max, ctl_snapshot, atl_snapshot, carbs_used)` после `ActivityAchievement.save_bulk`.
- [x] **SPORT_SETTINGS_UPDATED writes MMP** — `actor_sync_athlete_settings` извлекает `mmp_model` только на Ride (Run/Swim NULL by design), роутит через `AthleteSettings.upsert` (COALESCE-on-conflict).
- [ ] Backfill CLI отработал на owner; `activity_weather` covers ≥95% outdoor Run/Ride. **Deferred** — новые webhooks пишут going forward, исторические строки NULL до отдельного backfill-PR. §5 описывает стратегию.
- [ ] `ML_HRV_PREDICTION_SPEC §15` / `ML_RACE_PROJECTION_SPEC §17` open questions про backfill помечены resolved. **Pending** — schema на месте, но backfill ещё не прогнан.

### Deviations (Phase 1)

- **One migration вместо трёх (§7).** Все DDL — `ADD COLUMN` / `CREATE TABLE`, revert симметричен; три ревизии только усложнили бы alembic graph без снижения риска.
- **`achievements_json` column skipped.** Уже покрыто таблицей `activity_achievements` (migration `u1b2c3d4e5f6` — per-achievement rows + raw `extra` JSONB, idempotent `UNIQUE(user_id, activity_id, achievement_id)`). Дублирующий JSONB на `activity_details` служил бы только JOIN-avoidance, который ML feature builders не используют.
- **`trimp` column skipped.** Уже существует на `activity_details` (заполнялся activities-API путём через `_DETAIL_FIELD_MAP['trimp']`). Phase 1 просто расширяет тот же столбец на webhook-путь через `ActivityDetail.patch(trimp=...)`.
- **`@dual` вместо `@with_sync_session`** на `ActivityDetail.patch` / `ActivityWeather.upsert_from_dto`. Первая итерация с `@with_sync_session` блокировала FastAPI event loop на `psycopg2` round-trip per webhook (caught post-merge). `@dual` auto-dispatches: dispatchers `await`, sync actors — без.
- **`_UNSET` sentinel на module scope** (`data/db/activity.py:44`), как `data/db/athlete.py:30`. Убирает повторные `# type: ignore` и держит sentinel вне `Base.__init_subclass__` mapped-column scan.
- **Achievements dispatcher читает dict напрямую**, без `ActivityDTO.model_validate` — matches surrounding `ActivityAchievement.save_bulk` pattern, избегает 50-field Pydantic round-trip на hot path.

### Phase 2

- [x] `warmup_time_sec` / `cooldown_time_sec` / `polarization_index` пишутся в `activity_details`. Migration `c4d5e6f7a8b9_phase2_webhook_data_capture` (3 nullable columns). `ActivityDTO` несёт три поля (Phase 1 sentinel pattern). `ActivityDetail.patch` расширен тремя `_UNSET`-default kwargs. `_dispatch_activity_uploaded` строит единый `upload_patch` dict из `dto.trimp` + Phase 2 полей, один `.patch` вызов. Tests green (`tests/api/test_webhook_dispatch.py`: `ACTIVITY_UPLOADED_PHASE2_EVENT` fixture + positive + sentinel-regression). Backfill deferred per §5.

### Deviations (Phase 2)

- **Single-call dispatcher patch.** §4 sketched separate `.patch(...)` lines per field group; shipped как один dict-builder + один `.patch(**upload_patch)` — trimp + Phase 2 поля делят один try/except + Sentry capture, log line перечисляет attempted поля. Поведение идентично.

---

## 8. Open questions

- **Achievements retroactive backfill.** Бэкфилить ли `icu_achievements` для исторических activities? Полезно для HRV-фичи `yesterday_had_pr`, но N × API calls. **Предлагаю** только последние 6 месяцев (период HRV train-set'а).
- **MMP model для не-Ride.** Run/Swim sport_settings **не содержат** `mmp_model` (research sample A.8 — только Ride). Если Intervals добавит — расширить ORM. Пока скипаем при отсутствии.
- **Weather на outdoor Ride.** Sample A.4 (VirtualRide, indoor) — `has_weather=false`. Outdoor Ride должен иметь weather как Run, но sample'а нет. **Проверить** первой outdoor Ride-активностью.
- **`carbs_used` origin.** Garmin auto-compute или ручной ввод в Intervals? Понаблюдать coverage rate за месяц.
