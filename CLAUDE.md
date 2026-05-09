# Triathlon AI Agent — Project Specification

> Architecture, stack, structure, and business logic.

---

## What We're Building

Personal AI agent for a triathlete: syncs wellness/HRV/training from Intervals.icu, evaluates recovery and planned workouts, sends morning reports via Telegram Bot, exposes data via MCP server, and provides an interactive dashboard via Telegram Mini App.

---

## Tech Stack

| Component         | Technology                                                            |
| ----------------- | --------------------------------------------------------------------- |
| Language          | Python 3.12+                                                          |
| Package Manager   | Poetry                                                                |
| Data Source       | Intervals.icu API                                                     |
| AI Analysis       | Anthropic Claude API (`claude-sonnet-4-6`)                            |
| Telegram Bot      | `python-telegram-bot` v21+                                            |
| Scheduler         | `APScheduler`                                                         |
| Database          | PostgreSQL 16 + `SQLAlchemy` (async) + Alembic                        |
| API Server        | `FastAPI` + `uvicorn`                                                 |
| Mini App Frontend | React 18 + TypeScript + Vite + Tailwind CSS + Chart.js                |
| Backend Hosting   | Docker Compose on VPS                                                 |
| Error Monitoring  | Sentry (`sentry-sdk[fastapi,dramatiq]`)                               |
| Config            | `pydantic-settings` + `.env`                                          |

---

## Project Structure

```
triathlon-agent/
├── config.py / sentry_config.py / cli.py
├── bot/          # Telegram bot: main.py (handlers), agent.py (ClaudeAgent), tools.py (MCPClient), prompts.py, scheduler.py
├── tasks/        # Dramatiq actors: broker.py, actors/ (wellness, activities, training_log, reports, workout)
├── data/         # Domain: metrics.py, hrv_activity.py, workout_adapter.py, ramp_tests.py, crypto.py, card_renderer.py
│   ├── intervals/  # Intervals.icu client + DTOs
│   ├── garmin/     # Garmin GDPR parser + importer
│   └── db/         # SQLAlchemy ORM (@dual sync/async), all models, decorators
├── api/          # FastAPI: server.py, auth.py, deps.py, routers/ (wellness, activities, workouts, jobs, auth)
├── mcp_server/   # 49 MCP tools + 3 resources, context.py (user_id contextvars), sentry.py
├── webapp/       # React 18 SPA (Vite + TypeScript + Tailwind)
├── migrations/ / templates/ / static/ / locale/ / docs/ / tests/
```

---

## Database Schema

33 tables. Full column specs in `data/db/`. Key tables:

**Core:** `users` (multi-tenant, chat_id, role, api_key_encrypted, mcp_token, is_active, last_donation_at, + Intervals.icu OAuth: `intervals_access_token_encrypted` / `intervals_oauth_scope` / `intervals_auth_method` — `"api_key"` | `"oauth"` | `"none"` — see `api/routers/intervals/oauth.py`), `athlete_settings` (per-sport thresholds), `athlete_goals` (race goals + CTL targets), `wellness` (daily Intervals.icu data + recovery score + AI recommendations).

**Analysis:** `hrv_analysis` (dual-algorithm baselines), `rhr_analysis` (RHR baselines, inverted), `activity_details` (zones, intervals, EF, decoupling), `activity_hrv` (DFA a1, Ra/Da), `pa_baseline` (14d rolling), `fitness_projection` (CTL/ATL/rampRate decay curve from `FITNESS_UPDATED` webhook, dates can be future), `activity_achievements` (per-activity PRs from `ACTIVITY_ACHIEVEMENTS` webhook — power PRs / FTP changes / future milestone types; raw payload preserved in `extra` JSON; UNIQUE on user+activity+achievement_id).

**Training:** `scheduled_workouts`, `activities` (incl. `is_race`/`sub_type`/`rpe` — Borg CR-10 1-10 with `CHECK` constraint), `ai_workouts`, `training_log` (pre/actual/post + compliance + `race_id` FK), `exercise_cards`, `workout_cards`, `races` (name, distance, finish/goal time, placement, surface/weather, RPE, notes, race-day CTL/ATL/TSB/HRV/recovery snapshot).

**Tracking:** `mood_checkins` (1-5 scales), `iqos_daily`, `api_usage_daily`, `star_transactions` (Telegram Stars donation ledger, `UNIQUE(charge_id)` for webhook idempotency, `refunded_at` nullable), `user_backfill_state` (1 row/user, cursor-based bootstrap progress: `oldest_dt`/`newest_dt`/`cursor_dt`/`chunks_done`/`status`+`last_error` + `hey_message` (datetime?) — post-onboarding nudge timestamp, see `docs/OAUTH_BOOTSTRAP_SYNC_SPEC.md`), `user_facts` (long-term memory: free-text traits per `topic` with `fact_language` (BCP-47), `source` (`tool`/`extractor`/`user`), `expires_at`, and soft-delete `deactivated_at`+`deactivated_reason` (`user_request`/`topic_cap`/`hard_cap`/`expired`/`contradicted`) — see `docs/USER_CONTEXT_SPEC.md`).

**Garmin (9 tables):** `garmin_sleep`, `garmin_daily_summary`, `garmin_training_readiness`, `garmin_health_status`, `garmin_training_load`, `garmin_fitness_metrics`, `garmin_race_predictions`, `garmin_bio_metrics`, `garmin_abnormal_hr_events`.

---

## Implementation Status

All core modules done. Multi-tenant Phase 1.3 + Intervals.icu OAuth Phase 2 + OAuth bootstrap backfill Phase 1+2 + Webhook data capture Phase 1+2 + User-memory facts Phase 1 + ATP Phase 3 personal-patterns prompt enrichment complete. HRV collapsed to single algorithm (Flatt/Esco) in #307 — AIEndurance retired. Ramp-test protocols rebuilt 2026-05-08 against `docs/RAMP_TEST_BIKE_SPEC.md`: Run pace-driven 8-step `80→115%`, Bike power-driven 11+1 step `60→110% + 1×120%` push-to-failure (each calibrated against pace/pow at HRVT2). Both builders return `(steps, warnings)` with default fallbacks (Run 295 s/km, Bike 200W) when sport-settings missing. Phase-aware test cadence (`tasks/utils.py:RampTrainingSuggestion`): peak/taper (≤14d to nearest race) suppress, base (≤56d) 8w cadence, build (>56d) 6w cadence, no goal 30d default — multi-goal aware (nearest race wins, not RACE_A first). Drift detection: absolute per-metric gates (`DRIFT_LTHR_BPM=3`, `DRIFT_PACE_SEC_PER_KM=5`, `DRIFT_FTP_WATTS=5`) replace 5% relative; R² 3-tier (`DRIFT_R2_HIGH=0.85` → auto-fire `actor_update_zones`, medium → button, low → soft hint). `actor_update_zones` pushes HRVT2 (anaerobic) into Intervals' `lthr`/`threshold_pace`/`ftp` (Ride only for FTP — issue #313, 2026-05-08; prior HRVT1→`lthr` was mis-aligning every Intervals zone by ~13%). DFA detector gained slope-sign sanity check + power-bound WARN logging + per-threshold confidence (`hrvt1_confidence`/`hrvt2_confidence` columns combine `n_local` ±0.15 around α1 crossing × global R²) — see `docs/DFA_REGRESSION_METHODOLOGY_SPEC.md` for the deferred sigmoid-fit + per-step steady-state averaging. `get_zones` MCP tool reshape (issue #313): sport-tagged keys, dual-unit zone objects (raw % + absolute watts/sec). Webhook dispatchers 8/10 implemented. **Race-goal cleanup (issue #323, 2026-05-09):** dropped orphan `disciplines` JSON column from `athlete_goals`; race-goal sport_type enum (`triathlon`/`duathlon`/`aquathlon`/`run`/`ride`/`swim`/`fitness`) lives in `data/sport_map.py:RACE_SPORT_TYPES` with `resolve_race_sport_type` resolver — wired into `actor_sync_athlete_goals` + `suggest_race` (no more hardcoded `"triathlon"` from Intervals webhooks). User-editable via Settings dropdown (`PATCH /api/athlete/goal/{id}` + `sport_type` field). Settings page now shows ALL active future goals (`GET /api/athlete/goals`, `require_viewer` so demo can browse) — was single-anchor before. Prompt templates surface RACE_A + nearest race (helper `AthleteGoal.get_goals_for_prompt` returns 0/1/2 DTOs) with `Goals:` block + sport_type, replacing the legacy single-line `Goal:`. **Pending:** retire legacy `INTERVALS_API_KEY`, user-memory Phase 2 extractor, DFA H1+H2 (sigmoid + per-step averaging). MT Phase 2 (auth upgrade) и Phase 3 (security hardening) — deferred с зафиксированным audit + punch-list в `docs/MULTI_TENANT_SECURITY_SPEC.md` §9; триггеры для перезапуска описаны там же.

> Full feature-by-feature changelog: **`docs/IMPLEMENTATION_STATUS.md`**.

**Key patterns:** ORM uses `@dual` (auto sync/async dispatch), `@with_session`/`@with_sync_session`. `AthleteSettings.get_thresholds()` + `AthleteGoal.get_goal_dto()`. MCP tools use `get_current_user_id()` from contextvars. Sentry with `@sentry_tool` for MCP. Bot decorators: `@athlete_required` (needs `athlete_id`), `@user_required` (any active user — for `/lang`, `/silent`, `/donate`). API DTOs in `api/dto.py`.

---

## Environment Variables (.env)

See `.env.example` for full list. Key vars: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_BOT_USERNAME` (for Login Widget), `TELEGRAM_WEBHOOK_URL` (empty=polling), `ANTHROPIC_API_KEY`, `DATABASE_URL`, `REDIS_URL`, `API_BASE_URL` (single URL for API + webapp + static + CORS origin), `INTERVALS_API_KEY`/`INTERVALS_ATHLETE_ID` (legacy owner, being replaced by per-user OAuth), `INTERVALS_OAUTH_CLIENT_ID`/`INTERVALS_OAUTH_CLIENT_SECRET`/`INTERVALS_OAUTH_REDIRECT_URI` (per-user OAuth), `INTERVALS_WEBHOOK_SECRET` (shared secret for webhook verification), `TIMEZONE=Europe/Belgrade`, `MCP_AUTH_TOKEN`, `FIELD_ENCRYPTION_KEY` (Fernet), `DEMO_PASSWORD` (shared password for read-only demo access, empty=disabled), `SENTRY_DSN` (empty=disabled).

**Telegram Login Widget setup** (one-time, for web login): in `@BotFather` run `/setdomain` → choose your bot → enter `bot.endurai.me` (no protocol, no path). Widget will only render on that domain. Set `TELEGRAM_BOT_USERNAME` in `.env` to the bot username (without `@`). See `api/auth.py:verify_telegram_widget_auth` for the HMAC-SHA256 verification logic (`docs/MULTI_TENANT_SECURITY_SPEC.md` threat T3 scope).

---

## Business Rules & Thresholds

> Full implementations in `data/metrics.py`.

**CTL/ATL/TSB** — All values from Intervals.icu API (τ_CTL=42d, τ_ATL=7d). NOT recalculated. Thresholds calibrated for Intervals.icu, not TrainingPeaks.
TSB zones: >+10 under-training | -10..+10 optimal | -10..-25 productive overreach | <-25 overtraining risk.

**HRV — Flatt & Esco** baseline (today's RMSSD vs 7d mean, asymmetric bounds −1/+0.5 SD, fast response). Status: green (full load) / yellow (monitor) / red (reduce) / insufficient_data (<14 days). The AIEndurance algorithm was retired in #307 — historical `algorithm='ai_endurance'` rows in `hrv_analysis` are preserved but never read; `algorithm` column kept in PK so the schema stays addressable.

**RHR** — Inverted vs HRV: elevated RHR = red. Bounds: ±0.5 SD of 30d mean.

**Recovery Score (0-100)** — Weights: RMSSD 35%, Banister 25%, RHR 20%, Sleep 20%.
Categories: excellent >85, good 70-85, moderate 40-70, low <40.
Recommendations: zone2_ok / zone1_long / zone1_short / skip.

**Cardiac Drift (Decoupling)** — Pa:Hr from Intervals.icu, not recalculated.
Filter: `is_valid_for_decoupling()` — VI <= 1.10, >70% Z1+Z2, bike >= 60min / run >= 45min, swim excluded.
Traffic light: green (<5%) / yellow (5-10%) / red (>10%). Uses abs() for negative drift.
Trend: last-5 median via `get_efficiency_trend(strict_filter=True)`. Theory: `docs/knowledge/decoupling.md`.

**HR / Power / Pace Zones** — synced from Intervals.icu sport-settings into `athlete_settings.{hr,power,pace}_zones` (source of truth). Zone count varies per user (typically 5-7 zones). **Units contract** (see `data/db/athlete.py:33`): `hr_zones` are absolute bpm, `power_zones` are **%FTP** (not watts — Intervals stores them pre-normalized), `pace_zones` are %threshold where 100.0 = threshold. Top zone opens upward, often stored with a `999` sentinel.

Two independent consumers read these zones, each with its own fallback:
- **`get_zones` MCP tool** (`mcp_server/tools/zones.py`) — sport-tagged response (`hr_zones_bike` / `hr_zones_run` / `hr_zones_swim`, `power_zones_bike` / `power_zones_run`, `pace_zones_run` / `pace_zones_swim`). Power and pace zone objects carry **dual units**: raw `min_pct/max_pct` (the values stored in DB) **and** absolute `min_w/max_w` (or `min_sec_per_km`/`min_sec_per_100m`). Sentinel boundary `999` collapses to «no upper bound». Fallbacks: Run 7-zone Z1 0-84%…Z7 106%+, Bike 5-zone Z1 0-68%…Z5 105-120%.
- **`get_system_prompt_chat`** (`bot/prompts.py`) — renders a per-user `{zones_block}` straight into `SYSTEM_PROMPT_CHAT` so workout generation uses the athlete's own zones rather than a hardcoded model. Treats `power_zones` / `pace_zones` as percentages directly (no dual-unit transform — Claude works fine with %). Fallbacks (Friel 5-zone): Run `_FALLBACK_RUN_HR_PCT` Z1 0-72%…Z5 92-100%, Bike HR `_FALLBACK_BIKE_HR_PCT` Z1 0-68%…Z5 105-120%, Ride power `_FALLBACK_RIDE_POWER_PCT` Z1 0-55%…Z5 105-120%. Each rendered branch always emits a concrete Example Z2 JSON step so Claude never invents the target shape.

---

## AI Architecture

### MCP as Unified Data Layer

All AI tool calls go through MCP server via HTTP — no direct DB access from AI code.

```
Telegram text → ClaudeAgent (bot/agent.py)
  → MCPClient.list_tools() → HTTP /mcp tools/list (Streamable HTTP, SSE)
  → Claude API (claude-sonnet-4-6, tools from MCP)
  → tool_use? → MCPClient.call_tool() → HTTP /mcp tools/call
  → final text → Telegram
```

**ClaudeAgent** (`bot/agent.py`): thin async client. No business logic. Per-user `mcp_token` passed to `MCPClient` per call.

**MCPClient** (`bot/tools.py`): async MCP Streamable HTTP client. Tool list cached at class level. Session per-instance (per-token).

**MCPTool** (`tasks/tools.py`): sync MCP client for dramatiq actors (morning report generation).

### Morning Report (via Dramatiq)

Generated by `actor_compose_user_morning_report` → `MCPTool.generate_morning_report_via_mcp()` → sync Claude API + MCP tool loop → saves `ai_recommendation` to wellness row.

### Telegram Chat

Stateless. Each message: `agent.chat(text, mcp_token=user.mcp_token)` → Claude + MCP tools → response. Reply context included when replying to a message.

**Distance-based workouts:** `WorkoutStep` supports `distance` (meters) as alternative to `duration` (seconds). Mutually exclusive. `target: "PACE"` set for Swim/Run.

**Intensity target mandate:** `PlannedWorkoutDTO._check_steps_have_targets` rejects any terminal (non-repeat-group) step without `hr` / `power` / `pace`. Garmin/Wahoo watches only alert on the target corridor when a numeric target is present, so text-only steps (`"Z2" label + duration`) are forbidden. **Exception:** sport `Other` (yoga, stretching, mobility) skips this validation — watches don't need intensity targets for these activities. Per-sport convention: Run → `hr` with `%lthr` units, Ride → `power` with `%ftp`, Swim → `pace` with `%pace`. Use `value` (low) + `end` (high) for a corridor. The `suggest_workout` MCP tool docstring and `SYSTEM_PROMPT_CHAT` (workout-generation section) both enforce this contract — the validator is the backstop if the model forgets.

**Strava source filter:** Intervals.icu returns 422 `Cannot read Strava activities via the API` for `source == STRAVA` activities (licensing). `actor_fetch_user_activities` drops them **before** `Activity.save_bulk` so they never enter the DB or trigger downstream pipelines. `ActivityDTO.source` carries `GARMIN_CONNECT` / `OAUTH_CLIENT` / `STRAVA` / etc. from Intervals.icu.

---

## Operations

> Bot commands, API endpoints, webapp routes, CLI, migrations, onboarding, Docker — full reference in **`docs/OPERATIONS.md`**.

**Quick orientation:**

- **Bot commands** (`bot/main.py`) — `/start`, `/dashboard`, `/workout`, `/race`, `/web`, `/donate`, `/lang`, `/silent`, `/whoami`, `/health` (owner), `/stick` (owner). Free-form `<text>`/`<photo>` go to AI chat. Decorators: `@athlete_required` vs `@user_required`.
- **API** (`api/routers/`) — `/api/report`, `/api/wellness-day`, `/api/scheduled-workouts`, `/api/activities-week`, `/api/activity/{id}/details`, `/api/progress`, `/api/polarization`, `/api/fitness-projection`, dashboard routes, `/api/auth/*`, `/api/intervals/{auth,webhook}`, `/api/jobs/*`, `/health`, `/mcp`. Auth: Telegram initData or `Bearer <jwt>`; deps `require_viewer` / `require_athlete` / `require_owner`.
- **Webapp** (`webapp/`) — React 18 SPA, routes `/wellness` (home), `/plan`, `/activities`, `/activity/:id`, `/dashboard`, `/progress`, `/settings`, `/login`. Global auth gate: no `athlete_id` → `<OnboardingPrompt/>`.
- **CLI** (`cli.py`) — `shell`, `sync-{settings,wellness,activities,training-log}`, `import-garmin`, `backfill-races`, `bootstrap-sync`, `broadcast-migration`. Period formats: `2025Q4` / `2025-11` / `2025-01-01:2025-03-31`.
- **Migrations** — `alembic upgrade head`, `alembic revision --autogenerate -m "..."`. Auto-applied on deploy via `migrate` compose service.
- **Onboarding** — default path is automatic OAuth (user `/start` → connect Intervals.icu → fast-path + slow-path bootstrap). Manual CLI path exists for legacy/admin use.
- **Docker** — `docker compose up -d` (full stack); `docker compose run --rm api python -m cli ...` for CLI in container.

**Two-phase mutation flows:** `/workout` and free-form race creation use a **dry-run preview → inline-button confirm** pattern. The handler stores Claude's `tool_use` block from the first call and replays it directly via `MCPClient.call_tool` on confirm — no re-inference, bit-for-bit identical to preview, prevents prompt-injection on the state-mutating step. See `bot/main.py:_PREVIEWABLE_TOOLS` and the relevant section of `docs/OPERATIONS.md`.

---

## Key Implementation Notes

- **Intervals.icu API** — wellness every 10 min (4-8h) then every 30 min (9-22h), workouts hourly at :00 (4-23h), activities every 10 min (4-23h), DFA every 5 min (5-22h), evening report Mon–Sat 19:00 (`misfire_grace_time=3600, coalesce=True` — Sunday slot taken by weekly), weekly report Sunday 19:00 (`misfire_grace_time=7200, coalesce=True`, replaces Sunday evening report — contains the weekly summary + next week's plan), progression-model retrain Sunday 16:00 (`misfire_grace_time=7200, coalesce=True`). Misfire grace covers restart/deploy within the cron-tick window — without it APScheduler's default `misfire_grace_time=1` silently drops the user-facing report
- **HRV** uses Flatt & Esco baseline (single algo since #307 retired AIEndurance)
- **Claude API** once per day to minimize costs (morning report). Chat uses per-request calls. Prompt caching: **two `cache_control: ephemeral` segments** — `get_static_system_prompt()` (instructions, never changes) and `render_athlete_block(...)` (today + profile + goal + zones + facts + language). `save_fact` / goal update invalidates only the ~240-tok tail; the ~780-tok static prefix stays hot on Anthropic's prefix cache (see USER_CONTEXT_SPEC §6). Tool filtering: 6 groups, keyword-based, core+tracking+workouts always included (~75% token reduction for simple messages)
- **All timestamps** UTC in DB, local timezone for display. "Today" in actors and formatter functions always goes through `tasks.dto.local_today()` (Belgrade tz from `settings.TIMEZONE`), **not** `date.today()` (the container drifts to UTC if `TZ` env is unset). The api/worker containers export `TZ=${TIMEZONE:-Europe/Belgrade}` plus the `tzdata` package in the Dockerfile, so `date.today()` is also Belgrade — but `local_today()` remains the canonical choice for new code.
- **Telegram bot** — polling (local dev, `TELEGRAM_WEBHOOK_URL` empty) or webhook (production)
- **Frontend** — React SPA via Vite; dev proxies /api to FastAPI; production serves from webapp/dist/
- **i18n** — Backend: gettext (contextvars `_()`, `locale/` .po/.mo). Frontend: react-i18next (`webapp/src/i18n/` .json). User.language field, `"Respond in {response_language}"` in Claude prompts
- **Task queue** — Dramatiq + Redis. Scheduler dispatches groups per-user. Jobs endpoints dispatch directly. Actor time limits (30 min for FIT processing). `--force` flag for re-processing unchanged data
- **ORM** — `@dual` decorator creates `DualMethod` descriptor: auto-dispatches sync/async by detecting event loop. One method name works in both contexts: `Activity.get_for_date()` (sync) and `await Activity.get_for_date()` (async)
- **DTOs** — organized by domain: `data/dto.py` (metrics), `data/db/dto.py` (DB models), `data/intervals/dto.py` (API), `tasks/dto.py` (processing)
- **Sentry** — single init via `sentry_config.py`, called from `tasks/broker.py` (workers), `api/server.py` (API), `bot/main.py` (polling). Empty `SENTRY_DSN` = disabled. Data scrubbing: request headers/body, breadcrumbs, stackframe local vars. `@sentry_tool` decorator for MCP tools with spans. Intervals.icu client has spans + retry breadcrumbs

### Telegram Bot — Webhook Lifecycle

Startup: `initialize()` → `post_init()` (scheduler + Redis) → `start()` → `set_webhook()`.
Shutdown: `delete_webhook()` → `stop()` → `shutdown()` → `post_shutdown()`.
Auth: `X-Telegram-Bot-Api-Secret-Token` header (SHA256 of bot token, first 32 hex).

### Multi-Tenant Data Flow

```
Wellness cron → actor_user_wellness (per-user) → auto-fires
  → actor_compose_user_morning_report.send(user=UserDTO)
  → Dramatiq actor (sync) → MCPTool (sync HTTP to /mcp)
  → MCPAuthMiddleware → User.get_by_mcp_token → set_current_user_id
  → MCP tools → get_current_user_id() → user-scoped queries
```

---

## MCP Server (58 tools + 3 resources)

Run: `python -m mcp_server`. Production: mounted at `/mcp` (Streamable HTTP, per-user Bearer auth via `User.mcp_token`).

**Auth:** `MCPAuthMiddleware` resolves user by `User.get_by_mcp_token(token)` → sets `user_id` in `contextvars`. All tools call `get_current_user_id()` — user cannot manipulate `user_id` via tool parameters.

**58 tools** covering: wellness, HRV/RHR analysis, activities, training load/recovery, workouts (suggest/adapt/remove), training log, exercise/workout cards, mood/IQOS tracking, Garmin data (6 tools), efficiency trends, polarization index, goal progress, zones, races (`get_races`/`tag_race`/`update_race`/`suggest_race` for future-race creation with dry-run preview/`delete_race_goal` for removal), **long-term user memory** (`save_fact`/`list_facts`/`deactivate_fact`/`reactivate_fact`/`get_fact_metrics` — see `docs/USER_CONTEXT_SPEC.md`), GitHub issues (`create_github_issue` available to athletes, sliding-window cap 5/24h per user, attribution in body — `user_id` only, no `@username`/`athlete_id`, `title ≤ 200` / `body ≤ 8000` cap; see `docs/MULTI_TENANT_SECURITY_SPEC.md` §13), API usage. **3 resources:** `athlete://profile`, `athlete://goal`, `athlete://thresholds`.

**Key constraint:** CTL/ATL/TSB come from Intervals.icu, not TrainingPeaks.

---

## Mood, IQOS & Long-term Memory

**Mood:** Via MCP only. Claude notices emotional context → `save_mood_checkin_tool`. Scales 1-5: energy, mood, anxiety, social + note. Transient — one check-in per moment.
**IQOS:** `/stick` command increments daily counter. MCP tool `get_iqos_sticks(target_date, days_back)` for trends.
**Long-term memory (`user_facts`):** Claude calls `save_fact(topic, fact, expires_at?)` when the athlete reveals a LASTING trait (injury, schedule, family, preference, equipment, travel, job, health — something still relevant in 2+ weeks). Active facts are injected into the system prompt via `render_athlete_block`. Undo: each mutation ships with an inline button (`🗑 Забудь это` / `↩️ Вернуть`) that invokes the compensating MCP tool (`deactivate_fact` / `reactivate_fact`) directly without re-inference; TTL is next-message cleanup + 10-min `job_queue.run_once` fallback. Phase 2 async extractor is gated on `get_fact_metrics().tool_facts_per_100_msgs_30d < 3` with `chat_msgs ≥ 100`. Full spec: `docs/USER_CONTEXT_SPEC.md`.

---

## Intervals.icu Auth — Dual Mode (Phase 1 of OAuth migration)

Per-user Intervals.icu credentials support **two** authentication methods, tracked by `users.intervals_auth_method`:

| method | Credential storage | Who uses it |
|---|---|---|
| `"api_key"` | `users.api_key_encrypted` (Fernet) | Legacy — existing athletes, owner |
| `"oauth"` | `users.intervals_access_token_encrypted` (Fernet) + `intervals_oauth_scope` | New/migrated users via OAuth flow |
| `"none"` | — | Revoked OAuth with no api_key fallback (user must reconnect) |

**OAuth flow** (`api/routers/intervals/oauth.py`): frontend XHR `POST /api/intervals/auth/init` (auth header attached by `apiFetch`) → signed JWT state (`purpose='intervals_oauth'`, 15-min TTL) → returns `{authorize_url}` → `window.location.assign(authorize_url)` → `intervals.icu/oauth/authorize` → consent → `GET /api/intervals/auth/callback?code=&state=` (validates state, no auth header needed) → server-side POST to `intervals.icu/api/oauth/token` → response has `{access_token, token_type: "Bearer", scope, athlete: {id, name}}` (**no** refresh_token, **no** expires_in) → `User.set_oauth_tokens()` → 302 redirect to `/settings?connected=intervals`. Why init is POST and not GET: a full-page `<a href>` doesn't send the Authorization header from localStorage, so a GET endpoint with `require_viewer` would 401. POST+XHR+JSON sidesteps that.

**Scopes:** `ACTIVITY:WRITE,WELLNESS:READ,CALENDAR:WRITE,SETTINGS:WRITE` — `:WRITE` implies `:READ` per Intervals.icu docs, and listing the same area twice produces `"Duplicate scope"` error. `ACTIVITY:WRITE` for rename/update, `SETTINGS:WRITE` for `actor_update_zones` (ramp-test LTHR + Run threshold_pace push — pace converted from sec/km in our DB to m/s for the API).

**Phase 2 complete:** `IntervalsClient` (`data/intervals/client.py`) now supports dual auth — `_resolve_credentials(user)` reads `User.intervals_auth_method` and picks Bearer (`access_token`) or Basic (`api_key`). Constructor is keyword-only (`*, athlete_id, api_key=None, access_token=None`) to prevent positional arg swap. Both `for_user()` factories (async + sync) delegate to `_resolve_credentials`. Empty `athlete_id` → `LookupError` at resolve time. Verified end-to-end on real Intervals.icu API.

**Webhook receiver** (`POST /api/intervals/webhook`): verifies `body.secret` via `hmac.compare_digest`, resolves tenant by `athlete_id`, parses records into typed DTOs for drift detection (errors go to app logs, not Sentry). 5 delivery patterns documented: `records[]`, `activity`, `sportSettings[]`, top-level fields, empty notification. See `docs/INTERVALS_WEBHOOKS_RESEARCH.md` for full payload samples (10/10 event types researched).

**Onboarding routing:** `bot/main.py:start` branches on `user.athlete_id` — new users get "🔗 Подключить Intervals.icu" WebApp button → `/settings`. `webapp/src/pages/Login.tsx:routeAfterLogin` sends users without `athlete_id` to `/settings`. Global auth gate in `App.tsx` blocks all data routes for unauthenticated users or users without `athlete_id` (issue #185 fix).

---

## Documentation

Specs and plans in `docs/`. Key references:

- **`IMPLEMENTATION_STATUS.md`** — feature-by-feature changelog, what's done / pending.
- **`OPERATIONS.md`** — bot commands, API endpoints, webapp routes, CLI, migrations, onboarding, Docker.
- **`ADAPTIVE_TRAINING_PLAN_SPEC.md`**, **`MULTI_TENANT_SECURITY_SPEC.md`**, **`INTERVALS_WEBHOOKS_RESEARCH.md`** (10 event-type payload samples), **`OAUTH_BOOTSTRAP_SYNC_SPEC.md`**, **`USER_CONTEXT_SPEC.md`**, **`WEBHOOK_DATA_CAPTURE_SPEC.md`**, **`RACE_PLAN_SPEC.md`**, **`TRAINING_PROGRESSION_SPEC.md`**, **`ML_HRV_PREDICTION_SPEC.md`**, **`ML_RACE_PROJECTION_SPEC.md`** — feature specs.
- **`intervals_icu_openapi.json`** — Intervals.icu API reference. **`knowledge/`** — training methodology.

---

## Next Steps

1. **Webhook dispatchers** — all done: `WELLNESS_UPDATED` ✓, `CALENDAR_UPDATED` ✓, `SPORT_SETTINGS_UPDATED` ✓, `FITNESS_UPDATED` ✓, `APP_SCOPE_CHANGED` ✓, `ACTIVITY_ACHIEVEMENTS` ✓, `ACTIVITY_UPLOADED` ✓, `ACTIVITY_UPDATED` ✓. Skipped: `ACTIVITY_ANALYZED` (rare, re-analysis only), `ACTIVITY_DELETED`.
2. **OAuth** — ✅ disconnect endpoint, ✅ lazy 401 handling, ✅ bootstrap Phase 1+2 (watchdog cron, retry endpoint, HRV ordering fix, progress UI, last_error allowlist). Remaining: retire legacy `INTERVALS_API_KEY` env vars (Phase 5). When scaling to multi-worker uvicorn, migrate `_retry_backfill_last_success` and `_mcp_config_last_access` to Redis INCR+EXPIRE
3. **Multi-Tenant Phase 2** — JWT upgrade (tenant_id, role, scope claims), bot middleware (resolve_tenant). See `docs/MULTI_TENANT_SECURITY_SPEC.md`

---

## Contributing

- Follow existing module structure
- DTOs: `api/dto.py` (API request/response), `data/dto.py` (metrics), `data/db/dto.py` (DB), `data/intervals/dto.py` (Intervals.icu API), `tasks/dto.py` (processing)
- ORM methods: use `@with_session` (async), `@with_sync_session` (sync), or `@dual` (both). `user_id` always first param after `cls`
- New MCP tools: add to `mcp_server/tools/`, use `get_current_user_id()` from `mcp_server.context`, never accept `user_id` as tool parameter
- New data tools: add only to MCP, not to `TOOL_HANDLERS` (deprecated)
- Write deterministic tests for metric calculations
- Keep prompts in `bot/prompts.py`
- i18n: wrap user-facing bot strings in `_()` from `bot.i18n`. Add translations to `locale/en/LC_MESSAGES/messages.po`, run `pybabel compile -d locale`. Webapp: add keys to `webapp/src/i18n/ru.json` + `en.json`
- Document new env vars in `.env.example`
- When closing GitHub issues, follow the workflow in `~/.claude/skills/github-workflow/SKILL.md` — add a closing comment with "What was done" + "How to verify" before closing
