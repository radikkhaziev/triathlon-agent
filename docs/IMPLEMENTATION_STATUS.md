# Implementation Status

> Live snapshot of what's done and what's pending across the project.
> Read this when you need historical context for a feature; the root `CLAUDE.md` keeps only the headline summary.

---

## Headline

All core modules done. Multi-tenant Phase 1.3 complete (per-user MCP auth, contextvars, scheduler). Intervals.icu OAuth Phase 2 complete (Bearer auth, lazy 401 handling, disconnect endpoint, viewer→athlete promotion + mcp_token + auto-sync, rate limit on `/auth/init`).

---

## OAuth bootstrap backfill — Phase 1+2 complete

- Chunk-recursive `actor_bootstrap_step`, `CHUNK_DAYS=30`, cursor state in `user_backfill_state`.
- Fast-path today + settings + goals + 14d-workouts + year-long slow-path.
- `GET /api/auth/backfill-status` + `POST /api/auth/retry-backfill` with dual cooldown 7d/1h + 1h anti-spam rate limit.
- CLI `bootstrap-sync`.
- `scheduler_watchdog_bootstrap` cron every 10 min with 3-kick escalation to `watchdog_exhausted`.
- Empty-import sentinel with 1h cooldown.
- `last_error` allowlist sanitization for UI.
- HRV ordering fix via inline `process_wellness_analysis_sync` in chronological loop.
- Webapp `BackfillSection` progress bar + 7-state button machine.
- See `docs/OAUTH_BOOTSTRAP_SYNC_SPEC.md`.

---

## Webhook dispatchers — 8/10 implemented

`WELLNESS`, `CALENDAR`, `SPORT_SETTINGS`, `FITNESS`, `APP_SCOPE`, `ACHIEVEMENTS`, `ACTIVITY_UPLOADED`, `ACTIVITY_UPDATED`. Skipped: `ACTIVITY_ANALYZED` (rare, re-analysis only), `ACTIVITY_DELETED`.

### Strava signature (`actor_rename_activity`)

Behind two gates: global kill-switch `STRAVA_SIGNATURE_ENABLED` (in actor) + per-user allowlist `STRAVA_SIGNATURE_USER_IDS` (CSV in env, default `{1}`, checked at dispatch in `_dispatch_activity_uploaded`). Allowlist keeps the queue clean — non-allowlisted tenants don't enqueue 5-min-delayed no-ops every upload. Renames Intervals.icu activities on `ACTIVITY_UPLOADED` with `{sport_emoji} {descriptor}` title (e.g. `🏃 Easy Run 10k`) and a 2-3 sentence AI description (Instagram-card tone) ending with `→ endurai.me`; idempotent via `_SIGNATURE_MARKERS` (`"endurai.me"`, legacy `"Readiness"`).

### Workout card PNG generator

Pillow-based renderer with GPS polyline, AI text via Claude, sport-specific metrics (Run = pace, Ride = power, Swim = pace/100m), endurai.me branding. Triggered via inline button in activity notification; sent via `sendDocument` (not `sendPhoto`) so Telegram preserves PNG transparency.

---

## User-memory facts — Phase 1 complete

Long-term traits via `user_facts` table.

- `UserFact.save_with_cap` append-with-cap — per-topic `TOPIC_CAPS` dict `injury=5` / `health=5` / default 3 + global hard cap 200, race-safe via `SELECT ... FOR UPDATE`.
- 5 MCP tools `save_fact` / `list_facts` / `deactivate_fact` / `reactivate_fact` / `get_fact_metrics` in `tracking` group.
- Two-segment cache split in `bot/prompts.py` — `get_static_system_prompt()` + `render_athlete_block(user, language, *, include_facts=True)` each with its own `cache_control: ephemeral` marker so `save_fact` invalidates only the ~240-tok tail, not the ~780-tok static prefix.
- Inline undo-button registry `_UNDOABLE_TOOLS` in `bot/main.py` — `🗑 Забудь это` after `save_fact`, `↩️ Вернуть` after `deactivate_fact`, direct MCP call from `fact_undo` callback without re-inference; TTL = next-chat-msg cleanup + 10-min `job_queue.run_once` fallback.
- Workout handlers widened filter to union with `_UNDOABLE_TOOL_NAMES` so `save_fact` inside `/workout` still surfaces undo.
- See `docs/USER_CONTEXT_SPEC.md`.

---

## Post-onboarding hey-message (issue #258)

`scheduler_onboarding_hey_job` cron hourly 09:00–21:00 picks athletes with `user_backfill_state.status = 'completed'` AND `finished_at` 24-48h ago AND `hey_message IS NULL`. `actor_send_onboarding_hey` does mark-first via `UserBackfillState.mark_hey_sent` (RETURNING-guarded) so a parallel actor cannot double-send. Text is i18n-localized in `tasks/formatter.py:build_onboarding_hey_message`.

---

## Achievement persistence

`ACTIVITY_ACHIEVEMENTS` webhook now persists `icu_achievements[]` (BEST_POWER PRs with full `point` index span) plus a synthetic `FTP_CHANGE` row when `icu_rolling_ftp_delta != 0` into the new `activity_achievements` table (migration `u1b2c3d4e5f6`). `ActivityAchievement.save_bulk` is idempotent via `UNIQUE(user_id, activity_id, achievement_id)`; raw achievement dict preserved in `extra` JSONB for forward-compat with future Intervals.icu types. Save runs BEFORE Telegram notification — outage doesn't lose data; persistence error is logged + Sentry-captured but doesn't block the realtime ping. Source of truth for the upcoming social-share UI (power PR / FTP-update lists).

---

## Webhook data capture — Phase 1

Migration `b3d4e5f6a7b8`, see `docs/WEBHOOK_DATA_CAPTURE_SPEC.md`. Three new persistence paths:

1. **`activity_weather` table** (FK to activities, populated from ACTIVITY_UPLOADED when `has_weather=True` via `ActivityWeather.upsert_from_dto`).
2. **7 new columns on `activity_details`** (`carbs_used`, `rolling_ftp`, `rolling_ftp_delta`, `rolling_w_prime`, `rolling_p_max`, `ctl_snapshot`, `atl_snapshot`) populated from ACTIVITY_ACHIEVEMENTS via the new `ActivityDetail.patch(_UNSET sentinel)` partial-upsert.
3. **4 MMP columns on `athlete_settings`** (`critical_power`, `w_prime`, `p_max`, `mmp_ftp`) populated from SPORT_SETTINGS_UPDATED.mmp_model — only Ride sport_settings carries the block.

**Skipped from spec:** `trimp` (already on `activity_details`), `achievements_json` (redundant with `activity_achievements` table). **Backfill CLI deferred** — applies on new webhooks going forward; historical rows stay NULL until a separate backfill task lands.

---

## Webhook data capture — Phase 2

Migration `c4d5e6f7a8b9`, see `docs/WEBHOOK_DATA_CAPTURE_SPEC.md` Phase 2. Three nullable columns on `activity_details` populated from ACTIVITY_UPLOADED inline:

- `warmup_time_sec` (INT) — `activity.icu_warmup_time`
- `cooldown_time_sec` (INT) — `activity.icu_cooldown_time`
- `polarization_index` (REAL) — `activity.polarization_index`

`ActivityDTO` extended with the three matching optional fields. `ActivityDetail.patch` extended with `_UNSET`-default kwargs. `_dispatch_activity_uploaded` now builds a single `upload_patch` dict (skipping `None`) for trimp + Phase 2 fields and calls `ActivityDetail.patch` once — Phase 1 trimp behavior preserved, three new fields land in the same call. Backfill deferred (spec §6 marks Phase 2 backfill as "⚠ Не срочно"); historical rows stay NULL until a separate backfill PR lands.

---

## Bot-chat gate (issue #266)

`users.bot_chat_initialized` flag tracks whether the user has actually opened a chat with the bot — Login Widget signups land with `False` because Telegram bots can't initiate chats. Set `True` in `bot/main.py:start` and `handle_my_chat_member` MEMBER transition. Read by:

- `TelegramTool._suppress` — skips send when False.
- `POST /api/intervals/auth/init` 412 gate (`{error: "bot_chat_not_initialized", bot_username}`).
- `GET /api/auth/me` for the frontend (`<BotChatBanner/>` sticky banner + `<OnboardingPrompt/>` "press /start" CTA + Settings deep-link).

Self-healing in `tasks/tools.py:_post_with_retries`: 400 with `description ∈ {chat not found, user is deactivated, peer_id_invalid}` clears the flag (guarded — only when failing chat_id matches `self.user.chat_id`, so broadcast typos can't poison the wrong row).

Sentry scrubbing extended with `bot\d+:[A-Za-z0-9_-]{30,}` regex so leaked Telegram URLs in httpx errors get redacted before they hit GitHub-issue auto-creation.

---

## Pending

- Personal patterns cron (ATP Phase 3): `compute_personal_patterns()` weekly cron + prompt enrichment. Waits for 30+ records in training_log.
- MT Phase 2 (JWT upgrade): tenant_id, role, scope claims, bot middleware (resolve_tenant). See `docs/MULTI_TENANT_SECURITY.md`.
- Retire legacy `INTERVALS_API_KEY` env vars (OAuth Phase 5).
- User-memory Phase 2 extractor — gated on `tool_facts_per_100_msgs_30d < 3` with `chat_msgs ≥ 100`.
- When scaling to multi-worker uvicorn, migrate `_retry_backfill_last_success` and `_mcp_config_last_access` to Redis INCR+EXPIRE.
