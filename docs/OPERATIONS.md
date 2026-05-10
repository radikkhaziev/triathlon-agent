# Operations Guide

> Bot commands, API endpoints, CLI, migrations, onboarding, Docker.
> Operational reference — read on demand. The root `CLAUDE.md` keeps architecture, business rules, and patterns.

---

## Bot Commands (`bot/main.py`)

Commands use `@athlete_required` (needs `athlete_id`) or `@user_required` (any active user) decorator — resolves `User` from Telegram `chat_id`.

```
/start      — welcome + create User in DB. Branches on `athlete_id`: new users get "🔗 Подключить Intervals.icu" WebApp button → /settings onboarding. Existing athletes get the generic dashboard entry.
/dashboard  — dashboard link (Mini App) — opens today's morning report in webapp
/workout    — interactive workout generation: sport picker → dry-run preview → "Отправить в Intervals" button
/race       — lightweight entry point for race creation: sends a priming message; user describes the race in free-form, preview+confirm via `suggest_race` MCP tool
/web        — one-time code for desktop login (5 min TTL)
/stick      — increment IQOS stick counter for today (owner only)
/health     — server diagnostics: system stats, DB/Redis/queues, per-athlete Intervals.icu token check (OAuth/api_key), daily token usage per user, Anthropic (owner only)
/lang       — set language: /lang ru or /lang en (@user_required — works for viewers too)
/silent     — toggle silent mode (@user_required — works for viewers too)
/whoami     — show current user info (chat_id, role)
/donate     — voluntary support via Telegram Stars (XTR), 3 tiers (50/200/500)
<text>      — free-form AI chat (stateless, tool-use via MCP, per-user token). Triggers `suggest_race` preview+confirm for race-creation requests.
<photo>     — AI chat with vision (base64 image + caption). Same `suggest_race` preview+confirm flow as `<text>`.
<reply>     — reply context included as "[В ответ на: ...]"
```

**Callback handlers:** `ramp_test:{sport}` — create ramp test, `update_zones` — update HR zones, `workout:{sport}` / `workout_push` / `workout_cancel` — `/workout` ConversationHandler states, `race_push` / `race_cancel` — suggest_race confirm/dismiss in free-form chat (standalone, not in a ConversationHandler), `rpe:{activity_id}:{value}` — single-shot RPE rating from post-activity notification (see `docs/RPE_SPEC.md`), `card:{activity_id}` — generate Instagram workout card PNG (GPS track + metrics + AI text) via `actor_generate_workout_card`.

**Ramp test post-activity flow:** `_actor_send_activity_notification` detects ramp-test activities via `_is_ramp_test_activity` (matches a `ScheduledWorkout` with `"Ramp Test"` in name on same date/sport) and routes to `build_ramp_test_message` (`tasks/formatter.py`) instead of the generic message. HRVT regression in `detect_hrv_thresholds` filters by WORK-segments from `activity_details.intervals` to exclude WU/CD/recovery noise. On detection failure `diagnose_hrv_thresholds` returns a structured `{code, ...}` dict localized in the formatter. The inline `Обновить зоны` button is shown when drift exceeds the per-metric absolute gate (`DRIFT_LTHR_BPM=3` / `DRIFT_PACE_SEC_PER_KM=5` / `DRIFT_FTP_WATTS=5`) AND `R²` lands in the medium tier (`0.70 ≤ R² < 0.85`). At `R² ≥ 0.85` (high tier) the actor auto-fires `actor_update_zones` without a button; at `R² < 0.70` (low tier) only a soft hint surfaces. Mirrors `User.detect_threshold_drift` so UI and backend never disagree. See `docs/ADAPTIVE_TRAINING_PLAN_SPEC.md` Фаза 4 + `docs/RAMP_TEST_BIKE_SPEC.md` §8 for the full protocol.

**`/workout` two-phase flow:** generation calls `suggest_workout` (or `compose_workout` for fitness) with `dry_run=True` / `push_to_intervals=False`. `bot/agent.py:chat()` returns `ChatResult(text, tool_calls, nudge_boundary, request_count)` — `tool_calls` holds every tool_use block Claude emitted (deep-copied), filtered via the `tool_calls_filter` param to `set(_PREVIEWABLE_TOOLS.keys())` to avoid copying unrelated large inputs. The handler stashes the last previewable call in `context.user_data["pending_workout"]`. On "✅ Отправить в Intervals" tap, `workout_push` pops the draft, flips the preview flag, and calls `MCPClient.call_tool` directly **without** re-invoking Claude — so what lands in Intervals.icu is bit-for-bit identical to the preview. Prevents prompt-injection on the state-mutating step and saves one inference round per push. See `bot/main.py:_PREVIEWABLE_TOOLS` for the flag-name mapping.

**Race creation via chat:** same preview/confirm pattern as workouts, for future races. In free-form chat (`handle_chat_message` / `handle_photo_message`) `tool_calls_filter=_RACE_TOOLS` catches `suggest_race(dry_run=True)`, renders a confirm button, and `race_push` replays with `dry_run=False` via direct MCP call — no re-inference, bit-for-bit same event goes to Intervals.icu. Idempotency by `(user_id, category)` — one active RACE_A/B/C per athlete; new date on same category triggers `update_event`, not create. `ctl_target` is local-only (Intervals doesn't store it) — written via `AthleteGoal.set_ctl_target`, which `upsert_from_intervals` deliberately leaves alone so the 30-min sync actor can't overwrite user input. Editable also via Settings UI: `PATCH /api/athlete/goal/{goal_id}` with `ctl_target` / `per_sport_targets` (local-only fields), sentinel-based partial update — see `api/routers/athlete.py`. Deletion via `delete_race_goal(category)` MCP tool (removes event from Intervals + soft-deletes local row).

**Donate nudge:** after every N-th chat request (default N=5), free-form handlers (`handle_chat_message`, `handle_photo_message`) append a nudge as a **separate** Telegram message via `bot/donate_nudge.py:get_nudge_text()`. Policy lives in `should_show_nudge(user, nudge_boundary, request_count)` — agent only reports the raw `nudge_boundary` signal, all suppression rules (owner opt-out, recent donation, daily cap) apply in the handler. `/workout` handlers deliberately skip the nudge (rating limit counted, but not shown — see `DONATE_SPEC.md` §11.6). Suppression after a donation: `User.last_donation_at` is set in `successful_payment_callback` via `User.mark_donation`, and `should_show_nudge` skips for `DONATE_NUDGE_SUPPRESS_DAYS` (default 7 days).

---

## API Endpoints

```
GET  /api/report                        — full morning report (today)
GET  /api/wellness-day?date=YYYY-MM-DD  — wellness for any date (navigable)
GET  /api/scheduled-workouts?week_offset=0 — weekly plan (Mon-Sun)
GET  /api/activities-week?week_offset=0 — weekly activities
GET  /api/activity/{id}/details         — full activity stats + zones + DFA
GET  /api/progress?sport=bike&days=90   — aerobic efficiency trend (EF/SWOLF/pace)
GET  /api/polarization?sport=run&days=28 — zone distribution (Low/Mid/High) + multi-window + signals
GET  /api/fitness-projection            — CTL/ATL/rampRate decay curve (from FITNESS_UPDATED webhook)
POST /api/auth/verify-code              — verify one-time code → JWT
POST /api/auth/demo                     — demo password → JWT with role=demo (read-only owner data)
GET  /api/auth/me                       — auth status + language + intervals connection + profile/goal
GET  /api/auth/mcp-config                — per-user MCP config (rate-limited, audit-logged)
PUT  /api/auth/language                 — update user language (ru/en)
PATCH /api/athlete/goal/{goal_id}        — update local-only overlay (ctl_target, per_sport_targets); sentinel-partial
POST /api/intervals/auth/init            — initiate OAuth (authenticated XHR) → {authorize_url}
GET  /api/intervals/auth/callback        — OAuth callback: code → token → DB → redirect
POST /api/intervals/auth/disconnect      — clear OAuth tokens (user can reconnect anytime)
POST /api/intervals/webhook              — Intervals.icu push webhooks: secret verification, DTO parsing, Sentry monitoring. 10/10 event types researched (see docs/INTERVALS_WEBHOOKS_RESEARCH.md)
GET  /api/auth/backfill-status           — cursor-based progress of the OAuth bootstrap backfill (status/cursor_dt/progress_pct/chunks_done), tenant-scoped. `last_error` is sanitized to an allowlist (EMPTY_INTERVALS / watchdog_exhausted / OAuth revoked / internal)
POST /api/auth/retry-backfill            — manual re-run of OAuth bootstrap. Dual cooldown: 7d after completed+data, 1h after EMPTY_INTERVALS, immediate for failed; plus 1h per-user anti-spam rate limit. 409 if already running, 429 with Retry-After on cooldown/rate-limit
POST /api/jobs/sync-wellness            — dispatch dramatiq actor (require_athlete)
POST /api/jobs/sync-workouts            — dispatch dramatiq actor (require_athlete)
POST /api/jobs/sync-activities          — dispatch dramatiq actor (require_athlete)
GET  /api/changelog/latest               — latest weekly Discussion (`{url, title, published_at}` or 404). 1h in-process cache (200 + 404). 503+`Retry-After:300` on GitHub-side failure. `require_viewer` (demo can read). See `docs/WEEKLY_CHANGELOG_SPEC.md`
GET  /health
POST /telegram/webhook                  — webhook mode only
POST /mcp                               — MCP (Streamable HTTP, Bearer auth)
GET  /static/exercises/{id}.html        — generated exercise card HTML (StaticFiles)
GET  /static/workouts/{date}-{slug}.html — generated workout HTML (StaticFiles)
```

**Dashboard API** (real per-user, in `api/routers/dashboard.py`): `/api/training-load`, `/api/activities`, `/api/recovery-trend`, `/api/weekly-recap`, `/api/goal` (`{has_goal: false}` when no active race; React hides the Goal tab in that case). Activities/recap drop sports that don't normalize to Swim/Ride/Run (yoga, hike, weights → not on the chart and excluded from week TSS). Still mock in `api/dashboard_routes.py`: `/api/dashboard` (legacy, no current consumer after Today page removal), `/api/jobs/morning-report`, `/api/jobs/sync-wellness`.

**Auth:** Two methods in `Authorization` header — Telegram initData (HMAC-SHA256, 15-min freshness) or `Bearer <jwt>`. Demo mode: `POST /api/auth/demo` with `DEMO_PASSWORD` → JWT with `purpose=demo` claim, resolved to owner's User with virtual `role="demo"` (read-only, mutation endpoints blocked via `require_athlete`). Resolves to `User` object via `get_current_user()`. Dependencies: `require_viewer` (any authenticated user), `require_athlete` (active + athlete_id, blocks demo), `require_owner`. `get_data_user_id(user)` always returns `user.id`. API DTOs centralized in `api/dto.py`.

---

## Webapp (`webapp/`) — React SPA

React 18 + TypeScript + Vite 6 + React Router v7 + Tailwind CSS v3 + Chart.js v4 + React Context. Light theme, Inter font, mobile-first, Telegram Mini App compatible.

**Routes:** `/` → redirect to `/wellness` for authenticated users, Landing for guests. `/wellness` (home), `/plan`, `/activities`, `/activity/:id`, `/dashboard` (3 tabs), `/progress`, `/settings`, `/login`. `/report` is a legacy alias → `/wellness` (used by morning-report Telegram link). Bottom tabs navigation. Wellness page exposes a manual sync button (`POST /api/jobs/sync-wellness`) to all athletes on today's view.

**Auth:** `AuthProvider` (React Context): Telegram initData → JWT fallback → anonymous. `useAuth()` hook. Desktop: `/web` → 6-digit code → JWT. **Global auth gate** in `App.tsx`: fetches `/api/auth/me` on login, checks `intervals.athlete_id`. If missing → all data routes render `<OnboardingPrompt />` (issue #185). Settings and Login always accessible for OAuth onboarding.

**i18n:** `react-i18next` with `ru.json` / `en.json`. Backend sends localized strings for wellness verdicts (`_cv_verdict`, `_swc_verdict`, `_format_sleep_duration`) and recovery categories (`get_category_display`, `get_recommendation_text`) based on `user.language`. Frontend `StatusBadge` uses i18n keys.

**Build:** Dev: `cd webapp && npm run dev` (:5173, proxies /api → :8000). Prod: Docker multi-stage Node 20 → Python 3.12.

---

## CLI (`cli.py`)

```bash
python -m cli shell                                              # interactive Python shell with app context
python -m cli sync-settings <user_id>                            # sync athlete settings & goals from Intervals.icu
python -m cli sync-wellness <user_id> [period]                   # force re-sync wellness + HRV/RHR/recovery day by day
python -m cli broadcast-migration [--dry-run]                    # notify active athletes about bot migration (one-time)
python -m cli sync-activities <user_id> [period] [--force]       # force re-sync activities day by day
python -m cli sync-training-log <user_id> [period]               # recalculate training log from existing activities
python -m cli import-garmin <user_id> <source> [--types] [--period] [--force] [--dry-run]  # import Garmin GDPR export
python -m cli backfill-races <user_id> [period]                  # create Race records for historical race activities
python -m cli bootstrap-sync <user_id> [--period 365] [--force]  # chunk-recursive OAuth bootstrap backfill (wellness + activities)
python -m cli reprocess-ramp-test <user_id> <activity_id> [--push]  # back-fill hrvt2_pace (Run) / hrvt2_power (Ride) on one ramp test (post v2c3d4e5f6a7 / w3d4e5f6a7b8)
python -m cli publish-changelog [--force]                        # manually trigger the weekly changelog (idempotent by week; --force overrides)
```

### `publish-changelog`

Manual trigger for the weekly changelog publisher (`docs/WEEKLY_CHANGELOG_SPEC.md`). Same code path as the Sun 15:00 cron — fetches merged PRs from the last 7 days, runs the pre-filter, asks Claude for a 3-7-bullet summary, publishes a GitHub Discussion in `Announcements`.

Idempotent by week: if a Discussion was created within the last 7 days 12 hours (the padded window — see spec §13), the actor returns `skipped_already_published` and does NOT publish a duplicate. So a Wed manual trigger naturally blocks the next Sun cron from double-posting.

`--force` skips the idempotency check — use only when you really want a second digest in the same week (e.g., a major mid-week feature launch). Cron always runs without `--force`.

```bash
# Smoke test on prod after deploy: publishes a real Discussion
docker compose run --rm api python -m cli publish-changelog
```

### `reprocess-ramp-test`

Re-runs `detect_hrv_thresholds` on a stored ramp test's `dfa_timeseries` to populate the HRVT2-derived columns: `activity_hrv.hrvt2_pace` (Run, added by migration `v2c3d4e5f6a7`, 2026-05-08) and `activity_hrv.hrvt2_power` (Ride, added by `w3d4e5f6a7b8`, 2026-05-08; both NULL on pre-migration rows). Patches **only** the HRVT2-derived fields — other threshold fields (HRVT1/2 HR, R², confidence) stay untouched, since re-running the detector can produce slightly different float-rounded values that we don't want to perturb.

With `--push`, also dispatches `actor_update_zones` after patching so the drift detector picks up the freshly-populated HRVT2 fields and pushes the corrected `lthr` + `threshold_pace` (Run) or `ftp` (Ride) to Intervals.icu in one shot. The user gets a Telegram «✅ Зоны обновлены» notification.

Refuses `--push` if the activity is **not** the latest valid ramp for its sport — drift detector reads `LIMIT 1 ORDER BY date DESC`, so pushing a back-filled older activity has no effect on the next dispatch and would mislead the user.

Idempotent — repeated runs overwrite the same recomputed values.

```bash
# Dry-run: just back-fill hrvt2_pace / hrvt2_power
docker compose exec api python -m cli reprocess-ramp-test 1 i146377549

# Full path: patch + push to Intervals.icu + notify the user
docker compose exec api python -m cli reprocess-ramp-test 1 i146377549 --push
```

### Period formats for `sync-wellness` and `sync-activities`

| Format                    | Example                  | Result                         |
| ------------------------- | ------------------------ | ------------------------------ |
| (none)                    | `sync-activities 2`      | Last 180 days                  |
| Quarter                   | `sync-activities 2 2025Q4`| 2025-10-01 → 2025-12-31      |
| Month                     | `sync-activities 2 2025-11`| 2025-11-01 → 2025-11-30     |
| Date range                | `sync-activities 2 2025-01-01:2025-03-31` | Explicit range |

All sync commands dispatch dramatiq tasks with 20s delay between days. Requires a running worker (`dramatiq tasks.actors`) and Redis.

---

## Database Migrations (Alembic)

```bash
# Apply all pending migrations
alembic upgrade head

# Create a new migration (auto-detect model changes)
alembic revision --autogenerate -m "description"

# Show current revision
alembic current

# Show migration history
alembic history

# In Docker
docker compose run --rm api alembic upgrade head
```

Migrations run automatically on deploy via the `migrate` service in `docker-compose.yml`.

---

## Onboarding a New User

### Automatic OAuth onboarding (default path — public users)

1. User sends `/start` to the bot → `User` row created with `role=viewer`, no `athlete_id`.
2. `/start` returns a WebApp button "🔗 Подключить Intervals.icu" → opens `/settings` page.
3. User taps "Connect Intervals.icu" → frontend XHR `POST /api/intervals/auth/init` → redirect to Intervals.icu consent.
4. OAuth callback (`GET /api/intervals/auth/callback`): stores OAuth token, promotes viewer → athlete, generates `mcp_token`, then **auto-dispatches**:
   - **Fast-path (<2s, non-blocking):** `actor_sync_athlete_settings` + `actor_sync_athlete_goals` + `actor_user_wellness(today)` + `actor_fetch_user_activities(today, today)` + `actor_user_scheduled_workouts` + Telegram start notification. Webapp shows non-empty state within ~30s.
   - **Slow-path:** `actor_bootstrap_step(cursor_dt=oldest, period_days=365)` — chunk-recursive backfill of 1 year of wellness + activities. Chunks of 30 days, ~3-5 min wall-clock. Progress visible via `GET /api/auth/backfill-status` (polls every 5s when `status='running'`). Telegram sends completion notification with final counts after +60s delay (allows last chunk's wellness fan-out to drain).
5. State lives in `user_backfill_state` (1 row/user, cursor-based: `oldest_dt`/`newest_dt`/`cursor_dt`/`chunks_done`/`status`). Idempotency: 7-day cooldown on `completed`, 1-hour cooldown on empty-import (`last_error='EMPTY_INTERVALS'`). See `docs/OAUTH_BOOTSTRAP_SYNC_SPEC.md`.
6. **Post-onboarding nudge** — 24-48h after `finished_at`, `scheduler_onboarding_hey_job` (hourly 09:00–21:00 in `settings.TIMEZONE`) sends a friendly «hey, you can chat» Telegram message via `actor_send_onboarding_hey`. Idempotent via `user_backfill_state.hey_message` timestamp + `RETURNING`-guarded mark-first. `start()` (`--force` retry) resets `hey_message` to NULL — combined with the `status='completed'` filter, a re-run only re-nudges after the new bootstrap finishes. Issue #258.

### Manual CLI onboarding (legacy / admin path)

Used for existing athletes migrated from api-key, or to pre-seed an account before user connects.

#### Step 1: User sends /start to the bot

The bot creates a `User` row with `role=viewer`, no `athlete_id`.

#### Step 2: Owner configures user credentials via shell

```bash
python -m cli shell
```

```python
from data.db import User
from data.db.common import get_sync_session

with get_sync_session() as s:
    user = s.get(User, 2)  # user_id
    user.role = "athlete"
    user.athlete_id = "i543070"       # Intervals.icu athlete ID
    user.set_api_key("your-api-key")  # encrypted in DB via Fernet
    user.mcp_token = "generated-token"
    user.age = 30
    user.sports = ["swim", "ride", "run"]   # subset of {"swim","ride","run"}; null=picker on next webapp open
    s.commit()
```

#### Step 3: Sync athlete settings from Intervals.icu

```bash
python -m cli sync-settings 2
```

Pulls sport-specific thresholds (LTHR, FTP, max HR, threshold pace) and race goals (RACE_A/B/C events) from Intervals.icu into `athlete_settings` and `athlete_goals` tables.

#### Step 4: Sync historical data

```bash
python -m cli sync-wellness 2               # 1. wellness + training log POST
python -m cli sync-activities 2             # 2. activities + training log PRE/ACTUAL
```

For each day: fetches wellness data (HRV, CTL, sleep) and activities from Intervals.icu, computes HRV/RHR baselines, Banister/ESS, recovery scores, and syncs activity details.

#### Step 5 (optional): Set CTL targets for goals via shell

#### Quick onboard (alternative to Steps 3-4)

```bash
python -m bot.cli onboard <user_id> --days 180
```

### Re-run the automatic OAuth bootstrap manually

For existing athletes whose row in `user_backfill_state` is stale or failed:

```bash
python -m cli bootstrap-sync <user_id> [--period 365] [--force]
```

`--force` resets the state row (cursor → oldest, status → running) so the actor's `status != 'running'` guard fires on a fresh row. Without `--force` and an existing `completed` row, the actor short-circuits with a log line.

Runs sequentially: sync wellness → sync activities → sync details → sync workouts.

---

## Docker

```bash
docker compose up -d db                  # PostgreSQL only
docker compose up -d                     # all (includes React build, bot via webhook in api)
docker compose run --rm api python -m cli sync-settings 2   # CLI in Docker
docker compose run --rm api python -m cli sync-wellness 2   # CLI in Docker
docker compose run --rm api python -m cli sync-activities 2     # CLI in Docker
```

Multi-stage build: Node 20 → React SPA, Python 3.12 → serves built assets. No Node in final image.
