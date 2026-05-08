# OAuth Bootstrap Sync Spec

> После успешного Intervals.icu OAuth автоматически бэкфилим историю (wellness, activities, training load) за год назад. Один chunk-recursive Dramatiq-actor идёт хронологически (oldest → newest) шагами по 30 дней и вызывает сам себя до конца периода. Persistent state — для UX-прогресса и resume'а.
>
> Закрывает [issue #226](https://github.com/radikkhaziev/triathlon-agent/issues/226).

**Status:** ✅ Phase 1+2 done (2026-04-21 / 2026-04-22). 1 follow-up — CLAUDE.md описание онбординга (✅ landed в Operations §Onboarding).

**Related:**

| Issue / Spec / code | Связь |
|---|---|
| [#226](https://github.com/radikkhaziev/triathlon-agent/issues/226) | Основной трекер |
| `api/routers/intervals/oauth.py` | OAuth callback fast-path + bootstrap kick-off |
| `tasks/actors/bootstrap.py` | `actor_bootstrap_step` + `_finalize_bootstrap` + start/completion senders |
| `data/db/backfill.py` | `UserBackfillState` ORM + atomic helpers |
| `bot/scheduler.py:scheduler_watchdog_bootstrap` | Watchdog cron (Phase 2) |
| `cli.py:bootstrap-sync` | Manual rerun (`--force`) |
| `webapp/src/components/BackfillSection.tsx` | Progress bar + button state machine |
| `tasks/actors/{wellness,activities,training_log,athlete,workouts}.py` | Per-day actors — остаются для daily cron, переиспользуются в fast-path |
| `docs/MULTI_TENANT_SECURITY_SPEC.md` T2 | Per-user credentials, OAuth-scoped |
| `docs/WEBHOOK_DATA_CAPTURE_SPEC.md` §6 | Backfill webhook-only полей (weather/MMP/achievements) — отдельная история |

---

## 1. Мотивация

Pre-spec dispatcher после OAuth слал только `actor_sync_athlete_settings` + `actor_user_wellness(today)`. Activities, исторические wellness, training_log — нет. Атлет заходит в webapp → пустой `/wellness` и `/activities` → «бот вообще работает?».

Issue #226 требует 30/90/14 дней. ML-фичи (`ML_HRV_PREDICTION_SPEC`, `ML_RACE_PROJECTION_SPEC`) хотят 180+ дней. Делаем годовой бэкфилл по умолчанию, range-API'шкой Intervals.icu (`oldest`/`newest` на `/wellness{ext}` + `/activities`) — ~26 range-запросов вместо 730 per-day, wall-clock 3-5 мин.

---

## 2. Scope

### Phase 1 (MVP) — ✅ shipped

- Таблица `user_backfill_state` для tracking + resume через cursor.
- Один **chunk-recursive actor** `actor_bootstrap_step(user, cursor_dt)` — 30-дневный chunk за вызов, продвигает cursor, ре-диспатчит до конца периода.
- Последний chunk финализирует inline: training_log enrichment + Telegram notify.
- OAuth callback: fast-path (today + settings + goals + workouts) + kick off первого step'а.
- Синхронный «первый день» (today wellness + settings + today activities + race goals) — webapp non-empty в течение 30 сек.
- Telegram start + completion (с coverage stats).
- Idempotency: повторный OAuth не триггерит новый бэкфилл если `status='completed'` <7d назад.
- `GET /api/auth/backfill-status` для webapp-прогресса.

### Phase 2 — ✅ shipped (quality-of-life)

- Progress bar в `/settings` (poll каждые 5s пока `running`).
- `<BackfillButton />` 7-state machine (§6) — primary/quiet/secondary/danger/disabled-countdown варианты.
- `POST /api/auth/retry-backfill` с двумя guard'ами (business cooldown + 1h anti-spam rate limit).
- Configurable period (default 365, можно ужать до 180/90 через query param).
- `scheduler_watchdog_bootstrap` cron — rescue stuck state после Dramatiq's `max_retries`, escalation 3 kick'а → `watchdog_exhausted`.

### Non-goals (decisions)

- **Concurrent parallel chunks** — даёт N×-ускорение, но ломает хронологический порядок downstream-анализа (HRV baseline дня N зависит от N-7/N-60) и съедает rate limit.
- **Bulk save всей истории + отдельный finalize-drain** — для sparse activities counter-bookkeeping требует Redis-set intersection; downstream-анализ в случайном порядке ломает rolling-window baselines.
- **Рекурсивный per-day pipeline (365 итераций)** — избыточен по API calls (730 HTTP вместо 26) и enqueue overhead. Chunk'и по 30 дней — компромисс между bulk-эффективностью и chronological-correctness.
- **Backfill webhook-only полей** (weather/MMP/achievements) — `WEBHOOK_DATA_CAPTURE_SPEC` §6, разовый прогон после merge'а.

---

## 3. Архитектура — chunk-recursive self-rescheduling step

```
OAuth callback (was_new=True)
    │
    ├─ Synchronous fast-path (<2s, non-blocking sends):
    │    • actor_sync_athlete_settings
    │    • actor_sync_athlete_goals               ← RACE_A/B/C видны сразу
    │    • actor_user_wellness(today)
    │    • actor_fetch_user_activities(today, today)
    │    • actor_user_scheduled_workouts          ← 14 дней вперёд
    │    • _actor_send_bootstrap_start_notification
    │
    └─ actor_bootstrap_step.send(user, cursor_dt=oldest, period_days=365)
         │
         ▼
    actor_bootstrap_step  ← один актор, всё в нём
         │
         ├─ (First call) UserBackfillState.upsert(status='running', cursor=oldest)
         │  (Subsequent) read state, guard status=='running', deauth-guard
         │
         ├─ chunk_end = min(cursor + CHUNK_DAYS - 1, newest_dt)
         │
         ├─ 1 HTTP: client.get_wellness_range(cursor, chunk_end)
         ├─ 1 HTTP: client.get_activities(cursor, chunk_end)
         │
         ├─ Chronological loop for dt in [cursor .. chunk_end]:
         │    • Wellness.upsert(dt) + process_wellness_analysis_sync(dt)
         │    • Activity.save_bulk(...) + actor_fetch_activity_details.send(id)
         │
         ├─ UserBackfillState.advance_cursor(chunk_end + 1)  (atomic UPDATE)
         │
         ├─ if chunk_end < newest_dt:
         │     actor_bootstrap_step.send(user, cursor=chunk_end + 1)
         │     return
         │
         └─ else: _finalize_bootstrap(user, state)
               • actor_recalculate_training_log.send (если нужно)
               • EMPTY_INTERVALS sentinel если wellness_count + activity_count == 0
               • UserBackfillState.mark_finished('completed')
               • _actor_send_bootstrap_completion_notification.send(delay=60s)
```

### Почему chunk-recursion, а не bulk+drain

- **Хронологическая корректность downstream.** HRV baseline — rolling 7/60 дней. Bulk + 365 concurrent `_actor_compute_wellness_analysis` → race на окно. Chronological loop внутри chunk'а гарантирует порядок.
- **Counter тривиальный.** `progress_pct = (cursor - oldest) / period_days`. Никаких Redis-множеств, никакого AND-intersection по двум sparse источникам.
- **Resume бесплатный.** Dramatiq at-least-once пере-доставляет in-flight step. Chain оборвался → watchdog видит `status='running' AND last_step_at > 15min ago` и перезапускает с `state.cursor_dt`.
- **Observability встроена.** Каждый step логирует `cursor → chunk_end`.
- **Одна задача в очереди.** 10 параллельных onboarding'ов = 10 задач, не 10 × 365 = 3650.
- **Idempotency встроена.** Retry всего step'а = no-op (upserts + `actor_fetch_activity_details` идемпотентны).

### `CHUNK_DAYS = 30` — эмпирический выбор

- Step ~30-45 сек (2 range-fetch'а + 5-15 details dispatch + HRV/recovery inline по 30 датам).
- ~13 итераций на год → 3-5 мин wall-clock.
- Огромный запас до Dramatiq's `time_limit=300_000` (5 мин per chunk, есть `max_retries=3`).
- API volume: 13 × 2 = 26 range-запросов + 50-150 per-activity details через естественный worker-throttle.

### Concurrency со scheduler'ом

`actor_user_wellness(today)` от cron'а крутится каждые 10 мин; bootstrap трогает `[oldest .. today-1]`. `newest_dt = today - 1` зафиксирован на момент первого step'а — пересечений дат **by construction** нет. Полночь edge: bootstrap прошёл до старого `newest`, scheduler возьмёт новый `today`; upsert idempotent. На `activity_details` гонка возможна (chunk и scheduler оба триггерят `actor_fetch_activity_details` для одного `activity_id`) — `ActivityDetail.save` делает ON CONFLICT UPSERT, consistent.

---

## 4. Cursor semantics — нет races, нет Redis

Каждый step:
1. Читает `cursor_dt` (следующий необработанный день).
2. Обрабатывает `[cursor_dt .. min(cursor_dt + CHUNK_DAYS - 1, newest_dt)]`.
3. Атомарным `UPDATE … WHERE status='running'` пишет `cursor=chunk_end+1`, `last_step_at=now()`, `chunks_done+=1`.
4. Если `chunk_end >= newest_dt` → `_finalize_bootstrap` inline.

Один actor пишет cursor, следующий читает. Lost-update race'а нет. ORM helpers (`advance_cursor` / `mark_finished` / `mark_failed`) — все построены на single-statement `UPDATE`, никакого read-modify-write, никакого Redis-bookkeeping. Реализация: `data/db/backfill.py`.

`hey_message` (post-onboarding nudge таймстамп) сбрасывается в NULL вместе со всеми остальными при `--force` retry, что вместе с фильтром `status='completed'` в cron'е (§6.6) гарантирует, что повторный nudge уйдёт ТОЛЬКО когда новый бутстрап реально завершится.

---

## 5. Failure semantics

### 5.1. Transient внутри step'а

Dramatiq `max_retries=3` + exp backoff. Intervals 429 → retry через 60s. Network errors → 5s/30s/150s. Retry перезапускает весь step; upserts идемпотентны.

### 5.2. Worker restart

In-flight step: at-least-once redelivery после restart'а. Scheduled next step (`actor_bootstrap_step.send()` после commit'а cursor'а) — message в Redis, worker подберёт.

### 5.3. Chain обрыв после исчерпания `max_retries`

Step exhausted → message failed → `state.status='running'` навечно. **Watchdog cron** (`scheduler_watchdog_bootstrap`, каждые 10 мин) ищет `running AND last_step_at < now() - 15min` → re-dispatch `actor_bootstrap_step(cursor=state.cursor_dt)`. Cursor CAS гарантирует, что chain подхватится с последней зафиксированной позиции.

**Escalation:** `_BOOTSTRAP_MAX_WATCHDOG_KICKS=3` без advance'а cursor'а → `mark_failed(error='watchdog_exhausted')`. Защита от infinite re-kick broken chain'а. Счётчик живёт в `last_error` как `watchdog_kick_N`; `advance_cursor` чистит `last_error=None` при успешном прогрессе → counter reset автоматически.

### 5.4. Ручной rerun

`python -m cli bootstrap-sync <user_id> [--period 365] [--force]`. С `--force` ресетит state через `UserBackfillState.start()`, обходит idempotency guard.

---

## 6. Idempotency

| Сценарий | Cooldown | Поведение |
|---|---|---|
| `was_new=False` (refresh OAuth) | — | Bootstrap не триггерится. |
| `status='running'` | — | `actor_bootstrap_step` early-return, повторный `.send` no-op. |
| `status='completed'`, `last_error != 'EMPTY_INTERVALS'`, <7d | 7 дней | Skip (webhooks обслуживают incremental updates). |
| `status='completed'`, `last_error == 'EMPTY_INTERVALS'`, <1h | **1 час** | Intervals был пуст (Garmin ещё не доехал). Короткий cooldown — retry пока Intervals догоняет. |
| `status='failed'` / `completed` >7d ago | — | Разрешаем rerun (state overwritten, cursor сброшен на oldest). |

Внутри chunk'а идемпотентность встроена: `Wellness.upsert` + `Activity.save_bulk` (ON CONFLICT) + `actor_fetch_activity_details` (skip if exists).

---

## 7. UX

### 7.1. Button state machine (`<BackfillButton />`)

Webapp читает `/api/auth/backfill-status` + `finished_at` + `last_error` → рендерит кнопку по таблице:

| `status` | data | `last_error` | Время с `finished_at` | Кнопка |
|---|---|---|---|---|
| `none` | — | — | — | **«Загрузить историю»** (primary) |
| `running` | — | — | — | Скрыта, progress bar |
| `completed` | есть | — | <7d | Quiet «✅ История загружена» |
| `completed` | есть | — | ≥7d | Secondary «Пересинхронизировать» |
| `completed` | пусто | `EMPTY_INTERVALS` | <1h | Disabled countdown «Доступно через N мин» |
| `completed` | пусто | `EMPTY_INTERVALS` | ≥1h | Primary «Повторить импорт» |
| `failed` | — | `<error>` | — | Danger «Попробовать снова» + tooltip с sanitized error |

**Сервер не вычисляет button text** — только возвращает state, UI решает. `last_error` проходит `_sanitize_last_error` allowlist (`api/routers/auth.py`): `EMPTY_INTERVALS`, `watchdog_exhausted`, `OAuth revoked during backfill` пропускаются; `watchdog_kick_N` → `None`; всё остальное → `"internal"`. Defensive guard от raw `str(httpx_error)` с URL/токенами.

### 7.2. Webhooks vs ручной re-import

Если Intervals после bootstrap'а подтянет старые Garmin данные — каждая новая wellness/activity запись прилетит через `WELLNESS_UPDATED` / `ACTIVITY_UPLOADED` webhook, dispatcher запишет. **Ручной re-import нужен только** когда webhooks пропустили или юзер явно хочет всё перечитать (новый device в Garmin с годами истории — редкий кейс).

### 7.3. Post-onboarding nudge (issue #258)

Через 24-48ч после `finished_at` cron-job отправляет one-shot Telegram nudge «эй, можешь со мной чатиться» — для тех, кто прошёл OAuth, но молчит. Идемпотентность через mark-first: `actor_send_onboarding_hey` сначала `UserBackfillState.mark_hey_sent(user_id)` (атомарный `UPDATE … RETURNING`); race теряет один nudge при сбое send'а — приемлемо, т.к. one-shot UX.

---

## 8. Multi-tenant / security

- `user_backfill_state.user_id` FK + CASCADE — tenant-scoped.
- `actor_bootstrap_step(user_id)` — сервисный actor, вызывается из OAuth callback, CLI или watchdog (не из MCP). Не нарушает T1 — данные не отдаются через MCP, фон.
- `GET /api/auth/backfill-status` через `require_viewer`, читает row по `current_user.id` (не по параметру) → 100% tenant isolation.
- `POST /api/auth/retry-backfill` — два independent guard'а: business cooldown (7d/1h/immediate в зависимости от завершённого статуса) + anti-spam in-process 1/hour per user. Demo-reject ДО rate-limit lookup'а (у demo `user.id == owner.id`, иначе demo-сессия делила бы budget владельца).
- `IntervalsSyncClient.for_user(user)` читает per-user Fernet-encrypted токен (`users.intervals_access_token_encrypted`).

Security review (2026-04-21, 2026-04-22): T1/T2/T14 invariants держатся. Все ORM-запросы scoped по `user_id`. Open hardening (post-Phase 2): `_retry_backfill_last_success` живёт in-process, single-worker assumption — при scaling уйдёт в Redis INCR+EXPIRE.

---

## 9. Decisions log

- **Sort key wellness inside chunk** — `date.fromisoformat(w.id)`, не lexicographic string. Lexicographic был coupled к текущему ISO-date shape Intervals' ID, мог silently сломаться на format change. Unparseable IDs → `date.max`, идут в конец (2026-04-22, code-reviewer 🔴 critical).
- **Watchdog escalation cap** — 3 kick'а без advance'а → `watchdog_exhausted`. Защищает от infinite re-kick.
- **Completion notification 60s delay.** `actor_user_wellness.send()` fire-and-forget — к моменту finalize последний chunk's wellness actors ещё в полёте. `send_with_options(delay=60_000)` + completion actor сам пере-читает счётчики из БД.
- **HRV baseline ordering — inline sync, не fan-out.** `process_wellness_analysis_sync` в `tasks/actors/wellness.py` делает save + RHR + HRV + Banister + recovery синхронно. Bootstrap вызывает inline в chronological loop вместо `actor_user_wellness.send()`. Фан-аут (training_log enrichment, athlete_settings sync) остался async — per-day idempotent, cross-day ordering не нужен.
- **Retention policy на `user_backfill_state`** — 1 row/user навсегда. На 100+ юзерах незаметно; на 10k+ — `DELETE WHERE finished_at < now() - 90d AND status='completed'` + cron. В MVP skip.

---

## 10. Open questions

- **CHUNK_DAYS tuning.** 30 — стартовый выбор. Если step превысит `time_limit=5min` — уменьшить до 14 или 7. Configurable через CLI flag.
- **Rate-limit throttle на per-activity details.** Peak ~40-50 req/min. Если Intervals начнёт 429'ить — `dramatiq-rate-limit` middleware с 50 req/min per user. Не нужно сейчас.
- **Late-arriving Garmin данные** — покрыто: (a) `WELLNESS_UPDATED` / `ACTIVITY_UPLOADED` webhooks, (b) Empty-import cooldown 1h, (c) Completed re-sync cooldown 7d.
- **Deauth mid-backfill.** Per-step deauth-guard в `actor_bootstrap_step` (читает `user.intervals_auth_method == 'none'` → `mark_failed`). `APP_SCOPE_CHANGED` webhook должен вызвать `UserBackfillState.mark_failed()` если есть active state — отменяет дальнейшие step retries.
- **Backfill webhook-only полей.** Weather/MMP/achievements приходят только через `ACTIVITY_UPLOADED` webhook. Для исторических activities эти поля будут null. Отдельный `backfill-webhook-data` actor — разовый прогон после merge'а `WEBHOOK_DATA_CAPTURE_SPEC` Phase 3.
