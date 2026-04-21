# OAuth Bootstrap Sync Spec

> После успешного Intervals.icu OAuth автоматически бэкфилим историю
> (wellness, activities, training load) за год назад. Работает в фоне через
> существующий Dramatiq-pipeline с батч-диспатчем и persistent state для UX
> прогресса и resume'а при сбоях.
>
> Закрывает [issue #226](https://github.com/radikkhaziev/triathlon-agent/issues/226).

**Related:**

| Issue / Spec / code | Связь |
|---|---|
| [#226](https://github.com/radikkhaziev/triathlon-agent/issues/226) | Основной трекер |
| `api/routers/intervals/oauth.py:200-208` | Текущий minimal auto-sync — расширяем |
| `cli.py` (`sync-wellness`, `sync-activities`) | Референс per-day dispatch с 20s delay |
| `tasks/actors/wellness.py`, `activities.py`, `training_log.py` | Per-day actors, **переиспользуем as-is** |
| `tasks/actors/athlete.py:actor_sync_athlete_settings` | Разовый sync — тоже переиспользуем |
| `tasks/actors/workouts.py:actor_user_scheduled_workouts` | Планы на 14 дней вперёд |
| `bot/scheduler.py:70-73` | Daily sync cron — проверить что не конфликтует |
| `docs/MULTI_TENANT_SECURITY.md` T2 | Per-user credentials, OAuth-scoped |
| `docs/WEBHOOK_DATA_CAPTURE_SPEC.md` | Новые поля (weather/MMP/trimp) бэкфилим в той же pipeline |

---

## 1. Мотивация

Сейчас (`api/routers/intervals/oauth.py:200-208`): при первом OAuth-подключении dispatcher отправляет **две** задачи — `actor_sync_athlete_settings` и `actor_user_wellness(today)`. Activities, исторические wellness, training_log — **не загружаются**. Результат: атлет заходит в webapp после OAuth → `/wellness` и `/activities` пустые → непонимание «а бот вообще работает?».

Issue #226 требует загрузить 30/90/14 дней (wellness/activities/calendar). Но для ML-фичей из `ML_HRV_PREDICTION_SPEC` и `ML_RACE_PROJECTION_SPEC` нужна **длинная история** (180+ дней). Делаем **годовой бэкфилл** по умолчанию, с возможностью ограничить.

Intervals.icu API принимает **range-параметры** (`oldest`/`newest`) на `/wellness` и `/activities`, т.е. годовая история достаётся **2 API-запросами**, не 730. Ранний черновик этой спеки проектировал per-day loop — отказались (см. §4). Реальное время бэкфилла — **1-3 минуты** (fetch + persist + downstream analysis hooks), а не 2 часа. Long Telegram-notification всё равно нужен — training_log recompute и production of daily analysis rows поверх свежезалитых данных занимает время.

---

## 2. Scope

### Phase 1 (MVP)

- Новая таблица `user_backfill_state` для tracking и resume.
- Новые **bulk-range actors** (`actor_bootstrap_wellness_range` / `actor_bootstrap_activities_range`) — одна HTTP-сессия на весь период вместо 365 per-day вызовов (§4, §11).
- Новый actor `actor_bootstrap_sync(user_id, period_days=365)` — entry point, дёргает два bulk actors + ставит self-rescheduling finalize.
- Обновление `oauth.py:callback` — триггер bootstrap_sync при `was_new=True` + fast-path (сегодня, settings, **goals**, planned workouts).
- **Синхронный первый день** (today wellness + settings + today activities + race goals) — быстрый UX: webapp показывает non-empty state в течение 30 секунд.
- Telegram-notification: start + completion (с coverage stats).
- Idempotency: повторный OAuth не триггерит новый бэкфилл если `user_backfill_state.status='completed'` менее 7 дней назад.
- Progress endpoint `GET /api/auth/backfill-status` для webapp-прогресса.

### Phase 2 — quality-of-life

- Progress bar в `/settings` (poll `/api/auth/backfill-status` каждые 5s пока `status='running'`).
- `<BackfillButton />` компонент с state machine (§9.5) — обслуживает все варианты: первый импорт, retry после failed, re-import после empty-case, пересинк >7d после completed.
- `POST /api/auth/retry-backfill` endpoint с 1h rate limit (§9.4).
- Configurable period (по умолчанию 365, можно ужать до 180/90 через query param).

### Non-goals

- Concurrent parallel day-sync — даёт 4-5x ускорение, но съедает rate limit и ломает существующие 20s-throttle инварианты. Сейчас не нужно.
- Backfill webhook-only полей (weather/MMP/achievements) — отдельная история в `WEBHOOK_DATA_CAPTURE_SPEC.md` §6. При bootstrap sync эти поля заполняются **только для новых activities** (через REST API response); исторический бэкфилл webhook-полей — разовый прогон после merge'а этой спеки.
- Рекурсивный pipeline (day N → schedule day N+1) — рассмотрен, отклонён: нет observability (очередь пуста), ломается при рестарте worker'а, сложнее resume. Batch dispatch + state table решают те же задачи с прозрачным прогрессом.

---

## 3. Текущее состояние

**`oauth.py` сейчас:**

```python
# api/routers/intervals/oauth.py:200-208
if was_new:
    try:
        actor_sync_athlete_settings.send(user=user_dto)
        actor_user_wellness.send(user=user_dto)
        logger.info("Dispatched initial sync for new athlete user_id=%d", user_id)
    except Exception:
        logger.exception("Failed to dispatch initial sync for user_id=%d", user_id)
```

Покрывает:
- ✅ Athlete settings (LTHR/FTP/thresholds).
- ✅ Wellness на сегодня (1 день).
- ❌ Activities — никак.
- ❌ Исторические wellness.
- ❌ Training log (pre/actual/post).
- ❌ Planned workouts.
- ❌ Telegram notification.
- ❌ Tracking прогресса.

**CLI-эталон:**

```bash
python -m cli sync-wellness <user_id> [period]   # per-day dispatch, 20s delay
python -m cli sync-activities <user_id> [period] [--force]
python -m cli sync-training-log <user_id> [period]
```

Все три уже делают нужный нам per-day loop. **Новый actor** должен просто обернуть их логику диспатча, добавив state-tracking.

---

## 4. Архитектура — bulk range fetch + self-rescheduling finalize

```
OAuth callback (was_new=True)
    │
    ├─ Synchronous (blocking, < 5s):
    │    • actor_sync_athlete_settings.send()
    │    • actor_sync_athlete_goals.send()              ← RACE_A/B/C видны сразу
    │    • actor_user_wellness.send(dt=today)
    │    • actor_fetch_user_activities.send(today, today)
    │    • actor_user_scheduled_workouts.send()         ← 14 дней вперёд
    │    • Telegram: "🔄 Intervals подключён. Загружаю историю за год..."
    │
    └─ Enqueue actor_bootstrap_sync(user_id, period_days=365)
         │
         ▼
    actor_bootstrap_sync (single dramatiq task)
         │
         ├─ UserBackfillState.upsert(user_id, status='running', total_days=period_days)
         │
         ├─ Два bulk actors ПАРАЛЛЕЛЬНО:
         │    actor_bootstrap_wellness_range.send(user, oldest, newest)
         │    actor_bootstrap_activities_range.send(user, oldest, newest)
         │
         └─ actor_bootstrap_finalize.send_with_options(user_id, delay=60_000)
               │
               ▼ (poll-until-drained loop, см. §6.3)
         finalize:
           if not drained and attempt < 5:
               self.send_with_options(delay=60_000, attempt+1)
           else:
               compute final status, Telegram notify
```

**Почему bulk range, а не per-day loop:**

- **1 API request на endpoint вместо 365.** `GET /wellness?oldest=…&newest=…` и `GET /activities?oldest=…&newest=…` оба принимают диапазон. 20s-delay-per-day был защитой от rate-limit, которой не нужно при одном запросе.
- **Real wall-clock время — 1-3 минуты**, не 2 часа. Bulk-fetch возвращает 365 dataclass-ов за один roundtrip (~1-3s), chunked INSERT за ~5-15s, плюс downstream analysis hooks (HRV, recovery score, training_log recompute) — ещё 1-2 минуты. Пользователь видит полную историю, а не ждёт.
- **Нет 730 delayed tasks в Redis.** Вместо этого — 2 «рабочие» задачи + 1-5 finalize-попыток.
- **Failure isolation**: если `wellness_range` упал — `activities_range` всё равно проходит, UI показывает частичный прогресс, finalize помечает `status='failed'` если counter не добежал.

**Почему не group_callbacks / pipeline:** Dramatiq's `group()` + `group_callbacks` подошёл бы, но требует `GroupCallbacks` middleware и делает pipeline непрозрачным в `/health`. Self-rescheduling finalize проще для этой задачи: он сам проверяет `UserBackfillState`, не зависит от Dramatiq-контекста, тривиально дебажится.

**Scheduler (today) работает параллельно.** `actor_user_wellness(today)` от cron'а продолжает крутиться каждые 10 мин; bulk range actor трогает только `oldest..today-1`. Конфликта нет.

---

## 5. Data model

### 5.1. Таблица `user_backfill_state`

```sql
CREATE TABLE user_backfill_state (
    user_id         INTEGER PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,
    started_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    finished_at     TIMESTAMPTZ,
    status          VARCHAR(16) NOT NULL DEFAULT 'running',  -- running | completed | failed
    period_days     INTEGER NOT NULL,
    total_days      INTEGER NOT NULL,                        -- обычно == period_days
    completed_days  INTEGER NOT NULL DEFAULT 0,              -- инкремент per-day actor'ом
    failed_days     INTEGER NOT NULL DEFAULT 0,
    last_error      TEXT,                                    -- msg последнего фейла
    last_synced_dt  DATE                                     -- самый ранний успешно-обработанный день
);
```

**Почему PRIMARY KEY на user_id, а не SERIAL + unique:** у юзера в любой момент ровно **один** активный backfill (нет смысла запускать второй пока первый идёт). Повторный OAuth либо upsert'ит в ту же row (если `status != 'running'`), либо skip'ит (§7 idempotency).

### 5.2. Progress counter — single axis, atomic increment

`total_days = period_days` — **одна метка на календарный день**, а не два (wellness + activities отдельно). Прогресс-бар показывает `142 / 365`, не `284 / 730` — соответствует пользовательской модели «год истории».

Увеличение — **атомарный SQL-апдейт**, не ORM read-modify-write, чтобы исключить lost-update race при параллельных per-day commits внутри bulk actors:

```python
# data/db/user_backfill_state.py
@dual
def increment_completed(cls, user_id: int, *, session: Session) -> None:
    session.execute(
        update(cls)
        .where(cls.user_id == user_id, cls.status == 'running')
        .values(completed_days=cls.completed_days + 1)
    )
    session.commit()

@dual
def increment_failed(cls, user_id: int, error: str | None = None, *, session: Session) -> None:
    session.execute(
        update(cls)
        .where(cls.user_id == user_id, cls.status == 'running')
        .values(
            failed_days=cls.failed_days + 1,
            last_error=(error or '')[:500],
        )
    )
    session.commit()
```

Bulk actors (§6.2) вызывают `increment_completed` **один раз на календарный день**, когда обе ветки (wellness + activities) для даты успешно сохранены. Логика:

- `actor_bootstrap_wellness_range` пишет `wellness_days_done[dt] = True` в Redis set.
- `actor_bootstrap_activities_range` пишет `activities_days_done[dt] = True` в Redis set.
- После каждой отметки оба actor'а проверяют пересечение; при совпадении — `increment_completed(user_id)`.

Альтернатива — хранить завершённые дни в полях `user_backfill_state.wellness_days_done` / `activities_days_done` как `JSONB` массивы, но Redis-set чище (TTL 24h, самоочищается, не шумит в DB).

---

## 6. Pipeline

### 6.1. OAuth callback — синхронная часть

```python
# api/routers/intervals/oauth.py — замена строк 200-208
if was_new:
    try:
        # Fast path: today's data + settings + goals — все .send() возвращают
        # немедленно, per-actor выполнение ≤5s каждый, юзер видит non-empty
        # webapp через ~10-30 сек.
        actor_sync_athlete_settings.send(user=user_dto)
        actor_sync_athlete_goals.send(user=user_dto)         # RACE_A/B/C в /settings
        actor_user_wellness.send(user=user_dto, dt=date.today().isoformat())
        actor_fetch_user_activities.send(
            user=user_dto,
            oldest=date.today().isoformat(),
            newest=date.today().isoformat(),
        )
        actor_user_scheduled_workouts.send(user=user_dto)    # 14 дней вперёд

        # Slow path: year-long backfill (фон, ~1-3 мин в норме)
        actor_bootstrap_sync.send(user_id=user_id, period_days=365)

        # Telegram "start" — через существующий TelegramTool pattern
        # (см. tasks/actors/athlets.py:_actor_send_goal_notification для образца).
        _actor_send_bootstrap_start_notification.send(user=user_dto)
    except Exception:
        logger.exception("Failed to dispatch bootstrap sync for user_id=%d", user_id)
        # OAuth всё равно считается успешным — просто без бэкфилла
```

**`_actor_send_bootstrap_start_notification`** — новый helper в `tasks/actors/bootstrap.py`, использует `TelegramTool(user=dto).send_message(text=...)` (sync в dramatiq actor'е). Не через несуществующий generic `actor_send_telegram_message`.

### 6.2. `actor_bootstrap_sync` — планировщик

```python
@dramatiq.actor(max_retries=3)
def actor_bootstrap_sync(user_id: int, period_days: int = 365) -> None:
    with get_sync_session() as session:
        user = User.get_by_id(user_id, session=session)
        user_dto = UserDTO.model_validate(user)

        # Idempotency (§7)
        existing = UserBackfillState.get(user_id, session=session)
        if existing and existing.status == 'running':
            logger.info("Bootstrap already running for user %s, skip", user_id)
            return
        if existing and existing.status == 'completed' and \
           (now() - existing.finished_at).days < 7:
            logger.info("Bootstrap recently completed for user %s, skip", user_id)
            return

        UserBackfillState.upsert(
            user_id=user_id,
            started_at=now(),
            status='running',
            period_days=period_days,
            total_days=period_days,
            completed_days=0,
            failed_days=0,
            last_error=None,
            finished_at=None,
        )

    today = date.today()
    oldest = (today - timedelta(days=period_days)).isoformat()
    newest = (today - timedelta(days=1)).isoformat()  # today уже в fast-path

    # Два bulk actor'а — работают параллельно, каждый делает 1 API-request.
    actor_bootstrap_wellness_range.send(user=user_dto, oldest=oldest, newest=newest)
    actor_bootstrap_activities_range.send(user=user_dto, oldest=oldest, newest=newest)

    # Finalize сам себя перевызывает каждую минуту до 5 попыток, проверяет drain'нутость.
    actor_bootstrap_finalize.send_with_options(
        args=(user_id,),
        kwargs={'attempt': 1},
        delay=60_000,  # 60s — даём bulk actor'ам время стартануть
    )
```

### 6.2.1. `actor_bootstrap_wellness_range` — bulk wellness

```python
@dramatiq.actor(max_retries=3)
def actor_bootstrap_wellness_range(user: UserDTO, oldest: str, newest: str) -> None:
    """One HTTP call → chunked per-day save. Each successfully-saved day bumps
    the shared-counter bookkeeping (§5.2) so finalize can drain cleanly.
    """
    with IntervalsSyncClient.for_user(user) as client:
        rows = client.get_wellness_range(oldest=oldest, newest=newest)  # 1 REST call

    saved_dates: list[date] = []
    for row in rows:
        try:
            Wellness.upsert(user_id=user.id, **row.model_dump())
            saved_dates.append(row.date)
        except Exception as e:
            UserBackfillState.increment_failed(user.id, error=str(e))

    # Trigger downstream analysis per saved date (HRV baseline, recovery, etc.)
    for dt in saved_dates:
        _actor_compute_wellness_analysis.send(user=user, dt=dt.isoformat())

    # Record calendar-day completion in Redis; finalize reads intersection.
    _mark_wellness_days_done(user.id, saved_dates)
    _maybe_advance_counter(user.id, saved_dates)
```

`_mark_wellness_days_done` кладёт `SADD backfill:{user_id}:wellness_days {dt1},{dt2},…` с TTL 24h. `_maybe_advance_counter` для каждой `dt` из `saved_dates` делает `SISMEMBER backfill:{user_id}:activities_days dt` — если hit, вызывает `UserBackfillState.increment_completed(user_id)` **атомарно** через SQL (§5.2).

### 6.2.2. `actor_bootstrap_activities_range` — bulk activities

Симметрично `wellness_range`: один `client.get_activities_range(oldest, newest)`, chunked UPSERT (`Activity.save_bulk` уже есть), per-activity dispatch `actor_fetch_activity_details` для streams/intervals, зеркально помечает `activities_days_done` в Redis и двигает counter.

### 6.3. `actor_bootstrap_finalize` — self-rescheduling drain check

Finalize не может полагаться на clock-based delay, потому что bulk-actor'ы с ретраями по 429 могут финишировать позже предсказанного момента. Вместо этого — periodic poll: каждые 60s проверяет `completed_days + failed_days >= total_days`; если не drained — перепланирует сам себя с ограничением по попыткам.

```python
_FINALIZE_POLL_SEC = 60
_FINALIZE_MAX_ATTEMPTS = 15  # 15 × 60s = 15 min upper bound

@dramatiq.actor(max_retries=0)  # ручной retry через self.send_with_options
def actor_bootstrap_finalize(user_id: int, attempt: int = 1) -> None:
    with get_sync_session() as session:
        state = UserBackfillState.get(user_id, session=session)
        if not state or state.status != 'running':
            return  # кто-то уже завершил

        user = User.get_by_id(user_id, session=session)
        user_dto = UserDTO.model_validate(user)

        drained = state.completed_days + state.failed_days >= state.total_days
        if not drained and attempt < _FINALIZE_MAX_ATTEMPTS:
            # Ждём drain'а — bulk actor'ы ещё работают или ретраят.
            actor_bootstrap_finalize.send_with_options(
                args=(user_id,),
                kwargs={'attempt': attempt + 1},
                delay=_FINALIZE_POLL_SEC * 1000,
            )
            return

        # Drained (или превысили лимит попыток — force close).
        # Training log — финальный шаг: pre/actual/post из свежих wellness+activities.
        actor_recalculate_training_log.send(user=user_dto)

        # Три исхода — см. §7:
        if state.completed_days == 0 and state.failed_days == 0:
            # Empty-import: Intervals был пуст, не наша вина. 'completed' с sentinel
            # чтобы UI показал «повторить через час».
            final_status = 'completed'
            final_error = 'EMPTY_INTERVALS'
        else:
            success_rate = state.completed_days / max(state.total_days, 1)
            # Допускаем shallow gaps (<10%) — Intervals иногда отдаёт частичные ответы.
            final_status = 'completed' if success_rate >= 0.9 else 'failed'
            final_error = state.last_error  # keep whatever was recorded

        UserBackfillState.update(
            user_id=user_id,
            status=final_status,
            finished_at=now(),
            last_error=final_error,
            session=session,
        )

    # Telegram notification — через dedicated actor, не generic send.
    _actor_send_bootstrap_completion_notification.send(
        user=user_dto,
        status=final_status,
        completed=state.completed_days,
        failed=state.failed_days,
        total=state.total_days,
    )
```

**Почему `max_retries=0`** — ретраи управляются вручную через `send_with_options(delay=...)`. Dramatiq автоматические retries усложнили бы логику (attempt counter путался бы).

**Force-close после 15 минут:** если bulk actor'ы зависли (Intervals API даунтайм, OAuth revoke mid-backfill), не оставляем state в `running` навсегда. Помечаем `failed` с текущими counters. Юзер сможет retry (§9.4).

### 6.4. Existing per-day actors остаются нетронутыми

`actor_user_wellness(dt)` / `actor_fetch_user_activities(today, today)` из scheduler'а **не модифицируются** — они и дальше обслуживают daily sync. Bootstrap использует **отдельные** bulk actors (§6.2.1 / §6.2.2), чтобы per-day path остался простым и без знаний о `UserBackfillState`. Counter-bookkeeping живёт только внутри bulk actors через Redis + атомарный SQL (§5.2).

Deauth-guard — в начале каждого bulk actor'а:

```python
user = User.get_by_id(user.id)  # refresh
if user.intervals_auth_method == 'none':
    logger.info("bootstrap bulk: OAuth revoked for user %d, aborting", user.id)
    UserBackfillState.mark_failed(user.id, error='OAuth revoked during backfill')
    return
```

Это предотвращает накрутку `failed_days` до 365 после disconnect в середине бэкфилла.

---

## 7. Idempotency

Пять сценариев повторного bootstrap'а:

| Сценарий | Cooldown | Поведение |
|---|---|---|
| Пользователь уже athlete, OAuth-подтверждение (refresh token) | — | `was_new=False` → не триггерим bootstrap. Существующий код OAuth это уже делает. |
| `status='running'` | — | `actor_bootstrap_sync` видит running-state → skip. |
| `status='completed'`, `completed_days > 0`, `finished_at < 7d ago` | 7 дней | Skip (успешно загружено, webhooks обслуживают incremental updates). |
| `status='completed'`, `completed_days == 0` | **1 час** | Это **empty-import case**: Intervals был пуст (юзер только что зарегался, Garmin ещё не доехал). Короткий cooldown позволяет retry пока Intervals догоняет. |
| `status='failed'` / `status='completed'` >7d ago | — | Разрешаем rerun (upsert state, old row overwritten). |

**Empty-import logic** — в finalize (§6.3) различаем два исхода при `completed_days == 0`:

```python
if state.completed_days == 0 and state.failed_days == 0:
    # Intervals.icu вернул пустые ответы — ни успехов, ни фейлов.
    # Скорее всего юзер только что зарегался в Intervals, Garmin-sync ещё не произошёл.
    final_status = 'completed'  # не failed — API отработал, просто данных нет
    state.last_error = 'EMPTY_INTERVALS'  # sentinel для UI
elif success_rate >= 0.9:
    final_status = 'completed'
else:
    final_status = 'failed'
```

Важно: `EMPTY_INTERVALS` → `status='completed'`, но кнопка «Загрузить» остаётся видна (см. §9.5) с коротким cooldown'ом. Юзер может retry через час когда Intervals догонит Garmin.

Внутри per-day actor'ов идемпотентность уже обеспечена — `save_bulk` с ON CONFLICT для activities, upsert для wellness. Повторный sync того же дня — no-op.

---

## 8. Concurrency с daily scheduler

Scheduler (`bot/scheduler.py`):
- `actor_user_wellness(today)` — каждые 10 мин.
- `actor_fetch_user_activities(today)` — каждые 10 мин.
- `actor_recalculate_training_log` — weekly cron.

Bulk actors бэкфилла работают на `oldest .. today-1`, scheduler — на `today`. Пересечения дат нет **by construction** (см. §6.2: `newest = today - 1 day`).

**Пограничный случай — полночь:** bootstrap стартовал 23:50, `today` закрепился; к 00:01 `today` сменился. Bootstrap уже закончил fetch с oldest..today-1 (старого `today`), записи uppersted. Scheduler в 04:00 перепишет today (новый) в соответствующую row — это upsert, no-op если данные те же. Приемлемо.

**Конкуренция на activity_details:** если activity из 14:00 ещё не дошёл до `actor_fetch_activity_details` от scheduler'а, а bulk-activities bootstrap'а уже триггерит его — обе задачи могут параллельно работать на одном `activity_id`. `ActivityDetail.save` делает ON CONFLICT UPSERT (проверено) → consistent. Дубль-HTTP — неприятно но не ломается.

---

## 9. UX

### 9.1. Immediate feedback (OAuth callback)

Telegram:

```
🔄 Intervals.icu подключён. Загружаю историю за последний год — обычно 1-3 минуты.
Пришлю уведомление когда закончу.
```

Webapp: `GET /api/auth/me` возвращает `intervals.athlete_id` → webapp показывает Today/Activities/Plan без onboarding-stub'а. Данные за сегодня уже синхронизированы (fast path §6.1).

### 9.2. Progress poll endpoint (Phase 1)

```
GET /api/auth/backfill-status
Response: {
  "status": "running",  // running | completed | failed | none
  "completed_days": 142,
  "failed_days": 0,
  "total_days": 365,
  "progress_pct": 38.9,
  "started_at": "2026-04-20T10:00:00Z",
  "eta_seconds": 90,    // calculated from rate (bulk finishes fast)
  "last_error": null
}
```

Webapp `/settings` показывает progress bar пока `status='running'`. Poll interval 5s (бэкфилл быстрый — частый polling оправдан). Hide bar при `status='completed'`.

### 9.3. Completion notification

См. §6.3. Telegram-сообщение с coverage stats.

### 9.4. Retry / Re-import button (Phase 2)

Kнопка в `/settings` — одна и та же сущность, но её **видимость и текст** определяются state machine ниже (§9.5). Endpoint:

```
POST /api/auth/retry-backfill
Body: (none)
Response 200: {"status": "running", "started_at": "..."}
Response 409: {"error": "already_running"}
Response 429: {"error": "cooldown", "retry_after_seconds": 3456}
```

Вызывает `actor_bootstrap_sync(user_id)`. Idempotency §7 решает — пропустить или overwrite.

**Защита endpoint'а:**
- `require_athlete` (не demo).
- In-process rate limit **1 вызов / час на user_id** (тот же паттерн что `_MCP_CONFIG_RATE_WINDOW_SEC` в `api/routers/auth.py:28`). Защищает от retry-bomb.
- 429 с `Retry-After` header если превышен. Отдельно от §7 cooldown'а — этот limit на endpoint (anti-spam), §7 cooldown на бизнес-логику (не гонять bootstrap дважды).

### 9.5. Button state machine

Webapp читает `/api/auth/backfill-status` + знает `finished_at` → рендерит кнопку по таблице:

| `status` | `completed_days` | `last_error` | Время с `finished_at` | Кнопка | Почему |
|---|---|---|---|---|---|
| `none` (row нет) | — | — | — | **«Загрузить историю»** (primary) | Никогда не запускали. |
| `running` | любой | — | — | Скрыта, вместо неё progress bar | Идёт бэкфилл. |
| `completed` | >0 | — | <7d | **Скрыта**, показываем «✅ История загружена (N дней)» | Bootstrap сработал; webhooks догонят остальное. |
| `completed` | >0 | — | ≥7d | **«Пересинхронизировать»** (secondary) | Допускаем обновление истории вручную раз в неделю. |
| `completed` | 0 | `EMPTY_INTERVALS` | <1h | Disabled, подпись «Intervals ещё не подтянул Garmin. Подожди час и попробуй снова (доступно через `N` мин)» | Empty-import case (§7). |
| `completed` | 0 | `EMPTY_INTERVALS` | ≥1h | **«Повторить импорт»** (primary) | Короткий cooldown для empty-case — юзер ждёт пока Intervals догонит Garmin. |
| `failed` | любой | `<error>` | — | **«Попробовать снова»** (danger variant) + tooltip с `last_error` | Часть данных не доехала. |

**Frontend:** один компонент `<BackfillButton />` читает `status` + `completed_days` + `finished_at` + `last_error` и выбирает вариант по таблице. Сервер **не** вычисляет button text — только возвращает state, UI решает.

**Почему webhooks покрывают «дозалить позже»:** issue #226 отдельно упоминает auto-sync, но в реальности если Intervals после bootstrap'а подтянет старые Garmin данные — каждая новая wellness/activity запись прилетит через `WELLNESS_UPDATED`/`ACTIVITY_UPLOADED` webhook, dispatcher запишет. **Ручной re-import нужен только** когда webhooks пропустили или юзер явно хочет всё перечитать (новый device в Garmin с годами истории — редкий кейс).

---

## 10. Failure recovery

### 10.1. Дневные фейлы (transient)

Dramatiq retry policy: `max_retries=3`, exponential backoff. Intervals API 429 → retry через 60s. Network errors → retry через 5s, потом 30s, потом 150s.

Если после всех retry'ев день всё равно упал — `failed_days++`, фиксируется `last_error`. Остальные дни продолжают sync. В finalize — если `failed_days > 10%` от total → `status='failed'`, иначе `status='completed'` (допускаем shallow gaps).

### 10.2. Worker restart

Все per-day задачи в Redis с `delay=X`. Restart не теряет их — Dramatiq подхватит после restart'а. `completed_days` counter уже был инкременирован до рестарта для успевших задач — consistent state.

### 10.3. Scheduled finalize пропустил дедлайн

Если worker был off-line когда пришло время `actor_bootstrap_finalize`, Dramatiq всё равно запустит его как только сможет. `state.status='running'` до этого момента, UI покажет "stuck at 95%" — приемлемо, финализация догонит.

### 10.4. Ручной rerun

CLI: `python -m cli bootstrap-sync <user_id> [--period 365] [--force]`. С `--force` игнорирует idempotency guard, overwrite state. Полезно когда user попросит пересинхронизировать явно.

---

## 11. Rate limiting

Intervals.icu API ограничение: ~100 req/min per user (документация размыта, эмпирически подтверждено).

**Bulk bootstrap:**
- 2 range-запроса (`/wellness?oldest&newest` + `/activities?oldest&newest`) — суммарно ~2 req. Rate limit не проблема.
- Per-activity details fetch (`actor_fetch_activity_details`) — ~50-150 activities за год для типичного атлета, каждая = 1 request на streams/intervals. Это 50-150 req за ~2-3 минуты → 30-50 req/min пиково, в половине лимита. Если нужен throttle — Dramatiq rate_limiter middleware на per-user semaphore с лимитом 50 req/min.
- Downstream analysis (HRV baseline, recovery) — локальные DB-вычисления, не HTTP.

**Итог:** peak загрузка ~50 req/min при bulk-подходе (дороже чем прежние 6 req/min в per-day плане, но всё ещё в 2x запасе от rate limit). И в 30x быстрее по wall-clock.

---

## 12. Migration

Одна Alembic миграция `N_add_user_backfill_state.py`:

```python
def upgrade():
    op.create_table(
        'user_backfill_state',
        sa.Column('user_id', sa.Integer, sa.ForeignKey('users.id', ondelete='CASCADE'), primary_key=True),
        sa.Column('started_at', sa.TIMESTAMP(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column('finished_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('status', sa.String(16), nullable=False, server_default='running'),
        sa.Column('period_days', sa.Integer, nullable=False),
        sa.Column('total_days', sa.Integer, nullable=False),
        sa.Column('completed_days', sa.Integer, nullable=False, server_default='0'),
        sa.Column('failed_days', sa.Integer, nullable=False, server_default='0'),
        sa.Column('last_error', sa.Text, nullable=True),
        sa.Column('last_synced_dt', sa.Date, nullable=True),
    )

def downgrade():
    op.drop_table('user_backfill_state')
```

Никаких index'ов — PK-lookup единственный pattern. При scale до сотен юзеров — пересмотреть.

---

## 13. Multi-tenant / security

- `user_backfill_state.user_id` FK + CASCADE — tenant-scoped.
- `actor_bootstrap_sync(user_id)` — сервисный actor, user_id принимается параметром (вызывается только из OAuth callback или CLI — не из MCP tools). Не нарушает §T1 (данные не отдаются через MCP, фон).
- Endpoint `GET /api/auth/backfill-status` — через существующий `require_viewer`; читает row по `current_user.id`, не по параметру → 100% tenant isolation.
- Intervals API calls внутри per-day actor'ов — уже per-user через `IntervalsAsyncClient.for_user(user_id)` (credentials из `users.intervals_access_token_encrypted`).

---

## 14. Testing

### Unit

- `tests/tasks/test_bootstrap_sync.py`:
  - `actor_bootstrap_sync` с `period_days=3` → создаёт state row + dispatch'ит ровно 3 задачи (wellness_range + activities_range + finalize).
  - Idempotency: second call при `status='running'` → no-op, задачи не создаются.
  - Idempotency: second call при `status='completed'` <7d ago → no-op.
  - Idempotency: second call при `status='completed'` >7d ago → overwrite state, new dispatches.
  - `actor_bootstrap_wellness_range` с mock Intervals client: 3 rows в ответе → 3 upsert'а + 3 hit'а в Redis set.
  - OAuth revoke (`intervals_auth_method='none'`) в начале bulk actor'а → state помечается failed, ничего не fetching.
- `tests/tasks/test_bootstrap_finalize.py`:
  - `attempt=1`, state не drained → перепланирует `attempt=2` с delay=60s.
  - `attempt=1`, state drained (completed + failed >= total) → выполняет financial step (recalculate_training_log + notify).
  - `attempt=15` (max), state не drained → force-close с `status='failed'`.
  - `state.status != 'running'` → early return без side-effects.
- `tests/data/test_user_backfill_state.py`:
  - `increment_completed` — атомарный SQL UPDATE (assert через integration test с реальной DB, mock SQLAlchemy compiled query).
  - `increment_failed` — bumps counter + truncates error до 500 chars.
  - Guard: updates only rows with `status='running'` (completed state не перезаписывается).

### Integration

- `tests/api/test_oauth_bootstrap.py`:
  - OAuth callback с `was_new=True` → `actor_bootstrap_sync` задиспатчен, `UserBackfillState` row создан со status='running'.
  - OAuth callback с `was_new=False` → bootstrap NOT dispatched.
- `tests/api/test_backfill_status.py`:
  - `GET /api/auth/backfill-status` возвращает актуальный progress.
  - 401 для не-auth'нутого юзера.
  - Скрыт state чужого юзера (tenant isolation).

### E2E (manual, на стаже)

1. Disconnect existing OAuth в `/settings`.
2. Reconnect → проверить что через 10 секунд webapp показывает Today-данные.
3. `/api/auth/backfill-status` → `status='running'`, `completed_days` растёт.
4. Через 2 часа — Telegram-сообщение о завершении, `status='completed'`.
5. Повторный OAuth в течение 7 дней → не триггерит новый бэкфилл.

---

## 15. Acceptance criteria

### Phase 1 (MVP)

- [ ] Alembic миграция `user_backfill_state` применена.
- [ ] `data/db/user_backfill_state.py` — ORM модель + helpers (`get`, `upsert`, `update`, **атомарные** `increment_completed` / `increment_failed`).
- [ ] `actor_bootstrap_wellness_range` + `actor_bootstrap_activities_range` — bulk range fetchers с chunked save, Redis-set counter bookkeeping, deauth guard.
- [ ] `IntervalsSyncClient.get_wellness_range(oldest, newest)` и `.get_activities_range(oldest, newest)` — если ещё нет, добавить.
- [ ] Actor `actor_bootstrap_sync` — state init + dispatch двух bulk actor'ов + kickoff finalize.
- [ ] Actor `actor_bootstrap_finalize` — self-rescheduling до drain (≤15 попыток × 60s) → training_log recompute + Telegram notify.
- [ ] `_actor_send_bootstrap_start_notification` + `_actor_send_bootstrap_completion_notification` — dedicated Telegram senders (через `TelegramTool`, не generic).
- [ ] `api/routers/intervals/oauth.py` — расширен fast-path (добавить `actor_sync_athlete_goals` + `actor_user_scheduled_workouts`) + kick off `actor_bootstrap_sync`.
- [ ] `GET /api/auth/backfill-status` endpoint + DTO.
- [ ] CLI `python -m cli bootstrap-sync <user_id> [--period 365] [--force]` для ручного rerun'а.
- [ ] Telegram start/completion сообщения (i18n в `locale/`). **Разные тексты** для `completed` / `completed+EMPTY_INTERVALS` / `failed`.
- [ ] `POST /api/auth/retry-backfill` endpoint с dual cooldown: 7d при completed+data, 1h при EMPTY_INTERVALS, immediate при failed. 1h anti-spam rate limit поверх.
- [ ] Unit + integration тесты (§14) + тест на EMPTY_INTERVALS: bulk actors возвращают 0 rows → finalize ставит `status=completed, last_error=EMPTY_INTERVALS`; retry через час проходит.
- [ ] CLAUDE.md обновлён: секция «Onboarding» — описать новый автоматический флоу.
- [ ] **Retention note**: `user_backfill_state` row не удаляется after completion, остаётся 1 row/user навсегда. Cleanup policy (e.g. `DELETE WHERE finished_at < now() - 90d`) — отложить до Phase 2 если станет заметно.

### Phase 2

- [ ] Webapp progress bar в `/settings` (React Context + poll).
- [ ] Retry button при `status='failed'`.

---

## 16. Implementation order

1. **Migration + ORM** — `user_backfill_state` table + `data/db/user_backfill_state.py` с атомарными `increment_*` методами (SQL `UPDATE ... SET x = x + 1`, не ORM read-modify-write).
2. **IntervalsSyncClient range getters** — `get_wellness_range(oldest, newest)` и `get_activities_range(oldest, newest)` если ещё не существуют (проверить на соответствие REST API).
3. **Bulk actors** — `actor_bootstrap_wellness_range` + `actor_bootstrap_activities_range` с Redis-set book-keeping и deauth guard.
4. **`actor_bootstrap_sync`** — планировщик (state init → dispatch bulk → kickoff finalize).
5. **`actor_bootstrap_finalize`** — self-rescheduling drain check до 15 попыток.
6. **Telegram senders** — `_actor_send_bootstrap_start_notification` / `_actor_send_bootstrap_completion_notification` (новый файл `tasks/actors/bootstrap.py`).
7. **OAuth callback update** (`oauth.py`) — fast path (+ `sync_athlete_goals` + `scheduled_workouts`) + kick off + Telegram start.
8. **`GET /api/auth/backfill-status`** endpoint + DTO.
9. **CLI `bootstrap-sync`** для manual rerun.
10. **Tests** (§14).
11. **Phase 2** — webapp progress bar + retry endpoint с rate limit (отдельный PR).

---

## 17. Open questions

- **Retry failed days.** Если `failed_days=3` из 365 — стоит ли автоматически re-try'ить их перед finalize? **Предлагаю:** нет, допускаем shallow gaps. Если >10% failed → overall `status='failed'`, юзер может re-run через CLI/Phase 2 кнопку.
- **Backfill webhook-only полей.** Weather/MMP/achievements приходят только через `ACTIVITY_UPLOADED` webhook (см. `WEBHOOK_DATA_CAPTURE_SPEC.md` §6). Для исторических activities полученных через этот bootstrap — эти поля будут null. **Решение:** ожидаемо; `backfill-webhook-data` actor из WEBHOOK_DATA_CAPTURE §6.2 — отдельный разовый прогон после bootstrap'а.
- **Deauth mid-backfill.** Per-bulk-actor deauth-guard (§6.4) закрывает основную проблему. Отдельно: если `APP_SCOPE_CHANGED` webhook приходит пока finalize ретраится — он увидит `intervals_auth_method='none'` в refresh'е User, но сам по себе finalize на auth не завязан. Drain произойдёт как обычно, status будет зависеть от counters. **Action item:** `APP_SCOPE_CHANGED` dispatcher должен вызвать `UserBackfillState.mark_failed(user_id)` если есть active state — отменяет дальнейшие retries finalize'а.
- **Training log — полный re-compute в финале, а не по ходу.** 365 инкрементальных recompute'ов избыточны (каждый считает rolling windows). Одноразовый в finalize — правильный выбор.
- **Period по умолчанию: всё ещё 365?** С bulk-подходом нет причин урезать (1-3 мин всё равно быстро). Оставляем 365.
- **Rate-limit throttle на per-activity details.** §11 упоминает 50-150 req/min peak при bulk активностях. Если Intervals начнёт 429'ить — добавить semaphore/rate-limiter через Dramatiq middleware с limit 50 req/min per user. Не нужно в MVP, observability покажет.
- **Late-arriving Garmin данные.** Кейс: юзер зарегался в Intervals утром, сразу подключил наш бот (bootstrap получил 0 rows), через час Intervals ingested 2 года Garmin-истории. Как наверстать? **Решение:**
  - (a) `WELLNESS_UPDATED` / `ACTIVITY_UPLOADED` webhooks — Intervals шлёт их за каждую новую запись даже при массовом backfill'е. Наши dispatcher'ы запишут всё автоматически. В 90% случаев этого достаточно.
  - (b) Empty-import cooldown 1h (§7) — юзер может вручную тыкнуть «Повторить импорт» через час, подхватит что webhooks могли пропустить.
  - (c) Completed-re-sync cooldown 7d (§7, §9.5) — для edge case когда через месяц юзер купил новый Garmin с многолетней историей и хочет перечитать всё.
  - Итого: автомат через webhooks + два cooldown-варианта ручного trigger'а покрывают все observed cases.
- **Retention policy на `user_backfill_state`.** Сейчас row живёт вечно (1 per user). На 100+ юзерах незаметно; на 10k+ — миграция на `DELETE WHERE finished_at < now() - 90d AND status='completed'` + cron. В MVP skip.
