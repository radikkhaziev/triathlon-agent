# OAuth Bootstrap Sync Spec

**Status:** ✅ Phase 1+2 shipped (2026-04-21 / 2026-04-22). Closes [issue #226](https://github.com/radikkhaziev/triathlon-agent/issues/226).

Annual chunk-recursive backfill after Intervals.icu OAuth: один Dramatiq-actor (`actor_bootstrap_step`) идёт хронологически 30-дневными чанками, ре-диспатчит сам себя до конца периода. Persistent state в `user_backfill_state` (cursor-based, atomic UPDATE) — для UX-прогресса, resume'а, watchdog rescue.

---

## Where the code lives

| Layer | Artifact |
|---|---|
| OAuth callback fast-path + kick-off | `api/routers/intervals/oauth.py` |
| Chunk actor + finalize | `tasks/actors/bootstrap.py:actor_bootstrap_step` + `_finalize_bootstrap` |
| State ORM (atomic helpers) | `data/db/backfill.py:UserBackfillState` |
| Watchdog cron (Phase 2) | `bot/scheduler.py:scheduler_watchdog_bootstrap` |
| Manual rerun | `cli.py:bootstrap-sync` (`--force` resets state) |
| Webapp progress UI | `webapp/src/components/BackfillSection.tsx` (7-state button machine) |
| Retry endpoint (Phase 2) | `POST /api/auth/retry-backfill` (business cooldown + 1h anti-spam) |
| Post-onboarding nudge | `actor_send_onboarding_hey` + `UserBackfillState.hey_message` |

CLAUDE.md «Operations §Onboarding» — точка входа из docs; этот файл архивирует **почему**.

---

## Key parameters

- `CHUNK_DAYS = 30` — ~30-45 sec / step, ~13 итераций / год, wall-clock 3-5 min. Запас до Dramatiq `time_limit=300_000`.
- `period_days = 365` default; 180/90 через query param.
- 26 range-fetch'ей (`get_wellness_range` + `get_activities` per chunk) + per-activity-details dispatch (естественный worker-throttle).

---

## Decisions log (load-bearing)

1. **Chunk-recursion, не bulk+drain.** Хронологическая корректность downstream-анализа: HRV baseline rolling 7/60 дней — bulk + 365 concurrent compute'ов даст race на окно. Внутри чанка — chronological loop с inline `process_wellness_analysis_sync` (sort key через `date.fromisoformat(w.id)`, не lexicographic — code-reviewer 🔴 catch 2026-04-22).
2. **Cursor через atomic UPDATE, без Redis.** Один step пишет `cursor=chunk_end+1`, следующий читает. Lost-update race'а нет — single-statement UPDATE WHERE status='running'. ORM helpers (`advance_cursor` / `mark_finished` / `mark_failed`) — все без read-modify-write. С Phase 3 quota-пауз `advance_cursor` стал CAS (`expected_cursor=` → `False`, если курсор уже сдвинула другая копия цепочки): спящая в delay-queue копия + кик watchdog'а / `--force` иначе форкали цепочку (двойной расход квоты, двойной finalize + Telegram). Проигравшая копия молча останавливается; копия с аргументом *впереди* DB-курсора (после `start()`-reset) — «superseded generation», дропается на входе.
3. **Watchdog escalation cap = 3 kick'а без advance'а cursor'а** → `mark_failed('watchdog_exhausted')`. Защита от infinite re-kick сломанной цепочки. Counter живёт в `last_error` как `watchdog_kick_N`, `advance_cursor` чистит при успешном прогрессе → reset автоматически.
4. **HRV baseline inline sync, не fan-out.** `process_wellness_analysis_sync` делает save + RHR + HRV + Banister + recovery синхронно в chronological loop — bootstrap вызывает inline, не через `actor_user_wellness.send()`. Cross-day ordering требует sync; training_log остался async (per-day idempotent). Sport-settings в bootstrap **не** синхронятся вовсе — см. «Intervals.icu rate limits» ниже.
5. **Completion notification `delay=60_000`.** `actor_user_wellness.send()` fire-and-forget — к моменту finalize последний chunk's wellness actors ещё в полёте. 60s delay + completion actor пере-читает счётчики из БД.

---

## Intervals.icu rate limits (2026-09-10 outage)

Лимиты — на OAuth-приложение целиком, не per-athlete ([гайд](https://forum.intervals.icu/t/api-access-to-intervals-icu/609)): **100 запросов/день на каждого авторизовавшего атлета** (min 5000, max 50000; у нас 80 → 8000/day), **1/8 от дневного за скользящие 15 минут** (min 2500), плюс 10 req/s на IP (без заголовков). Каждый ответ несёт `X-RateLimit-Limit: <15m>,<day>` / `X-RateLimit-Remaining: <15m>,<day>`; 429 — `Retry-After` в секундах (для дневной квоты — до 00:00 UTC, т.е. часы) и `retry_after_seconds` в body.

**Что случилось 2026-09-10:** 6 новых юзеров за день (~2200 активностей). Bootstrap = 3 вызова на активность (detail / intervals / FIT) + `process_wellness_analysis_sync` слал `actor_sync_athlete_settings` на **каждый** wellness-день (~365 GET sport-settings на юзера, ~28% дневной квоты впустую). Квота кончилась в ~17:30 Belgrade; дальше retry-шторм: клиент резал `Retry-After` до 60 с × 5 попыток, Dramatiq добавлял ×3 — 1860 пустых 429 за час, 125 dead-letter'ов, бэкфиллы 105/106 в `failed`/stuck.

**Что сделано (Phase 1–2):**

- **Settings-sync только по делу.** Убран из `process_wellness_analysis_sync` (настройки — текущее состояние, не per-day). В `actor_user_wellness` API-fetch остался как страховка от пропущенного `SPORT_SETTINGS_UPDATED` webhook'а, но гейтится `AthleteSettings.is_stale(user_id, max_age=SETTINGS_SYNC_MAX_AGE)` (24h по `max(synced_at)`, который бампит и webhook-путь, и API-путь) → ≤1 запрос/юзер/день. Начальная загрузка при OAuth connect (fast-path) не тронута.
- **Quota-aware клиент** (`data/intervals/client.py`). На 429 с `Retry-After > RETRY_MAX_DELAY` (60 с) `_request` не спит, а сразу бросает `IntervalsRateLimitError(retry_after, method, path, quota)`. Короткий/отсутствующий `Retry-After` (per-second лимит) — прежний sleep-retry. Заголовки `X-RateLimit-*` парсятся в `client.quota: QuotaSnapshot` на каждом ответе; WARNING «daily quota low» раз в сутки на процесс при `remaining_day < DAILY_QUOTA_WARN_THRESHOLD` (1500).
- **`QuotaAwareRetries`** (`tasks/middleware.py`, подменяет `Retries` в `tasks/broker.py`). `IntervalsRateLimitError` наследует `dramatiq.Retry` — воркер не пишет error-трейсбек, Sentry не создаёт событие. Middleware пере-ставит сообщение в очередь с `delay = retry_after + jitter(0..120 с)` **без** инкремента `retries`, так что дневной outage не dead-letter'ит работу; после сброса квоты очередь доезжает сама. Единственный предохранитель — `QUOTA_MAX_DEFER_TOTAL_SEC` (3 суток суммарно на сообщение, проверяется по *прогнозу* «уже отложено + следующий delay»): структурная нехватка квоты dead-letter'ит с ERROR-логом вместо вечного цикла в delay-queue. Для pipeline/group это тот же `broker.enqueue(message, delay=)`, что и у штатных ретраев. MCP-тулы ловят `Exception` и возвращают Claude `str(e)` («quota exhausted … retry after 6h 42m»).
- `IntervalsRateLimitError` **не** наследует `IntervalsAccessError` — акторы глотают последний как «skip user», а квоту глотать нельзя.

**Восстановление после outage:** `bootstrap-sync --user-id N --force` (перезапуск с oldest; details только для новых активностей) + `sync-activities --user-id N --period A:B --force` для активностей без `activity_details`. Dead-letter'ы не переигрываются.

**Phase 3 — бюджетный резерв бэкфилла (2026-09-10):**

- `actor_bootstrap_step` после двух range-fetch'ей читает `client.quota.remaining_day`. Если он меньше `BOOTSTRAP_DAILY_RESERVE` (1500) + `BOOTSTRAP_REQUESTS_PER_ACTIVITY` (3) × активностей чанка — чанк **не** персистится (проверка стоит до `save_bulk`, иначе новые активности потеряли бы details fan-out навсегда), курсор не двигается, актор пере-ставит себя с `delay = seconds_until_quota_reset() + jitter(0..15 мин)` (00:00 UTC; jitter шире middleware'ных 120 с, потому что все паузы и deferral'ы просыпаются в одну секунду). Без заголовков (`quota is None`) бюджетирование невозможно — идём как обычно. Если `reserve + est_cost > limit_day` (чанк не влезает ни в один день) — не паузим (иначе вечный цикл «проснулся → 2 fetch'а → снова пауза»), идём с ERROR-логом, 429 добирает Phase 2 deferral.
- Пауза помечается `UserBackfillState.mark_quota_paused(resume_at)` → `last_error = QUOTA_PAUSED:<iso>` (сентинел в духе `watchdog_kick_N`, без миграции). Тот же штамп ставится при `IntervalsRateLimitError` откуда угодно внутри чанка (range-fetch, wellness-loop, будущие вызовы) — один `except` вокруг всего `_process_chunk`. `advance_cursor` чистит сентинел при прогрессе.
- Watchdog (`scheduler_watchdog_bootstrap`) пропускает строки с активной паузой до `resume_at + 15 мин`; после — обычная эскалация с кика №1 (счётчик стартует заново, потому что пауза перезаписала `watchdog_kick_N`).
- UI: `/api/auth/backfill-status` схлопывает сентинел в `QUOTA_PAUSED` (без timestamp), `BackfillSection` в состоянии running показывает нейтральное «пауза по лимиту API, продолжится автоматически (минуты для 15-минутного окна / после 00:00 UTC для дневной квоты)» вместо «загружаю» — сентинел один на оба случая. `retry-backfill` на паузе отвечает 409 как на любой running.
- Ограничение: резерв защищает только чанки бэкфилла и только дневную квоту; уже раскиданные `actor_update_activity_details` доедают квоту до нуля, а скользящее окно 15 минут (2500) резервом не покрыто — и то и другое спасает Phase 2 deferral (у 429 по окну `Retry-After > 60 с`, он уходит в middleware).

**Pending (Phase 4):** отдельная очередь `backfill` — только если после 1–3 наплыв регистраций всё ещё тормозит `default`.

---

## Idempotency cooldowns

| Сценарий | Cooldown | Поведение |
|---|---|---|
| `was_new=False` (refresh OAuth) | — | Bootstrap не триггерится |
| `status='running'` | — | Early-return, `.send` no-op |
| `completed`, `last_error != EMPTY_INTERVALS`, <7d | 7 дней | Skip (webhooks обслуживают incremental) |
| `completed`, `last_error == EMPTY_INTERVALS`, <1h | **1 час** | Intervals был пуст (Garmin догоняет) — короткий retry |
| `failed` / `completed` >7d | — | Allow rerun, state overwritten |

Все upserts ON CONFLICT идемпотентны.

---

## Security invariants (verified 2026-04-21/22)

- `user_backfill_state.user_id` FK + CASCADE.
- `actor_bootstrap_step(user_id)` — service actor (OAuth callback / CLI / watchdog), не из MCP → T1 не нарушает.
- `GET /api/auth/backfill-status` — `require_viewer`, читает по `current_user.id`, никаких параметров.
- `POST /api/auth/retry-backfill` — два независимых guard'а (business cooldown + in-process 1/hour). Demo-reject ДО rate-limit lookup'а (у demo `user.id == owner.id`).
- `last_error` через `_sanitize_last_error` allowlist (`api/routers/auth.py`) — allowlist: `EMPTY_INTERVALS`, `watchdog_exhausted`, `OAuth revoked during backfill`; всё остальное → `"internal"`. Защита от утечки raw `str(httpx_error)` с URL/токенами.

---

## Pending hardening

- **Multi-worker rate-limit lookup.** `_retry_backfill_last_success` живёт in-process — single-worker assumption. При scaling уйдёт в Redis INCR+EXPIRE.
- **`APP_SCOPE_CHANGED` webhook should call `UserBackfillState.mark_failed()`** if there's active state — cancels further step retries. Currently per-step deauth-guard catches it, но раньше будет чище.
- **Retention policy `user_backfill_state`** — 1 row/user навсегда. На 10k+ юзерах: `DELETE WHERE finished_at < now() - 90d AND status='completed'` + cron. Skip до явной нужды.
