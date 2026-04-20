# Webhook Data Capture Spec

> Расширяем schema + dispatchers для Intervals.icu webhooks, чтобы перестать
> терять поля которые уже прилетают: weather, achievements, MMP model, TRIMP,
> rolling FTP, carbs, warmup/cooldown, RPE с часов, per-activity polarization.
>
> Прямой enabler для `ML_HRV_PREDICTION_SPEC.md` §5 и `ML_RACE_PROJECTION_SPEC.md` §6.

**Related:**

| Spec / code | Связь |
|---|---|
| `docs/INTERVALS_WEBHOOKS_RESEARCH.md` | Инвентарь payload'ов, источник всех полей ниже |
| `api/routers/intervals/webhook.py` | `_dispatch_activity`, `_dispatch_achievements`, `_dispatch_sport_settings` — менять их |
| `data/db/activity.py`, `activity_details.py` | ORM-модели, расширяем columns |
| `data/db/athlete.py:AthleteSettings` | MMP model columns |
| `data/intervals/dto.py` | `ActivityDTO`, `SportSettingsDTO` — добавить optional fields |
| `docs/ML_HRV_PREDICTION_SPEC.md` §5.1, §5.7 | Фичи (PR/achievement flag, TRIMP, carbs, weather) зависят от этой спеки |
| `docs/ML_RACE_PROJECTION_SPEC.md` §6.2, §8 | CP/W'/pMax и rolling FTP зависят от этой спеки |
| `docs/RPE_SPEC.md` | `icu_rpe` auto-fill меняет политику — надо согласовать |

---

## 1. Мотивация

Intervals.icu webhooks доставляют **~30 полей на activity** и **полный MMP model** на sport settings, но наши dispatcher'ы сохраняют только subset. Данные **уже прилетают** — мы их просто выбрасываем через `extra='allow'` в Pydantic.

Прямые последствия:
- HRV-модель (§5 ML_HRV_PREDICTION_SPEC) не может построить фичи `yesterday_had_pr`, `yesterday_temp_c`, `yesterday_trimp`, `yesterday_carbs_used` — полей нет в БД.
- Race-projection Ride-модель (§6.2 ML_RACE_PROJECTION_SPEC) не может использовать `critical_power`/`w_prime`/`p_max` — не пишем MMP model.
- `rolling_ftp` / `rolling_ftp_delta` / `carbs_used` / `achievements_json` — приходят в каждом ACTIVITY_ACHIEVEMENTS, но dispatcher только шлёт Telegram-notification и не persist'ит.
- FTP-история строится только опросом `athlete_settings` в момент sync — упускаем точные `rolling_ftp` snapshots на каждую activity.

Спека закрывает **8 конкретных gaps** без изменения архитектуры — только schema-расширение и правки в существующих dispatcher'ах.

---

## 2. Scope

### Phase 1 (MVP) — unlock уже-запланированных ML-фичей

1. **Weather block** на outdoor activities — новая таблица `activity_weather`.
2. **MMP model** (CP/W'/pMax) — колонки в `athlete_settings`.
3. **Achievements + rolling FTP** — колонки в `activity_details`.
4. **TRIMP** — колонка в `activity_details`.

### Phase 2 — quality-of-life

5. **icu_warmup_time / icu_cooldown_time** — колонки в `activity_details`.
6. **polarization_index per activity** — колонка в `activity_details`.

### Non-goals

7. **ACTIVITY_DELETED** — **осознанно игнорируем**. Если атлет удалил activity в Intervals UI, у нас в `activities` она **остаётся**. Обоснование: полезная история для ML train-set'а; CTL-расхождение с Intervals приемлемо (мы и так считаем свои recovery-метрики поверх `icu_training_load`, а не дублируем их). Webhook event приходит, но dispatcher остаётся skip'нутым.
8. **`icu_rpe` auto-fill** — **уже работает**: Telegram inline-кнопка RPE показывается только если `icu_rpe` пуст (атлет не rate'нул на часах). Значит auto-source используется как first-class путь. В этой спеке ничего не меняем; оставляем как reference на существующую логику в `tasks/actors/activities.py` / `RPE_SPEC.md`.
9. `skyline_chart_bytes`, `interval_summary` text, `stream_types`, `icu_intensity`, `session_rpe`, `strain_score` — отклонены (дубли/шум, см. `INTERVALS_WEBHOOKS_RESEARCH.md` секция «Что не записывать»).
10. Per-sport CTL из webhook — **не приходит** через API, считаем сами (см. `ML_RACE_PROJECTION_SPEC.md` §6.1 исправление).

---

## 3. Data model changes

### 3.1. Новая таблица `activity_weather`

```sql
CREATE TABLE activity_weather (
    activity_id             VARCHAR PRIMARY KEY REFERENCES activities(id) ON DELETE CASCADE,
    avg_temp_c              REAL,
    min_temp_c              REAL,
    max_temp_c              REAL,
    avg_feels_like_c        REAL,
    avg_wind_speed_mps      REAL,
    avg_wind_gust_mps       REAL,
    prevailing_wind_deg     INT,
    headwind_pct            REAL,
    tailwind_pct            REAL,
    avg_clouds              REAL,
    max_rain_mm             REAL,
    max_snow_mm             REAL,
    captured_at             TIMESTAMPTZ DEFAULT now()
);
```

Зачем отдельная таблица, а не колонки в `activity_details`:
- Outdoor Run/Ride имеют weather, indoor/trainer/treadmill — нет. Выносим в опциональный left-join вместо 12 nullable колонок в основной таблице.
- Бэкфилл исторический: можем селективно дозаписать только outdoor активности, не трогая остальные строки.
- Read path: queries без weather не платят за лишние колонки.

### 3.2. Расширение `activity_details`

Добавляем колонки (все nullable, REAL/INT как уместно):

| Колонка | Тип | Источник webhook | Нужно для |
|---|---|---|---|
| `trimp` | REAL | `activity.trimp` (ACTIVITY_UPLOADED) | HRV §5.1 |
| `carbs_used` | INT | `activity.carbs_used` (ACTIVITY_ACHIEVEMENTS) | HRV §5.1 |
| `rolling_ftp` | INT | `activity.icu_rolling_ftp` (ACTIVITY_ACHIEVEMENTS) | Race §6.2 `ftp_delta_30d` |
| `rolling_ftp_delta` | INT | `activity.icu_rolling_ftp_delta` | Progression/FTP change detection |
| `rolling_w_prime` | REAL | `activity.icu_rolling_w_prime` | Race §6.2 CP model history |
| `rolling_p_max` | REAL | `activity.icu_rolling_p_max` | Race §6.2 |
| `ctl_snapshot` | REAL | `activity.icu_ctl` | Точный CTL на момент activity (для ML) |
| `atl_snapshot` | REAL | `activity.icu_atl` | То же для ATL |
| `achievements_json` | JSONB | `activity.icu_achievements` | HRV §5.1 `yesterday_had_pr`, notifications |
| `warmup_time_sec` | INT | `activity.icu_warmup_time` | Phase 2 — HRV feature, activity card |
| `cooldown_time_sec` | INT | `activity.icu_cooldown_time` | Phase 2 |
| `polarization_index` | REAL | `activity.polarization_index` | Phase 2 — aggregates |

Итого **12 новых колонок** в `activity_details`. Миграция безопасная — все nullable, дефолты NULL.

### 3.3. Расширение `athlete_settings`

MMP model приходит **только для Ride** sport_settings (run/swim не содержат). Добавляем колонки:

| Колонка | Тип | Источник | Нужно для |
|---|---|---|---|
| `critical_power` | REAL | `sportSettings[type=Ride].mmp_model.criticalPower` | Race §6.2 |
| `w_prime` | REAL | `.mmp_model.wPrime` | Race §6.2 (анаэробный запас) |
| `p_max` | REAL | `.mmp_model.pMax` | Race §6.2 (пиковая мощность) |
| `mmp_ftp` | INT | `.mmp_model.ftp` | Альтернативный FTP из кривой (может отличаться от displayed `ftp`) |

Колонки на **`AthleteSettings` rows с `sport='Ride'`** — для Run/Swim остаются NULL. Альтернатива — выделить в отдельную таблицу `mmp_models(user_id, sport, cp, w_prime, p_max, updated_at)` — но для MVP не стоит, одна запись на юзера, inline нормально.

### 3.4. `activities.rpe` — без изменений

Auto-fill из `icu_rpe` **уже работает** (`tasks/actors/activities.py` + `RPE_SPEC.md`): Telegram-кнопка показывается только если `icu_rpe is None`. Эта спека логику не трогает.

---

## 4. DTO changes

### 4.1. `ActivityDTO` (`data/intervals/dto.py`)

Добавить optional поля (Pydantic):

```python
class ActivityDTO(BaseModel):
    model_config = ConfigDict(extra='allow', populate_by_name=True)
    # ... existing fields ...

    # Phase 1
    trimp: float | None = None
    carbs_used: int | None = Field(None, alias='carbsUsed')  # или snake_case в исходнике — уточнить
    icu_rolling_ftp: int | None = None
    icu_rolling_ftp_delta: int | None = None
    icu_rolling_w_prime: float | None = None
    icu_rolling_p_max: float | None = None
    icu_ctl: float | None = None
    icu_atl: float | None = None
    icu_achievements: list[dict] | None = None

    # Weather (outdoor only)
    average_weather_temp: float | None = None
    min_weather_temp: float | None = None
    max_weather_temp: float | None = None
    average_feels_like: float | None = None
    average_wind_speed: float | None = None
    average_wind_gust: float | None = None
    prevailing_wind_deg: int | None = None
    headwind_percent: float | None = None
    tailwind_percent: float | None = None
    average_clouds: float | None = None
    max_rain: float | None = None
    max_snow: float | None = None
    has_weather: bool | None = None

    # Phase 2
    icu_warmup_time: int | None = None
    icu_cooldown_time: int | None = None
    polarization_index: float | None = None
    # icu_rpe и feel — уже парсятся в текущем ActivityDTO (не трогаем)
```

Все nullable, все optional. Для исторических activities (где поля не было) парсинг не ломается.

### 4.2. `SportSettingsDTO`

```python
class MmpModelDTO(BaseModel):
    type: str | None = None
    critical_power: float | None = Field(None, alias='criticalPower')
    w_prime: float | None = Field(None, alias='wPrime')
    p_max: float | None = Field(None, alias='pMax')
    ftp: int | None = None

class SportSettingsDTO(BaseModel):
    # ... existing ...
    mmp_model: MmpModelDTO | None = Field(None, alias='mmp_model')
```

---

## 5. Dispatcher changes

### 5.1. `_dispatch_activity` (ACTIVITY_UPLOADED + ACTIVITY_UPDATED)

После `Activity.save_bulk(...)` и перед возвратом:

```python
# Weather — только если есть
if activity_dto.has_weather:
    await ActivityWeather.upsert(
        activity_id=activity_dto.id,
        avg_temp_c=activity_dto.average_weather_temp,
        min_temp_c=activity_dto.min_weather_temp,
        # ... остальные weather поля
    )

# trimp и (позже) warmup/cooldown/polarization — идут в details
await ActivityDetail.patch(
    activity_id=activity_dto.id,
    trimp=activity_dto.trimp,
    warmup_time_sec=activity_dto.icu_warmup_time,  # Phase 2
    cooldown_time_sec=activity_dto.icu_cooldown_time,  # Phase 2
    polarization_index=activity_dto.polarization_index,  # Phase 2
)
```

**`ActivityDetail.patch`** — новый helper, обновляет **только переданные поля** (sentinel `_UNSET` паттерн, как в `RACE_CREATION_SPEC §10.3 update_local_fields`), чтобы не стирать уже заполненное (например, `trimp` приходит в UPLOADED, а `achievements_json` — в ACHIEVEMENTS через 60 секунд).

### 5.2. `_dispatch_achievements` (ACTIVITY_ACHIEVEMENTS)

Сейчас только Telegram-notification. Добавить persist:

```python
await ActivityDetail.patch(
    activity_id=activity_dto.id,
    carbs_used=activity_dto.carbs_used,
    rolling_ftp=activity_dto.icu_rolling_ftp,
    rolling_ftp_delta=activity_dto.icu_rolling_ftp_delta,
    rolling_w_prime=activity_dto.icu_rolling_w_prime,
    rolling_p_max=activity_dto.icu_rolling_p_max,
    ctl_snapshot=activity_dto.icu_ctl,
    atl_snapshot=activity_dto.icu_atl,
    achievements_json=activity_dto.icu_achievements,
)
```

Существующая логика «если `icu_rolling_ftp_delta != 0` → sync athlete_settings» остаётся. Но теперь у нас **локальная история** FTP изменений — не нужно опрашивать.

### 5.3. `_dispatch_sport_settings` (SPORT_SETTINGS_UPDATED)

В `actor_sync_athlete_settings` — при обработке Ride settings:

```python
ride_settings = next((s for s in sport_settings if 'Ride' in s.types), None)
if ride_settings and ride_settings.mmp_model:
    mmp = ride_settings.mmp_model
    await AthleteSettings.upsert(
        user_id=user_id,
        sport='Ride',
        # ... existing FTP/LTHR/max_hr ...
        critical_power=mmp.critical_power,
        w_prime=mmp.w_prime,
        p_max=mmp.p_max,
        mmp_ftp=mmp.ftp,
    )
```

### 5.4. Auto-RPE — уже реализовано

См. §2.8 и §3.4. Пропускаем.

---

## 6. Backfill strategy

Webhook'и начали стабильно писать **с 2026-04-11**. Активности до этой даты в БД имеют NULL во всех новых колонках.

### 6.1. Нужно ли бэкфилить

| Поле | Backfill? | Обоснование |
|---|---|---|
| Weather | ✅ outdoor only | Критично для Run race-projection и HRV heat_stress; через Intervals REST `/activity/{id}` разово |
| TRIMP | ✅ | Дешево, приходит в тот же REST response |
| carbs_used | ⚠ опционально | Всё что до апреля 2026 — скорее всего без данных (Garmin начал ассортимент недавно) |
| rolling_ftp / rolling_ftp_delta | ✅ | История FTP — ценная для progression |
| rolling_w_prime / p_max / ctl/atl snapshot | ✅ | Приходят в том же REST call'е |
| achievements_json | ❓ | Retroactive PRs бессмысленны (мы их не showed как notification), но для HRV-фичи `yesterday_had_pr` полезно — решить по стоимости API calls |
| MMP model | ✅ один раз | Текущий snapshot через `actor_sync_athlete_settings(user)` |
| warmup/cooldown/polarization | ⚠ Phase 2 backfill | Не срочно |

### 6.2. Как бэкфилить

Новый CLI + actor:

```bash
python -m cli backfill-webhook-data <user_id> [--period 2Y] [--fields weather,trimp,rolling_ftp,achievements]
```

Внутри: итерация по `activities` юзера, `GET /api/v1/athlete/{athlete_id}/activity/{id}` → extract missing fields → patch `activity_details` + upsert `activity_weather`. Rate-limit: 10 req/sec, sleep между batch'ами.

Для owner (user 1) — 900+ activities × 100ms = ~90 секунд. Один разовый прогон.

---

## 7. Migration order

Alembic migrations в таком порядке:

1. **`N_add_activity_weather_table.py`** — CREATE TABLE, FK to `activities.id`.
2. **`N+1_add_activity_details_webhook_columns.py`** — ALTER TABLE `activity_details` ADD 12 nullable columns.
3. **`N+2_add_athlete_settings_mmp_columns.py`** — ALTER TABLE `athlete_settings` ADD 4 columns.

Все три — `upgrade` only ADD COLUMN / CREATE TABLE, `downgrade` — DROP. Нулевой риск для existing data.

---

## 8. Implementation order

1. **Migrations** (§7) — применить на dev/prod, проверить что ничего не сломалось в существующих write-path'ах.
2. **DTO extensions** (§4) — pydantic models, unit-тест «старый payload парсится без ошибок, новые поля null».
3. **ORM helpers**:
   - `ActivityWeather` модель + `upsert_from_dto()`.
   - `ActivityDetail.patch(activity_id, **fields)` — sentinel `_UNSET` pattern.
   - `AthleteSettings` — существующий upsert расширить MMP колонками.
4. **Dispatcher updates** (§5) — `_dispatch_activity`, `_dispatch_achievements`, `_dispatch_sport_settings`.
5. **Tests** — §9.
6. **Backfill CLI + actor** (§6) — с dry-run flag'ом сначала.
7. **Phase 2**: warmup/cooldown/polarization (отдельная ветка, после Phase 1 merge'а и валидации на user 1).
8. **Cross-spec cleanup**: пометить в `ML_HRV_PREDICTION_SPEC.md` §15 open question про webhook-бэкфилл как **resolved** (ссылка на эту спеку). Аналогично в `ML_RACE_PROJECTION_SPEC.md` §17.

---

## 9. Testing

### Unit

- `tests/data/test_activity_weather.py` — `ActivityWeather.upsert_from_dto` detlерминистично, upsert по activity_id.
- `tests/data/test_activity_detail_patch.py` — `_UNSET` sentinel игнорирует не-переданные поля; передача `None` **очищает** поле; patch идемпотентен.
- `tests/api/test_webhook_dispatch.py` — расширить существующие тесты:
  - ACTIVITY_UPLOADED с weather payload (sample A.7 из research) → `activity_weather` row создан.
  - ACTIVITY_ACHIEVEMENTS с `icu_achievements` → `activity_details.achievements_json` = список, `rolling_ftp` записан.
  - SPORT_SETTINGS_UPDATED с `mmp_model` → `athlete_settings.critical_power=180`.
  - **Regression:** старый payload без новых полей → нет ошибок, старые колонки заполнены как раньше.

### Integration

- `tests/data/test_backfill_webhook.py` — backfill CLI с mock Intervals REST (fixture с sample A.7) → `activity_weather` + `activity_details` заполнены.

### Manual smoke

1. Применить migrations.
2. Триггернуть тестовую активность в Intervals (любой outdoor Run).
3. `SELECT * FROM activity_weather WHERE activity_id = 'iXXX'` — поля заполнены.
4. `SELECT trimp, rolling_ftp, achievements_json FROM activity_details WHERE activity_id = 'iXXX'` — non-null.
5. Изменить FTP в Intervals → `SELECT critical_power, w_prime FROM athlete_settings WHERE user_id=1 AND sport='Ride'` — non-null.
6. Backfill на owner: `python -m cli backfill-webhook-data 1 --period 6M --fields weather,trimp,rolling_ftp`. Проверить coverage `SELECT count(*) FROM activity_weather` vs `SELECT count(*) FROM activities WHERE has_weather`.

---

## 10. Acceptance criteria

### Phase 1

- [ ] Три миграции применены, БД не сломана, existing dispatcher'ы проходят тесты.
- [ ] `ActivityDTO` / `SportSettingsDTO` парсят sample A.4 / A.7 / A.8 без warnings.
- [ ] ACTIVITY_UPLOADED пишет `activity_weather` + `activity_details.trimp`.
- [ ] ACTIVITY_ACHIEVEMENTS пишет `activity_details.rolling_ftp`, `achievements_json`, `carbs_used`, snapshots.
- [ ] SPORT_SETTINGS_UPDATED обновляет `athlete_settings.critical_power / w_prime / p_max`.
- [ ] Backfill CLI отработал на owner; `activity_weather` covers ≥95% outdoor Run/Ride в истории.
- [ ] `ML_HRV_PREDICTION_SPEC §15` и `ML_RACE_PROJECTION_SPEC §17` open questions про webhook-бэкфилл помечены resolved.

### Phase 2

- [ ] `icu_warmup_time` / `icu_cooldown_time` / `polarization_index` пишутся в `activity_details`.

---

## 11. Multi-tenant / security

Всё через существующие invariants:

- `activity_weather.activity_id` FK на `activities` — tenant-isolated через `activities.user_id` (transitive).
- `ActivityDetail.patch` — scoped через `activity_id`, который в свою очередь scoped по user_id (см. `data/db/` паттерн).
- MMP колонки в `athlete_settings` — таблица уже per-user (`user_id` FK), новые колонки наследуют tenant isolation.
- DTO-поля из webhook'а содержат athlete-provided данные (weather временные данные, achievements — физические показатели) — не PII, нормальное логирование.
- `achievements_json` — структурные данные PR'ов, в Sentry breadcrumbs **не логируем body** (как и для фактов в USER_CONTEXT_SPEC §12) — только факт наличия achievement и его тип.

---

## 12. Open questions

- **Achievements retroactive backfill.** Бэкфилить ли `icu_achievements` для исторических activities? Полезно для HRV-фичи `yesterday_had_pr` на training data, но стоит N × API call'ов. **Предлагаю:** делать только для последних 6 месяцев (это период HRV-модель train-set'а), остальное игнор.
- **MMP model для не-Ride.** Run/Swim sport_settings **не содержат** `mmp_model` блока (подтверждено в research sample A.8 — только Ride). Если Intervals когда-то добавит — расширить ORM. Пока — скипаем на парсинге если отсутствует.
- **Weather на Ride (outdoor).** В research sample A.4 (VirtualRide, indoor trainer) — `has_weather=false`. Outdoor Ride должен иметь weather как Run, но sample'а нет. **Проверить:** первой же outdoor Ride-активностью подтвердить schema совпадает, иначе добавить fix.
- **`carbs_used` origin.** Приходит из Garmin или пользователь вводит вручную в Intervals? Если Garmin auto-computes — надёжно для ML; если ручной ввод — разреженно. Понаблюдать coverage rate за месяц, решить полезность.
