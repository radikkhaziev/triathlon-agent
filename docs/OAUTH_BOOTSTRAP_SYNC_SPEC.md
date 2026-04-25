# OAuth Bootstrap Sync Spec

> После успешного Intervals.icu OAuth автоматически бэкфилим историю
> (wellness, activities, training load) за год назад. Работает в фоне
> через один chunk-recursive Dramatiq-actor, который идёт по истории
> хронологически (oldest → newest) шагами по 30 дней и вызывает сам себя
> до конца периода. Persistent state — для UX-прогресса и resume'а.
>
> Закрывает [issue #226](https://github.com/radikkhaziev/triathlon-agent/issues/226).

**Related:**

| Issue / Spec / code | Связь |
|---|---|
| [#226](https://github.com/radikkhaziev/triathlon-agent/issues/226) | Основной трекер |
| `api/routers/intervals/oauth.py:200-208` | Текущий minimal auto-sync — расширяем |
| `cli.py` (`sync-wellness`, `sync-activities`) | Референс per-day dispatch (отходим от него) |
| `tasks/actors/wellness.py`, `activities.py`, `training_log.py` | Per-day actors, **остаются для daily cron** |
| `tasks/actors/athlete.py:actor_sync_athlete_settings` | Разовый sync — переиспользуем в fast-path |
| `tasks/actors/workouts.py:actor_user_scheduled_workouts` | Планы на 14 дней вперёд |
| `bot/scheduler.py:70-73` | Daily sync cron — проверить что не конфликтует |
| `docs/MULTI_TENANT_SECURITY.md` T2 | Per-user credentials, OAuth-scoped |
| `docs/WEBHOOK_DATA_CAPTURE_SPEC.md` | Новые поля (weather/MMP/trimp) бэкфилим в той же pipeline |

---

## 1. Мотивация

Сейчас (`api/routers/intervals/oauth.py:200-208`): при первом OAuth-подключении dispatcher отправляет **две** задачи — `actor_sync_athlete_settings` и `actor_user_wellness(today)`. Activities, исторические wellness, training_log — **не загружаются**. Результат: атлет заходит в webapp после OAuth → `/wellness` и `/activities` пустые → непонимание «а бот вообще работает?».

Issue #226 требует загрузить 30/90/14 дней (wellness/activities/calendar). Но для ML-фичей из `ML_HRV_PREDICTION_SPEC` и `ML_RACE_PROJECTION_SPEC` нужна **длинная история** (180+ дней). Делаем **годовой бэкфилл** по умолчанию, с возможностью ограничить.

Intervals.icu API принимает **range-параметры** (`oldest`/`newest`) на `/wellness{ext}` и `/activities`, т.е. годовая история достаётся ~26 range-запросами (13 шагов × 2 endpoint'а) вместо 730 per-day вызовов. Реальное время бэкфилла — **3-5 минут wall-clock** (fetch + chunked persist + inline downstream analysis).

---

## 2. Scope

### Phase 1 (MVP)

- Новая таблица `user_backfill_state` для tracking и resume через cursor.
- Один **chunk-recursive actor** `actor_bootstrap_step(user, cursor_dt)` — за один вызов обрабатывает 30-дневный chunk, продвигает cursor, ре-диспатчит сам себя до конца периода (§4, §6.2).
- Последний chunk финализирует inline: training_log recompute + Telegram notify.
- Обновление `oauth.py:callback` — fast-path (сегодня, settings, **goals**, planned workouts) + kick off первого step'а.
- **Синхронный первый день** (today wellness + settings + today activities + race goals) — быстрый UX: webapp показывает non-empty state в течение 30 секунд.
- Telegram-notification: start + completion (с coverage stats).
- Idempotency: повторный OAuth не триггерит новый бэкфилл если `status='completed'` менее 7 дней назад.
- Progress endpoint `GET /api/auth/backfill-status` для webapp-прогресса.

### Phase 2 — quality-of-life

- Progress bar в `/settings` (poll `/api/auth/backfill-status` каждые 5s пока `status='running'`).
- `<BackfillButton />` компонент с state machine (§9.5) — обслуживает все варианты: первый импорт, retry после failed, re-import после empty-case, пересинк >7d после completed.
- `POST /api/auth/retry-backfill` endpoint с 1h rate limit (§9.4).
- Configurable period (по умолчанию 365, можно ужать до 180/90 через query param).
- `watchdog_bootstrap` cron — rescue stuck state после Dramatiq's `max_retries` (§10.3).

### Non-goals

- **Concurrent parallel chunks** — даёт N×-ускорение, но ломает хронологический порядок downstream-анализа (HRV baseline дня N зависит от N-7/N-60) и съедает rate limit. Сейчас не нужно.
- **Bulk save всей истории одним HTTP + отдельный finalize-drain** — рассмотрен, отклонён: для sparse activities counter-bookkeeping требует Redis-set intersection; downstream-анализ в случайном порядке ломает rolling-window baselines; drain-check сложно дебажить.
- **Рекурсивный per-day pipeline (day N → schedule day N+1, 365 итераций)** — избыточен по API calls (730 HTTP вместо 26) и enqueue overhead. Chunk'и по 30 дней — лучший компромисс между bulk-эффективностью и chronological-correctness.
- **Backfill webhook-only полей** (weather/MMP/achievements) — отдельная история в `WEBHOOK_DATA_CAPTURE_SPEC.md` §6. Исторический бэкфилл этих полей — разовый прогон после merge'а этой спеки.

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

Эти CLI остаются для ad-hoc ручного резинка; автоматический bootstrap на них **не опирается** — у него собственный chunk-actor (§6.2).

---

## 4. Архитектура — chunk-recursive self-rescheduling step

```
OAuth callback (was_new=True)
    │
    ├─ Synchronous fast-path (<2s, non-blocking sends):
    │    • actor_sync_athlete_settings.send()
    │    • actor_sync_athlete_goals.send()              ← RACE_A/B/C видны сразу
    │    • actor_user_wellness.send(dt=today)
    │    • actor_fetch_user_activities.send(today, today)
    │    • actor_user_scheduled_workouts.send()         ← 14 дней вперёд
    │    • _actor_send_bootstrap_start_notification.send()
    │
    └─ actor_bootstrap_step.send(user, cursor_dt=oldest, period_days=365)
         │
         ▼
    actor_bootstrap_step  ← один актор, всё в нём
         │
         ├─ (First call) UserBackfillState.upsert(status='running', cursor_dt=oldest, ...)
         │  (Subsequent) read state, guard status=='running', refresh deauth-state
         │
         ├─ chunk_end = min(cursor_dt + CHUNK_DAYS - 1, newest_dt)
         │
         ├─ 1 HTTP: client.get_wellness_range(cursor_dt, chunk_end)
         ├─ 1 HTTP: client.get_activities(cursor_dt, chunk_end)
         │
         ├─ Chronological loop for dt in [cursor_dt .. chunk_end]:
         │    • Wellness.upsert(dt, ...) если row есть
         │    • _compute_wellness_analysis_sync(dt)    ← inline: HRV/RHR/recovery
         │    • for each activity on dt:
         │         - Activity.upsert(...)
         │         - actor_fetch_activity_details.send(activity_id)  ← async, fire-and-forget
         │
         ├─ UserBackfillState.advance_cursor(chunk_end + 1, last_step_at=now())
         │
         ├─ if chunk_end < newest_dt:
         │     actor_bootstrap_step.send(user, cursor_dt=chunk_end + 1, period_days)
         │     return
         │
         └─ else (последний chunk):
               _finalize_bootstrap(user, state):
                 • actor_recalculate_training_log.send(user)
                 • определяем EMPTY_INTERVALS (wellness_count + activity_count == 0)
                 • state.status = 'completed' (с EMPTY_INTERVALS sentinel если нужно)
                 • _actor_send_bootstrap_completion_notification.send()
```

**Почему chunk-recursion, а не bulk+finalize-drain:**

- **Хронологическая корректность downstream.** HRV baseline — rolling window на 7/60 дней назад. При bulk + 365 concurrent `_actor_compute_wellness_analysis` baseline'ы считаются в случайном порядке → race на окно. Chronological loop внутри chunk'а гарантирует правильный порядок.
- **Counter тривиальный.** `progress_pct = (cursor_dt - oldest_dt) / period_days`. Никаких Redis-множеств, никакого AND-intersection по двум sparse-источникам (activities существуют не каждый день — rest days).
- **Resume бесплатный.** На рестарте worker'а Dramatiq at-least-once пере-доставляет in-flight step. Если сам chain оборвался (exception в `send(next_step)`) — watchdog (Phase 2) видит `status='running' AND last_step_at > 15 min ago` и перезапускает с `state.cursor_dt`.
- **Observability встроена.** Каждый step логирует `cursor_dt → chunk_end`, легко видно в логах в каком куске мы находимся.
- **Одна задача в очереди.** 10 параллельных onboarding'ов = 10 задач, worker справляется. Не 10 × 365 = 3650 как было бы у per-day fan-out'а.
- **Idempotency встроена.** Retry всего step'а = no-op (upserts + `actor_fetch_activity_details` идемпотентны). Dramatiq `max_retries=3` с exp-backoff покрывает transient failures без кастомной логики.

**`CHUNK_DAYS = 30` — эмпирический выбор:**
- Step ~30-45 сек (2 range-fetch'а + ~5-15 activity-details dispatch'ей + HRV/recovery inline по 30 датам).
- ~13 итераций на год → 3-5 мин wall-clock.
- Огромный запас до Dramatiq's `time_limit=30min`.
- API volume: 13 × 2 = 26 range-запросов + 50-150 per-activity details через естественный throttle (§11).

**Scheduler (today) работает параллельно.** `actor_user_wellness(today)` от cron'а продолжает крутиться каждые 10 мин; bootstrap трогает `[oldest .. today-1]`. Пересечений нет **by construction** (см. §8).

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
    oldest_dt       DATE NOT NULL,                           -- самый ранний день периода
    newest_dt       DATE NOT NULL,                           -- today - 1 на момент старта
    cursor_dt       DATE NOT NULL,                           -- следующий необработанный день
    chunks_done     INTEGER NOT NULL DEFAULT 0,              -- для диагностики / watchdog
    last_step_at    TIMESTAMPTZ,                             -- watchdog trigger (Phase 2)
    last_error      TEXT,
    hey_message     TIMESTAMPTZ                              -- post-onboarding nudge (issue #258), NULL = не отправляли
);
```

**`hey_message`** — таймстамп отправки post-onboarding нудж-сообщения «эй, можешь со мной чатиться». NULL = не отправляли. Заполняется атомарно через `mark_hey_sent` (UPDATE … RETURNING) — два параллельных actor'а никогда не отправят дубликат. На `start()` `--force` retry поле сбрасывается в NULL вместе со всеми остальными — комбинация с фильтром `status='completed'` в cron'е гарантирует, что повторный нудж уйдёт ТОЛЬКО когда новый бутстрап реально завершится. См. §10.

**Почему PRIMARY KEY на user_id:** у юзера в любой момент ровно **один** активный backfill. Повторный OAuth либо upsert'ит ту же row (если `status != 'running'`), либо skip'ит (§7).

### 5.2. Cursor semantics — нет races, нет Redis

Каждый step'а цикл:
1. Читает `cursor_dt` (следующий необработанный день).
2. Обрабатывает `[cursor_dt .. min(cursor_dt + CHUNK_DAYS - 1, newest_dt)]`.
3. Одним UPDATE пишет `cursor_dt = chunk_end + 1`, `last_step_at = now()`, `chunks_done = chunks_done + 1`.
4. Если `chunk_end >= newest_dt` → финализация inline, `status = 'completed'`.

Один actor пишет cursor, следующий читает. Нет lost-update race'а. Нет Redis-множеств с TTL. Нет intersection-bookkeeping.

```python
# data/db/user_backfill_state.py
@dual
def advance_cursor(cls, user_id: int, cursor_dt: date, *, session: Session) -> None:
    session.execute(
        update(cls)
        .where(cls.user_id == user_id, cls.status == 'running')
        .values(
            cursor_dt=cursor_dt,
            chunks_done=cls.chunks_done + 1,
            last_step_at=func.now(),
        )
    )
    session.commit()

@dual
def mark_finished(cls, user_id: int, status: str, last_error: str | None = None,
                  *, session: Session) -> None:
    session.execute(
        update(cls)
        .where(cls.user_id == user_id, cls.status == 'running')
        .values(status=status, finished_at=func.now(), last_error=last_error)
    )
    session.commit()

@dual
def mark_failed(cls, user_id: int, error: str, *, session: Session) -> None:
    session.execute(
        update(cls)
        .where(cls.user_id == user_id, cls.status == 'running')
        .values(
            status='failed',
            finished_at=func.now(),
            last_error=(error or '')[:500],
        )
    )
    session.commit()
```

---

## 6. Pipeline

### 6.1. OAuth callback — fast-path + kick off

```python
# api/routers/intervals/oauth.py — замена строк 200-208
if was_new:
    try:
        # Fast path: today + settings + goals + workouts (non-blocking sends, ≤2s).
        actor_sync_athlete_settings.send(user=user_dto)
        actor_sync_athlete_goals.send(user=user_dto)
        actor_user_wellness.send(user=user_dto, dt=date.today().isoformat())
        actor_fetch_user_activities.send(
            user=user_dto,
            oldest=date.today().isoformat(),
            newest=date.today().isoformat(),
        )
        actor_user_scheduled_workouts.send(user=user_dto)

        # Kick off chunk-recursive backfill.
        period_days = 365
        oldest = (date.today() - timedelta(days=period_days)).isoformat()
        actor_bootstrap_step.send(
            user=user_dto,
            cursor_dt=oldest,
            period_days=period_days,
        )

        # Telegram "start" — dedicated actor через TelegramTool.
        _actor_send_bootstrap_start_notification.send(user=user_dto)
    except Exception:
        logger.exception("Failed to dispatch bootstrap for user_id=%d", user_id)
        # OAuth всё равно считается успешным — без бэкфилла.
```

`_actor_send_bootstrap_start_notification` и `_actor_send_bootstrap_completion_notification` — dedicated helpers в `tasks/actors/bootstrap.py`, используют `TelegramTool(user=dto).send_message(text=...)` (sync в dramatiq actor'е). Не через несуществующий generic `actor_send_telegram_message`.

### 6.2. `actor_bootstrap_step` — chunk pipeline + self-reschedule

```python
CHUNK_DAYS = 30

@dramatiq.actor(max_retries=3, time_limit=300_000)  # 5 min per chunk
def actor_bootstrap_step(user: UserDTO, cursor_dt: str, period_days: int = 365) -> None:
    cursor = date.fromisoformat(cursor_dt)

    with get_sync_session() as session:
        state = UserBackfillState.get(user.id, session=session)

        # First invocation — create state row
        if state is None:
            newest = date.today() - timedelta(days=1)
            oldest = cursor
            state = UserBackfillState.upsert(
                user_id=user.id,
                status='running',
                period_days=period_days,
                oldest_dt=oldest,
                newest_dt=newest,
                cursor_dt=cursor,
                chunks_done=0,
                started_at=func.now(),
                finished_at=None,
                last_error=None,
                session=session,
            )
        elif state.status != 'running':
            # Idempotency: call on completed/failed state — early return.
            logger.info("bootstrap_step: state=%s for user=%d, skip", state.status, user.id)
            return

        # Deauth guard — OAuth могли revoke'нуть mid-backfill.
        db_user = User.get_by_id(user.id, session=session)
        if db_user.intervals_auth_method == 'none':
            logger.info("bootstrap_step: OAuth revoked for user=%d, aborting", user.id)
            UserBackfillState.mark_failed(user.id, error='OAuth revoked during backfill')
            return

        newest_dt = state.newest_dt

    chunk_end = min(cursor + timedelta(days=CHUNK_DAYS - 1), newest_dt)

    # Fetch range (2 HTTP calls)
    with IntervalsSyncClient.for_user(user) as client:
        wellness_rows = client.get_wellness_range(oldest=cursor, newest=chunk_end)
        activity_rows = client.get_activities(oldest=cursor, newest=chunk_end)

    wellness_by_date = {w.date: w for w in wellness_rows}
    activities_by_date: dict[date, list] = defaultdict(list)
    for a in activity_rows:
        activities_by_date[a.start_date_local.date()].append(a)

    # Chronological loop — HRV baseline correctness зависит от порядка.
    for offset in range((chunk_end - cursor).days + 1):
        dt = cursor + timedelta(days=offset)

        if w := wellness_by_date.get(dt):
            Wellness.upsert(user_id=user.id, **w.model_dump())
            _compute_wellness_analysis_sync(user.id, dt)  # HRV/RHR/recovery inline

        for a in activities_by_date.get(dt, []):
            Activity.upsert(user_id=user.id, **a.model_dump())
            actor_fetch_activity_details.send(activity_id=a.id)  # async, idempotent

    # Advance cursor atomically
    next_cursor = chunk_end + timedelta(days=1)
    UserBackfillState.advance_cursor(user_id=user.id, cursor_dt=next_cursor)

    # Recurse or finalize
    if chunk_end < newest_dt:
        actor_bootstrap_step.send(
            user=user,
            cursor_dt=next_cursor.isoformat(),
            period_days=period_days,
        )
        return

    _finalize_bootstrap(user, state)


def _finalize_bootstrap(user: UserDTO, state: UserBackfillState) -> None:
    """Последний chunk обработан — финализируем."""
    # Training log — полный recompute в одном прогоне (вместо 365 инкрементальных).
    actor_recalculate_training_log.send(user=user)

    # Empty-import detection — ни wellness, ни activities за весь период.
    with get_sync_session() as session:
        wellness_count = Wellness.count_for_period(
            user_id=user.id, oldest=state.oldest_dt, newest=state.newest_dt, session=session,
        )
        activity_count = Activity.count_for_period(
            user_id=user.id, oldest=state.oldest_dt, newest=state.newest_dt, session=session,
        )

    if wellness_count == 0 and activity_count == 0:
        final_status = 'completed'
        final_error = 'EMPTY_INTERVALS'  # sentinel для UI (§7, §9.5)
    else:
        final_status = 'completed'
        final_error = None

    UserBackfillState.mark_finished(
        user_id=user.id,
        status=final_status,
        last_error=final_error,
    )

    _actor_send_bootstrap_completion_notification.send(
        user=user,
        status=final_status,
        wellness_count=wellness_count,
        activity_count=activity_count,
        period_days=state.period_days,
    )
```

### 6.3. Failure semantics внутри step'а

- `max_retries=3` — Dramatiq автоматически ретраит весь step при exception. Upserts идемпотентны, retry безопасен.
- Падение до commit'а cursor'а → retry перезапустит тот же chunk с того же `cursor_dt`. OK.
- Падение после commit'а cursor'а но до `send(next_step)` → retry перевыполнит chunk (upserts no-op), новый cursor равен текущему, chain восстанавливается. OK.
- После 3-х retry'ев Dramatiq помечает message failed → state остаётся `running`. Watchdog (§10.3, Phase 2) подхватывает `last_step_at > 15 min ago`.

### 6.4. Existing per-day actors не модифицируются

`actor_user_wellness(dt)` / `actor_fetch_user_activities(today, today)` продолжают обслуживать daily cron и fast-path OAuth callback'а. Bootstrap работает через собственные range-fetches внутри `actor_bootstrap_step` и **не** знает о `UserBackfillState` логике per-day actor'ов. `actor_fetch_activity_details` переиспользуется как async dispatch внутри chunk'а (fire-and-forget, идемпотентен).

---

## 7. Idempotency

Четыре сценария повторного OAuth / step'а:

| Сценарий | Cooldown | Поведение |
|---|---|---|
| `was_new=False` (refresh OAuth) | — | Bootstrap не триггерится. Существующий код OAuth это уже делает. |
| `status='running'` | — | `actor_bootstrap_step` первой строкой читает state, видит `running` и идёт дальше по нормальному пути (собственный cursor). Повторный `actor_bootstrap_step.send` из вне (напр. ручной retry) — скипается early return'ом. |
| `status='completed'`, `last_error != 'EMPTY_INTERVALS'`, `finished_at < 7d ago` | 7 дней | Skip (webhooks обслуживают incremental updates). |
| `status='completed'`, `last_error == 'EMPTY_INTERVALS'`, `finished_at < 1h ago` | **1 час** | Intervals был пуст (юзер только что зарегался, Garmin ещё не доехал). Короткий cooldown позволяет retry пока Intervals догоняет. |
| `status='failed'` / `status='completed'` >7d ago | — | Разрешаем rerun (upsert state, old row overwritten, cursor сброшен на oldest). |

**Empty-import logic** — в `_finalize_bootstrap` (§6.2). `EMPTY_INTERVALS` → `status='completed'`, но UI (§9.5) показывает «Повторить импорт» с коротким cooldown'ом. Юзер может retry через час когда Intervals догонит Garmin.

Внутри chunk'а идемпотентность встроена: `Wellness.upsert` + `Activity.upsert` (ON CONFLICT) + `actor_fetch_activity_details` (skip if exists). Повторный прогон того же chunk'а — no-op.

---

## 8. Concurrency с daily scheduler

Scheduler (`bot/scheduler.py`):
- `actor_user_wellness(today)` — каждые 10 мин.
- `actor_fetch_user_activities(today)` — каждые 10 мин.
- `actor_recalculate_training_log` — weekly cron.

Bootstrap step'ы работают на `[oldest .. today-1]`, scheduler — на `today`. Пересечения дат нет **by construction** (см. §4: `newest_dt = today - 1` зафиксирован на момент первого step'а).

**Пограничный случай — полночь:** bootstrap стартовал 23:50, `newest_dt` закрепился как «вчера»; к 00:01 наступил новый день. Bootstrap уже прошёл до старого `newest_dt`, scheduler возьмёт новый `today`. Upsert idempotent → no-op при совпадении.

**Конкуренция на activity_details:** если activity из 14:00 ещё не дошёл до `actor_fetch_activity_details` от scheduler'а, а bootstrap'ный chunk уже триггерит его — обе задачи могут параллельно работать на одном `activity_id`. `ActivityDetail.save` делает ON CONFLICT UPSERT (проверено) → consistent. Дубль-HTTP — неприятно, но не ломается.

---

## 9. UX

### 9.1. Immediate feedback (OAuth callback)

Telegram:

```
🔄 Intervals.icu подключён. Загружаю историю за последний год — обычно 3-5 минут.
Пришлю уведомление когда закончу.
```

Webapp: `GET /api/auth/me` возвращает `intervals.athlete_id` → webapp показывает Today/Activities/Plan без onboarding-stub'а. Данные за сегодня уже синхронизированы (fast path §6.1).

### 9.2. Progress poll endpoint (Phase 1)

```
GET /api/auth/backfill-status
Response: {
  "status": "running",  // running | completed | failed | none
  "cursor_dt": "2025-08-14",
  "oldest_dt": "2025-04-21",
  "newest_dt": "2026-04-20",
  "progress_pct": 31.8,
  "chunks_done": 4,
  "started_at": "2026-04-20T10:00:00Z",
  "eta_seconds": 120,   // rolling estimate: avg_chunk_duration × remaining_chunks
  "last_error": null
}
```

Расчёт: `progress_pct = (cursor_dt - oldest_dt).days / (newest_dt - oldest_dt).days * 100`. `eta_seconds = (expected_chunks - chunks_done) × avg_chunk_duration` (если `chunks_done >= 1`).

Webapp `/settings` показывает progress bar пока `status='running'`. Poll interval 5s. Hide bar при `status='completed'`.

### 9.3. Completion notification

См. `_finalize_bootstrap` в §6.2. Telegram-сообщение с coverage stats:

- **Normal:** `✅ История загружена: 342 дня wellness, 78 активностей за 365 дней.`
- **Empty:** `ℹ️ Intervals.icu ещё не подтянул Garmin-историю. Попробую снова через час; можешь также нажать «Повторить импорт» в /settings.`
- **Failed:** `⚠️ Не удалось загрузить часть истории (см. /settings). Нажми «Попробовать снова».`

### 9.4. Retry / Re-import button (Phase 2)

Кнопка в `/settings` — одна сущность, видимость и текст определяются state machine ниже (§9.5). Endpoint:

```
POST /api/auth/retry-backfill
Body: (none)
Response 200: {"status": "running", "started_at": "..."}
Response 409: {"error": "already_running"}
Response 429: {"error": "cooldown", "retry_after_seconds": 3456}
```

Вызывает `actor_bootstrap_step(user, cursor_dt=oldest)` с upsert'ом state (cursor сброшен, chunks_done=0).

**Защита endpoint'а:**
- `require_athlete` (не demo).
- In-process rate limit **1 вызов / час на user_id** (тот же паттерн что `_MCP_CONFIG_RATE_WINDOW_SEC` в `api/routers/auth.py:28`). Защищает от retry-bomb.
- 429 с `Retry-After`. Отдельно от §7 cooldown'а — этот limit на endpoint (anti-spam), §7 cooldown на бизнес-логику.

### 9.5. Button state machine

Webapp читает `/api/auth/backfill-status` + `finished_at` + `last_error` → рендерит кнопку по таблице:

| `status` | `completed / empty` | `last_error` | Время с `finished_at` | Кнопка | Почему |
|---|---|---|---|---|---|
| `none` (row нет) | — | — | — | **«Загрузить историю»** (primary) | Никогда не запускали. |
| `running` | — | — | — | Скрыта, вместо неё progress bar | Идёт бэкфилл. |
| `completed` | есть данные | — | <7d | **Скрыта**, показываем «✅ История загружена» | Bootstrap сработал; webhooks догонят остальное. |
| `completed` | есть данные | — | ≥7d | **«Пересинхронизировать»** (secondary) | Допускаем обновление вручную раз в неделю. |
| `completed` | пусто | `EMPTY_INTERVALS` | <1h | Disabled, подпись «Intervals ещё не подтянул Garmin. Доступно через N мин» | Empty-import case (§7). |
| `completed` | пусто | `EMPTY_INTERVALS` | ≥1h | **«Повторить импорт»** (primary) | Короткий cooldown — юзер ждёт пока Intervals догонит Garmin. |
| `failed` | — | `<error>` | — | **«Попробовать снова»** (danger variant) + tooltip с `last_error` | Chain оборвался / exceeded retries. |

**Frontend:** один компонент `<BackfillButton />` читает state и выбирает вариант по таблице. Сервер **не** вычисляет button text — только возвращает state, UI решает.

**Почему webhooks покрывают «дозалить позже»:** если Intervals после bootstrap'а подтянет старые Garmin данные — каждая новая wellness/activity запись прилетит через `WELLNESS_UPDATED`/`ACTIVITY_UPLOADED` webhook, dispatcher запишет. **Ручной re-import нужен только** когда webhooks пропустили или юзер явно хочет всё перечитать (новый device в Garmin с годами истории — редкий кейс).

### 9.6. Post-onboarding nudge (issue #258)

Через 24-48ч после `finished_at` cron-job отправляет атлету одно дружелюбное Telegram-сообщение «эй, ты можешь со мной чатиться» — для тех, кто прошёл OAuth, но молчит. Решает проблему «онбординг закрылся, юзер не понял что делать дальше».

**Триггер:** `scheduler_onboarding_hey_job` в `bot/scheduler.py` — cron ежечасно `09:00–21:00` в `settings.TIMEZONE` (не будим ночью). SQL живёт целиком на `user_backfill_state`:

```sql
SELECT user_id FROM user_backfill_state
WHERE status = 'completed'
  AND hey_message IS NULL
  AND finished_at BETWEEN now() - interval '48 hours' AND now() - interval '24 hours'
```

Никаких JOIN'ов на `users` или `api_usage_daily` — сообщение friendly, ловить «уже чатился» не обязательно.

**Идемпотентность через mark-first:** `actor_send_onboarding_hey` сначала вызывает `UserBackfillState.mark_hey_sent(user_id)`, который делает атомарный `UPDATE … RETURNING user_id`. Если другой инстанс уже отметил — возвращает `False`, и actor молча выходит. Только победитель race'а отправляет Telegram. Цена: при сбое send'а после успешного UPDATE юзер теряет один nudge — приемлемо, т.к. сообщение one-shot UX.

**Поведение `start()` при `--force` retry:** `hey_message` сбрасывается в NULL вместе со всеми остальными полями. Это значит, что юзер, который ре-инициировал бутстрап (например, после `EMPTY_INTERVALS`), снова получит nudge через 24-48ч **после нового `finished_at`**. Дубль не возникает, потому что фильтр cron'а `status='completed'` блокирует выборку до тех пор, пока новый bootstrap не завершится.

**Текст** живёт в `tasks/formatter.py:build_onboarding_hey_message`, локализован через `_()`. RU/EN дают athlete'у краткое введение в ментальную модель чата (каждое сообщение — новый диалог, Reply продолжает разговор, важные факты бот запоминает) — это нетривиально и влияет на то, как юзер сформулирует первое сообщение.

---

## 10. Failure recovery

### 10.1. Transient failures внутри chunk'а

Dramatiq retry policy: `max_retries=3`, exponential backoff. Intervals API 429 → retry через 60s. Network errors → retry через 5s, потом 30s, потом 150s. Retry перезапускает весь step; upserts + fetch_details idempotent → безопасно.

### 10.2. Worker restart

In-flight step: Dramatiq at-least-once пере-доставит message после restart'а — step выполнится ещё раз, cursor продвинется как обычно (upserts no-op для уже-обработанной части chunk'а).

Scheduled next step (`actor_bootstrap_step.send(...)` после commit'а cursor'а): message уже в Redis → worker после restart'а подберёт.

### 10.3. Chain обрыв после исчерпания `max_retries`

Если step reached max_retries и Dramatiq пометил message failed — chain обрывается, `state.status='running'` навечно. Решение: **`watchdog_bootstrap` cron** (Phase 2, каждые 10 мин):

```python
# bot/scheduler.py (Phase 2)
@scheduler.scheduled_job('interval', minutes=10)
def watchdog_bootstrap() -> None:
    stuck = UserBackfillState.list_stuck(threshold_min=15)
    for state in stuck:
        logger.warning("bootstrap watchdog: re-kick user=%d cursor=%s", state.user_id, state.cursor_dt)
        actor_bootstrap_step.send(
            user=UserDTO.model_validate(User.get_by_id(state.user_id)),
            cursor_dt=state.cursor_dt.isoformat(),
            period_days=state.period_days,
        )
```

**В MVP watchdog не обязателен** — Dramatiq retry (max_retries=3) покрывает 99% transient-кейсов; stuck state после исчерпания retries — редкий, пользователь может нажать «Попробовать снова» в `/settings` (Phase 2). Если и retry endpoint ещё не готов — CLI `bootstrap-sync --force` (§10.4).

### 10.4. Ручной rerun

CLI: `python -m cli bootstrap-sync <user_id> [--period 365] [--chunk 30] [--force]`. С `--force` игнорирует idempotency guard, overwrite state, сбрасывает cursor на `oldest`. Полезно когда user попросит пересинхронизировать явно.

---

## 11. Rate limiting

Intervals.icu API ограничение: ~100 req/min per user (документация размыта, эмпирически подтверждено).

**Chunk bootstrap на год (365 дней, CHUNK_DAYS=30):**
- 13 chunks × 2 range-запроса = **26 range-requests** на весь бэкфилл. Distributed по 3-5 мин → ~5-10 req/min. Тривиально.
- Per-activity details: 50-150 activities за год. Dispatched async через `actor_fetch_activity_details.send()` — Dramatiq worker обрабатывает их последовательно (естественный throttle ~30-50 req/min, т.к. каждый detail-fetch ~300ms-1s).
- Downstream analysis (HRV baseline, recovery) — inline в step'е, DB-only, не HTTP.

**Итог:** peak ~40-50 req/min (ограниченный в основном `actor_fetch_activity_details` worker-level throttle). В 2x запасе от Intervals rate limit.

Если наблюдается 429 на details — ввести `dramatiq-rate-limit` middleware с per-user semaphore 50 req/min. Не нужно в MVP, observability покажет.

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
        sa.Column('oldest_dt', sa.Date, nullable=False),
        sa.Column('newest_dt', sa.Date, nullable=False),
        sa.Column('cursor_dt', sa.Date, nullable=False),
        sa.Column('chunks_done', sa.Integer, nullable=False, server_default='0'),
        sa.Column('last_step_at', sa.TIMESTAMP(timezone=True), nullable=True),
        sa.Column('last_error', sa.Text, nullable=True),
    )

def downgrade():
    op.drop_table('user_backfill_state')
```

Никаких index'ов — PK-lookup + редкий scan `WHERE status='running' AND last_step_at < ...` для watchdog (пока линейно на 100+ rows это тривиально; при 10k+ — добавить partial index на `status='running'`).

---

## 13. Multi-tenant / security

- `user_backfill_state.user_id` FK + CASCADE — tenant-scoped.
- `actor_bootstrap_step(user_id)` — сервисный actor, user_id принимается параметром (вызывается только из OAuth callback, CLI или watchdog — не из MCP tools). Не нарушает §T1 (данные не отдаются через MCP, фон).
- Endpoint `GET /api/auth/backfill-status` — через существующий `require_viewer`; читает row по `current_user.id`, не по параметру → 100% tenant isolation.
- Intervals API calls внутри chunk step'а — per-user через `IntervalsSyncClient.for_user(user)` (credentials из `users.intervals_access_token_encrypted`).

---

## 14. Testing

### Unit

- `tests/tasks/test_bootstrap_step.py`:
  - First call (no state): создаёт row со `status='running'`, `cursor_dt=oldest`, обрабатывает chunk, продвигает cursor, диспатчит next step.
  - Middle call: читает state, обрабатывает chunk `[cursor .. cursor+29]`, advance_cursor, диспатч next.
  - Last chunk (`chunk_end >= newest_dt`): НЕ диспатчит next, вызывает `_finalize_bootstrap`.
  - `_finalize_bootstrap` — EMPTY_INTERVALS path: wellness_count=0, activity_count=0 → `last_error='EMPTY_INTERVALS'`.
  - `_finalize_bootstrap` — normal path: есть данные → `last_error=None`.
  - Idempotency: call on `status='completed'` → early return (без fetch'а).
  - Idempotency: call on `status='failed'` → early return.
  - Deauth guard: `intervals_auth_method='none'` → `mark_failed`, без fetch'а.
  - Chronological order: wellness_analysis inline вызван по возрастанию дат внутри chunk'а.
- `tests/data/test_user_backfill_state.py`:
  - `advance_cursor` — атомарный SQL UPDATE, бampsit chunks_done, пишет last_step_at.
  - `mark_finished` — only updates rows with `status='running'`.
  - `mark_failed` — truncates error до 500 chars.

### Integration

- `tests/api/test_oauth_bootstrap.py`:
  - OAuth callback with `was_new=True` → `actor_bootstrap_step` задиспатчен с `cursor_dt=oldest`, `UserBackfillState` row создан после первого step'а.
  - OAuth callback with `was_new=False` → bootstrap НЕ dispatched.
- `tests/api/test_backfill_status.py`:
  - Возвращает актуальный progress: `progress_pct`, `cursor_dt`, `chunks_done`.
  - 401 для не-auth'нутого юзера.
  - Tenant isolation (не видит state чужого юзера).

### E2E (manual, на стейдже)

1. Disconnect existing OAuth в `/settings`.
2. Reconnect → через 30 сек в webapp видны today-данные.
3. `/api/auth/backfill-status` → `status='running'`, `cursor_dt` продвигается каждые ~30 сек.
4. Через 3-5 мин — Telegram «История загружена», `status='completed'`.
5. Повторный OAuth в течение 7 дней → не триггерит новый бэкфилл.

---

## 15. Acceptance criteria

### Phase 1 (MVP) — ✅ implemented 2026-04-21

- [x] Alembic миграция `user_backfill_state` (`migrations/versions/a7e4c1b8d9f0_add_user_backfill_state.py`) — применяется на следующем `alembic upgrade head`.
- [x] `data/db/backfill.py` — ORM `UserBackfillState` + атомарные helpers (`get`, `start` с `ON CONFLICT DO UPDATE`, `advance_cursor`, `mark_finished`, `mark_failed`, `list_stuck` для Phase 2 watchdog'а). `progress_pct()` / `is_empty_import()` derived helpers.
- [x] `IntervalsSyncClient.get_wellness_range(oldest, newest)` + async зеркало — новый `_spec_get_wellness_range` в `data/intervals/client.py`, использует bare `/athlete/{id}/wellness?oldest&newest` (тот же паттерн что `/activities`). `get_activities_range` уже был.
- [x] `actor_bootstrap_step` в `tasks/actors/bootstrap.py` — `CHUNK_DAYS=30`, `max_retries=3`, `time_limit=5min`, first-call state init, deauth guard, chronological wellness dispatch + Strava filter + `Activity.save_bulk` (ON CONFLICT) + per-activity `actor_update_activity_details.send()`, атомарный `advance_cursor`, self-reschedule или inline `_finalize_bootstrap`.
- [x] `_finalize_bootstrap` — empty-detect (wellness_count + activity_count == 0 → `EMPTY_INTERVALS` sentinel) + `mark_finished` + delayed Telegram notify. **Training_log recompute не отдельным шагом** — `actor_user_wellness` уже триггерит `actor_after_activity_update` per-day, который заполняет training_log (PRE/ACTUAL/POST) по ходу chunk recursion. Упрощение vs первоначальный план.
- [x] `_actor_send_bootstrap_start_notification` + `_actor_send_bootstrap_completion_notification` — dedicated senders через `TelegramTool(user).send_message()`. **Completion notification откладывается на 60s через `send_with_options(delay=60_000)`** и сам пере-запрашивает счётчики из БД — это закрывает race где последний chunk ещё не додиспатчил свой tail из `actor_user_wellness.send` к моменту finalize (см. §17 «Wellness count race»).
- [x] `api/routers/intervals/oauth.py` — fast-path расширен: `actor_sync_athlete_settings` + `actor_sync_athlete_goals` + `actor_user_wellness(today)` + `actor_fetch_user_activities(today, today)` + `actor_user_scheduled_workouts` + kick off `actor_bootstrap_step(cursor_dt=oldest, period_days=365)` + `_actor_send_bootstrap_start_notification.send()`.
- [x] `GET /api/auth/backfill-status` endpoint + `BackfillStatusResponse` DTO в `api/dto.py`. Tenant-scoped через `get_data_user_id(user)`, `Depends(get_current_user)` — 401 для анонимов. Демо-юзеры видят state владельца (консистентно с `/api/auth/me`).
- [x] CLI `python -m cli bootstrap-sync <user_id> [--period 365] [--force]` — `--force` ресетит state через `UserBackfillState.start()` до dispatch'а actor'а, обходит idempotency guard (§10.4).
- [x] Telegram start/completion сообщения. Три текста: normal «✅ История загружена: N дней wellness, M активностей за P дней», empty-import «ℹ️ Intervals.icu ещё не подтянул Garmin-историю», start «🔄 Intervals.icu подключён. Загружаю историю за последний год — обычно 3-5 минут.». i18n — строки уже обёрнуты в `_()`, перевод на en добавляется через `locale/en/LC_MESSAGES/messages.po` при первой английской сессии.
- [x] Unit-тесты `tests/tasks/test_bootstrap_actors.py` (8 кейсов: first-call init, completed / failed early-return, deauth-guard, middle chunk advance+recurse, chronological wellness + Strava filter + new-id details, last chunk finalize normal, EMPTY_INTERVALS sentinel). Помечены `pytest.mark.real_db` — из-за shared autouse-fixture в `tests/conftest.py` тесты проходят только когда тестовая БД доступна, даже если сами они DB не трогают (мокают всё). Логика актёра отдельно проверена standalone Python smoke'ом — все 8 assertion'ов зелёные.
- [ ] CLAUDE.md обновлён: секция «Onboarding» — описан новый автоматический флоу (см. отдельный PR или следующий коммит).
- [x] **Retention note:** `user_backfill_state` row не удаляется after completion (1 row/user). Cleanup policy отложен до Phase 2 при scale.

**Security review (2026-04-21):** Low risk, 1 информационный item (L1 — `mark_failed` docstring-guard от случайного `str(e)` с HTTP-contexts добавлен в коде). T1/T2/T147 — все tenant-isolation invariants держатся: все ORM-запросы scoped по `user_id`, `IntervalsSyncClient.for_user` читает per-user Fernet-шифрованный токен, UserDTO через dramatiq не содержит credentials.

### Phase 2 — ✅ completed 2026-04-22

- [x] Webapp progress bar в `/settings` — `webapp/src/components/BackfillSection.tsx` объединяет progress bar + button state machine в одном компоненте. Poll каждые 5s пока `status='running'`, отдельный 1-Hz тик для countdown disabled-состояния.
- [x] `<BackfillButton />` state machine (§9.5) — внутри `BackfillSection`. Семь веток: `none` → primary «Загрузить историю»; `running` → progress bar (кнопка скрыта); `completed+data+<7d` → quiet «✅ История загружена»; `completed+data+≥7d` → secondary «Пересинхронизировать»; `completed+EMPTY+<1h` → disabled countdown; `completed+EMPTY+≥1h` → primary «Повторить импорт»; `failed` → danger «Попробовать снова» + inline `last_error` (переведён через `explainLastError` i18n-лукап, не рендер raw string'а).
- [x] `POST /api/auth/retry-backfill` (`api/routers/auth.py:auth_retry_backfill`) — два independent guard'а: business cooldown (`_backfill_retry_retry_after`: 7d при completed+data, 1h при EMPTY_INTERVALS, immediate при failed) + anti-spam in-process 1/hour per user. 401 / 403 (demo) / 400 (OAuth not connected) / 409 (already_running) / 429 (cooldown или rate limit) с `Retry-After` header. На allow: `UserBackfillState.start` ресет + `actor_bootstrap_step.send`. Demo-reject ДО rate-limit lookup'а — у demo `user.id == owner.id`, иначе демо-сессия могла бы разделять budget владельца.
- [x] `watchdog_bootstrap` cron — `bot/scheduler.py:scheduler_watchdog_bootstrap`, каждые 10 мин. Через `UserBackfillState.list_stuck(threshold_min=15)` ищет `running` строки с stale `last_step_at` и ре-диспатчит `actor_bootstrap_step(cursor_dt=state.cursor_dt)`. Cursor CAS внутри актора гарантирует, что chain подхватится ровно с последней зафиксированной позиции. **Escalation:** после `_BOOTSTRAP_MAX_WATCHDOG_KICKS=3` kick'ов без advance'а cursor'а → `mark_failed(error='watchdog_exhausted')` — защита от infinite re-kick broken chain'а. Счётчик живёт в `last_error` как `watchdog_kick_N`; `advance_cursor` чистит `last_error=None` при успешном прогрессе, так что counter reset'ится автоматически.
- [x] **HRV baseline ordering fix** — решено через inline sync pipeline. Новый `process_wellness_analysis_sync(user, wellness)` в `tasks/actors/wellness.py` делает save + RHR + HRV + Banister + recovery синхронно внутри одного вызова; bootstrap вызывает его напрямую в chronological loop вместо `actor_user_wellness.send()`. Фан-аут (training_log enrichment, athlete_settings sync) остался асинхронным — эти шаги per-day idempotent и cross-day ordering не требуют. Sort key — `date.fromisoformat(w.id)`, не lexicographic (защита от будущего change'а ID format'а у Intervals); unparseable ID'ы идут в конец через `date.max`. Per-day exception swallowed + `sentry_sdk.capture_exception()` — чанк продолжается, failure осязаем в Sentry.
- [x] **`last_error` sanitization** — `_sanitize_last_error` allowlist в `api/routers/auth.py`: `EMPTY_INTERVALS`, `watchdog_exhausted`, `OAuth revoked during backfill` проходят; `watchdog_kick_N` → `None` (bookkeeping, не user-facing); всё остальное → `"internal"`. Defensive guard от caller'а, случайно передавшего raw `str(httpx_error)` с URL/токенами в `mark_failed`.

**Post-review adjustments (after code-reviewer + security-review 2026-04-22):**
- 🔴 **Critical fix:** sort key switched from string `w.id` to `date.fromisoformat(w.id)` — lexicographic sort was coupled to current Intervals ID shape (ISO dates), would silently break on format change.
- 🟡 **Watchdog escalation:** `_BOOTSTRAP_MAX_WATCHDOG_KICKS=3` with `watchdog_exhausted` sentinel, reset on `advance_cursor`.
- 🟡 **Single-worker assumption documented** for `_retry_backfill_last_success` + lazy LRU prune at 512-entry watermark.
- 🟡 **`last_error` allowlist** — server-side sanitization before it reaches UI.
- 🔵 **Sentry capture** on swallowed per-day wellness failures.
- 🔵 Demo-reject comment-anchored at rate-limit check.
- 🔵 Dead backwards-compat alias removed (`_actor_send_bootstrap_start_notification` in `tasks/actors/bootstrap.py`).
- 🔵 f-string continuation nit fixed.

**Tests:** `tests/tasks/test_bootstrap_actors.py` (10 cases — first-call, idempotency, deauth, cursor CAS, middle/last chunk, empty-import, **wellness ordering + failure swallow**), `tests/api/test_auth_retry_backfill.py` (17 cases — sanitize allowlist, business cooldown table, auth/authorization precondition ladder, dual-guard happy/edge paths), `tests/bot/test_scheduler_watchdog.py` (11 cases — parse_kick helper + scheduler behaviour under 6 stuck-state permutations). Все помечены `pytest.mark.real_db` — проходят в окружении с тестовой БД; helper-логика отдельно валидирована standalone smoke'ом.

---

## 16. Implementation order

1. **Migration + ORM** — `user_backfill_state` (cursor schema) + атомарные helpers (`advance_cursor` / `mark_finished` / `mark_failed` через SQL `UPDATE`, не ORM read-modify-write).
2. **IntervalsSyncClient range getters** — `get_wellness_range(oldest, newest)` (новый) и `get_activities_range(oldest, newest)` (проверить что уже есть).
3. **`actor_bootstrap_step`** — chunk pipeline с first-call init + chronological loop + self-reschedule. Новый файл `tasks/actors/bootstrap.py`.
4. **`_finalize_bootstrap` + Telegram senders** — в том же `tasks/actors/bootstrap.py`.
5. **OAuth callback update** (`oauth.py`) — fast path (+ `sync_athlete_goals` + `scheduled_workouts`) + kick off + Telegram start.
6. **`GET /api/auth/backfill-status`** endpoint + DTO.
7. **CLI `bootstrap-sync`** для manual rerun.
8. **Tests** (§14).
9. **Phase 2** — webapp progress bar + retry endpoint с rate limit + watchdog cron (отдельный PR).

---

## 17. Open questions

- **CHUNK_DAYS tuning.** 30 — стартовый выбор. Если step превысит `time_limit=5min` или HRV inline окажется медленным — уменьшить до 14 или 7. Observability покажет. Размер configurable через CLI flag.
- **Watchdog в MVP или Phase 2?** ✅ resolved — watchdog shipped в Phase 2. `scheduler_watchdog_bootstrap` каждые 10 мин ре-диспатчит `actor_bootstrap_step` для stuck running state (`last_step_at` старше 15 мин). Cursor CAS в акторе подхватывает chain с последнего зафиксированного cursor.
- **Training log — полный re-compute в финале, а не по ходу.** Один прогон в `_finalize_bootstrap` правильнее: 365 инкрементальных recompute'ов избыточны (каждый считает rolling windows).
- **Period по умолчанию: всё ещё 365?** С chunk-подходом нет причин урезать (3-5 мин приемлемо). Оставляем 365. CLI flag позволяет ужать.
- **Rate-limit throttle на per-activity details.** §11 упоминает 40-50 req/min peak. Если Intervals начнёт 429'ить — добавить `dramatiq-rate-limit` middleware с 50 req/min per user. Не нужно в MVP.
- **Late-arriving Garmin данные.** Покрывается:
  - (a) `WELLNESS_UPDATED` / `ACTIVITY_UPLOADED` webhooks — Intervals шлёт за каждую новую запись даже при массовом backfill'е.
  - (b) Empty-import cooldown 1h (§7) — ручной retry через час.
  - (c) Completed re-sync cooldown 7d (§7, §9.5) — через неделю юзер может перечитать.
- **Retention policy на `user_backfill_state`.** Сейчас 1 row/user навсегда. На 100+ юзерах незаметно; на 10k+ — `DELETE WHERE finished_at < now() - 90d AND status='completed'` + cron. В MVP skip.
- **Backfill webhook-only полей.** Weather/MMP/achievements приходят только через `ACTIVITY_UPLOADED` webhook (см. `WEBHOOK_DATA_CAPTURE_SPEC.md` §6). Для исторических activities через этот bootstrap — эти поля будут null. Отдельный `backfill-webhook-data` actor — разовый прогон после merge'а.
- **Deauth mid-backfill.** Per-step deauth-guard (§6.2) закрывает основную проблему. Отдельно: `APP_SCOPE_CHANGED` webhook dispatcher должен вызвать `UserBackfillState.mark_failed(user_id)` если есть active state — отменяет дальнейшие step retries.
- **HRV baseline ordering race** ✅ resolved 2026-04-22. Фан-аут `actor_user_wellness.send()` заменён на inline `process_wellness_analysis_sync(user, wellness)` в chronological loop. Каждый день save+RHR+HRV+Banister+recovery коммитятся до начала следующего дня — rolling baselines всегда читают полную историю предыдущих дней. Post-activity training_log + athlete_settings sync остались async (cross-day ordering не требуется).
- **Wellness count race в `_finalize_bootstrap`** (исправлено 2026-04-21). `_finalize_bootstrap` изначально читал `wellness_count` из БД inline и передавал в Telegram notification. Но `actor_user_wellness.send()` — fire-and-forget, и к моменту finalize последний chunk's wellness actors ещё в полёте. Текущее решение: completion notification дисп'этчится через `send_with_options(delay=60_000)` и сам пере-читает счётчики. EMPTY_INTERVALS остаётся race-safe потому что только brand-new users (где ни wellness ни activities не возвращается) попадают в ветку, и у них dispatches в принципе не было.
