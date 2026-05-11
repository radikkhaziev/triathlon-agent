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
2. **Cursor через atomic UPDATE, без Redis.** Один step пишет `cursor=chunk_end+1`, следующий читает. Lost-update race'а нет — single-statement UPDATE WHERE status='running'. ORM helpers (`advance_cursor` / `mark_finished` / `mark_failed`) — все без read-modify-write.
3. **Watchdog escalation cap = 3 kick'а без advance'а cursor'а** → `mark_failed('watchdog_exhausted')`. Защита от infinite re-kick сломанной цепочки. Counter живёт в `last_error` как `watchdog_kick_N`, `advance_cursor` чистит при успешном прогрессе → reset автоматически.
4. **HRV baseline inline sync, не fan-out.** `process_wellness_analysis_sync` делает save + RHR + HRV + Banister + recovery синхронно в chronological loop — bootstrap вызывает inline, не через `actor_user_wellness.send()`. Cross-day ordering требует sync; training_log/athlete_settings остались async (per-day idempotent).
5. **Completion notification `delay=60_000`.** `actor_user_wellness.send()` fire-and-forget — к моменту finalize последний chunk's wellness actors ещё в полёте. 60s delay + completion actor пере-читает счётчики из БД.

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
