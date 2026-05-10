# Implementation Status

> Live snapshot of what's done and what's pending across the project.
> Read this when you need historical context for a feature; the root `CLAUDE.md` keeps only the headline summary.

---

## Headline

All core modules done. Multi-tenant Phase 1.3 complete (per-user MCP auth, contextvars, scheduler). Intervals.icu OAuth Phase 2 complete (Bearer auth, lazy 401 handling, disconnect endpoint, viewer→athlete promotion + mcp_token + auto-sync, rate limit on `/auth/init`). ATP Phase 3 prompt enrichment complete (no cron deviation, see below). Ramp-test protocols rebuilt 2026-05-08 against `docs/RAMP_TEST_BIKE_SPEC.md`: Run pace-driven 8-step `80→115%`, Bike power-driven 11+1 step `60→110% + 1×120%` push-to-failure. Phase-aware test cadence (peak/taper/base/build cadence varies by nearest race). Drift detection: HRVT2 → Intervals' `lthr`/`threshold_pace`/`ftp` (was HRVT1 — concept bug, FTP added 2026-05-08 per issue #313), absolute-unit gates (3 bpm / 5 s/km / 5 W) + R² 3-tier (high → auto-fire, medium → button, low → soft hint). DFA detector: slope-sign sanity check, power-bound WARN logging, per-threshold confidence (n_local × R²) — see `docs/DFA_REGRESSION_METHODOLOGY_SPEC.md` for deferred sigmoid rewrite. `get_zones` MCP tool reshape: sport-tagged keys, dual-unit zone objects (issue #313). New CLI: `reprocess-ramp-test` for back-filling `hrvt2_pace`/`hrvt2_power` after migrations `v2c3d4e5f6a7` / `w3d4e5f6a7b8`. New schema: `x4e5f6a7b8c9` adds `hrvt1_confidence`/`hrvt2_confidence`.

---

## Race-goal cleanup (issue #323) — all 4 strands complete (2026-05-09)

End-to-end cleanup of `athlete_goals` after the table accumulated a mix of orphan fields (`disciplines` JSON column, never read), hardcoded defaults (`sport_type="triathlon"` set unconditionally on Intervals webhook sync), and a single-anchor UX where Settings showed only one goal even though athletes routinely have multiple A/B/C in a season.

### Strand A — Backend cleanup

- **Schema:** migration `z6a7b8c9d0e1` drops `athlete_goals.disciplines` column (`batch_alter_table` + reversible downgrade). Round-trip up/down/up clean.
- **Helper:** `data/sport_map.py:resolve_race_sport_type(raw)` + `RACE_SPORT_TYPES` frozenset — race-goal sport_type enum (`triathlon`/`duathlon`/`aquathlon`/`run`/`ride`/`swim`/`fitness`). Distinct from the activity-canonical `INTERVALS_TO_LOWER` map (`Ride`/`Run`/`Swim` only) because race goals can be multi-sport.
- **`AthleteGoal.upsert_from_intervals` + `suggest_race`:** both now require `sport_type: str` kwarg; resolved via `resolve_race_sport_type(event.type)` (Intervals webhook path) or `resolve_race_sport_type(sport)` (Claude `suggest_race` path). On the **update** branch, `sport_type` is intentionally NOT overwritten — user-edits via Settings (Strand B) win, Intervals re-sync logs an `info` divergence note. No data migration: existing `sport_type="triathlon"` rows stay; user fixes via Settings.
- **DTO:** `AthleteGoalDTO.disciplines` field removed; `category: str | None = None` added (Strand C).
- **`_to_dto`:** module-level helper consolidating ORM → DTO mapping. One edit point for new columns.

### Strand B — Edit `sport_type` via Settings

- **PATCH endpoint:** `AthleteGoalPatchRequest.sport_type` Pydantic Literal (server-validated against the 7 enum values). Router rejects explicit `null` with HTTP 400 (schema is NOT NULL; only field-absence leaves the column untouched). `update_local_fields` extends with `sport_type: str = _UNSET` sentinel param + ORM-layer `RACE_SPORT_TYPES` enum guard (defense-in-depth for CLI/direct callers).
- **Frontend:** dropdown `<select>` between Date and CTL Target rows in each goal card. Optimistic update + monotonic-seq rollback (existing pattern). RU/EN i18n: `settings.goal.sport_type` + `settings.goal.sport_type_options.{triathlon,duathlon,aquathlon,run,ride,swim,fitness}`.
- **Pydantic-Literal ↔ frozenset drift guard:** `tests/test_sport_map.py:test_pydantic_literal_matches_resolver_enum` introspects `AthleteGoalPatchRequest.model_fields["sport_type"]` and asserts the Literal args equal `RACE_SPORT_TYPES`. Catches the case where someone adds a new sport_type but forgets one of the two Python sources.

### Strand C — List ALL goals in Settings

- **New endpoint:** `GET /api/athlete/goals` → `{"goals": [{id, category, event_name, event_date, sport_type, ctl_target, per_sport_targets}, ...]}`. Sorted by `event_date ASC` (nearest race first). Auth: `require_viewer` (read-only OK for demo session — they see owner's goals on the read-only tour). PATCH on the same router stays on `require_athlete` (write blocked for demo).
- **ORM helper:** `AthleteGoal.get_goals_for_settings(user_id, today)` — returns ALL active future goals, no max-2 cap (different from `get_goals_for_prompt`). Past races filtered out (not editable).
- **Frontend:** Settings page now maps over `goals: AthleteGoal[]` and renders one card per goal. Each card has its own `patchGoal(goalId, patch)` invocation; rollback restores the entire array snapshot. Category badge `RACE_A/B/C` rendered in accent color above each event name; localized via `settings.goal.category.*` i18n keys.
- **`auth_me.goal`:** kept for legacy callers (still single-anchor for `Dashboard.tsx`'s `has_goal` gate). Settings page no longer reads it — separated into its own fetch.
- **TestClient regression guard:** `test_get_endpoint_uses_require_viewer_not_require_athlete` wires the route through FastAPI `dependency_overrides` so a future revert to `require_athlete` would fail-fast.

### Strand D — RACE_A + nearest race in Claude's prompts

- **Helper:** `AthleteGoal.get_goals_for_prompt(user_id, today)` returns 0/1/2 DTOs:
  - **0** — no future races. Render «Goal: не задана».
  - **1** — only one future race, OR RACE_A IS the nearest race. Single-line.
  - **2** — RACE_A exists AND nearest is a different event (typically a B/C tune-up before the season A). RACE_A first, nearest second.
- **Prompt templates:** `SYSTEM_PROMPT_V2` (morning), `SYSTEM_PROMPT_WEEKLY`, `_ATHLETE_BLOCK_TEMPLATE` (chat) — replaced single `Goal: {event} ({date})` line with `{goals_block}` placeholder rendered via `_render_goals_block(goals)`. Two-goal shape includes a focus-hint: «Goals (focus on RACE_A; mention nearest only if directly relevant to today)».
- **MCP resource:** `athlete://goal` updated to render both events (with `RACE_A:` / `Nearest:` labels) when 2 goals returned, single block when 1.
- **Token cost:** ~+30 tokens in `dynamic_tail` cache segment for two-goal case. No effect on prefix-cache hits (segment-tail invalidation only).

### Tests

- `tests/test_sport_map.py` — 30 tests on resolver: canonical enum, capitalization, Intervals aliases, empty/None, unknown→fitness, output-in-enum invariant, Pydantic-Literal drift guard.
- `tests/db/test_athlete_goal.py` — 16 tests across 3 classes: `TestUpsertFromIntervalsSportType` (insert/update/no-stomp), `TestGetGoalsForPrompt` (0/1/2 shapes + per-user scoping), `TestGetGoalsForSettings` (all-active, sort, past-filtered, dto-carries-category, per-user).
- `tests/api/test_athlete_goal.py` — 21 tests (PATCH endpoint + ORM `update_local_fields`): partial update, explicit-null clear, sport_type set/null/literal-rejected/combined, ORM enum guard.
- `tests/api/test_athlete_goals_list.py` — 6 tests (GET endpoint): empty, single, multi-preserve-order, resolution helper, demo-role-read, TestClient `require_viewer` wiring guard.
- `tests/bot/test_render_goals_block.py` — 5 unit tests on the renderer (no DB / async): empty, single-line, two-goal block with focus-hint, sport_type rendering.
- `tests/bot/test_prompts_zones.py` — bulk-replaced 18 patches `get_goal_dto` → `get_goals_for_prompt` (semantic equivalence verified).

**Total:** ~25 files, +780/−340 lines, 170 tests passing.

### Decisions log

- **No data migration for existing `sport_type="triathlon"`** — user fixes via Settings dropdown (Strand B). Heuristic re-resolve from Intervals would be brittle (most events have `type="Run"` etc., even for triathlon races where Intervals lacks a multi-sport activity type).
- **`require_viewer` not `require_athlete` on GET** — demo's read-only tour needs to see owner's goals. PATCH stays on `require_athlete`.
- **Two-goal cap in `get_goals_for_prompt`** — three+ goals would bloat the system prompt. RACE_A + nearest covers «strategic anchor + tactical context» without chatter.
- **Pydantic Literal + frozenset duplication accepted** — codegen across Python/TS/JSON would be heavyweight; the drift-guard test is cheap and effective for the Python side.

### Known follow-ups (not blocking)

- M2: per-sport CTL inputs render unconditionally regardless of `sport_type` (e.g. swim/bike inputs visible for `sport_type="run"` goal). UX polish.
- M3: shared `patchSeq` across all goal cards — failed late PATCH on goal A could roll back goal B's optimistic update via the snapshot. Per-goal seq map would fix.
- M4: frontend doesn't re-sort `goals` (trusts server `event_date ASC`). Defensive sort guard cheap.
- L2: empty `goals.length === 0` hides the entire section — no «Add goal» CTA. Chat hint at the bottom still says «use /race in the bot».
- L3: race-goal sport_type enum exists in 5 places (Python frozenset, Pydantic Literal, TS union, dropdown options array, prompt rendering — last one indirect). Drift-guard tests cover Python side.
- L5: `auth_me.goal.sport_type` is forward-compat (webapp doesn't read it from there).
- H1: pre-existing — `Dashboard.tsx` still reads `auth_me.goal` for `has_goal` gate. Cleanup before fully retiring legacy single-goal field on `auth_me`.

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

## HRV — single-algorithm collapse (issue #307)

Retired the AIEndurance HRV baseline. Code path collapses to a single algorithm — Flatt & Esco.

- `data/metrics.py:rmssd_ai_endurance` deleted.
- `config.HRV_ALGORITHM` env var + `.env.example` line removed (no longer choosing).
- `_actor_calculate_hrv` returns a single `RmssdStatusDTO` (was `dict[Literal["flatt_esco","ai_endurance"], ...]`); `_actor_update_hrv_analysis` writes only the `flatt_esco` row.
- `GET /api/wellness-day` response shape: `hrv` is now the HRV block directly (was `{primary_algorithm, flatt_esco, ai_endurance}`). `HRVData` interface in `webapp/src/api/types.ts` deleted; `WellnessResponse.hrv` now typed as `HRVBlock`.
- `mcp_server/tools/hrv.py:get_hrv_analysis` simplified — drops `algorithm` parameter, returns the Flatt/Esco block at the top level.
- Webapp `Wellness.tsx`: HRV `TabSwitcher` removed; renders `HRVBlockView` directly. `useState`/`TabSwitcher` import dropped.
- `mcp_server/resources/athlete_profile.py`: AIEndurance line removed from prompt-resource text.

**Schema preserved.** `hrv_analysis.algorithm` stays in the composite PK; historical `algorithm='ai_endurance'` rows are not deleted, just never read. Lets us bring the algorithm back as a per-user opt-in later without a migration if the chronic-fatigue use case re-emerges.

**Tests:** `TestRmssdAiEndurance` class deleted from `tests/metrics/test_metrics.py` (3 tests). `TestActorCalculateHrv` and `TestActorUpdateHrvAnalysis` rewritten to single-algo shape (drops 4 dual-algo tests). 99 tests still green in metrics + actors.

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

## ATP Phase 3 — personal-patterns prompt enrichment (2026-05-07)

Closing the long-pending Phase 3 finishing work, see `docs/ADAPTIVE_TRAINING_PLAN_SPEC.md` §3.

- `data/personal_patterns.py` (new) — `compute_personal_patterns(user_id, days_back=90) → dict` aggregator over `training_log`. Always returns `entries_total`/`entries_complete`; aggregate fields populated only at `entries_complete >= MIN_COMPLETE_ENTRIES` (30). Single SQL query, no persistence.
- `mcp_server/tools/training_log.py:get_personal_patterns` — refactored to thin wrapper. Eliminated previous double-query insufficient-data path.
- `bot/prompts.py` — added `_render_personal_patterns` + `{personal_patterns_block}` slot in `_ATHLETE_BLOCK_TEMPLATE`. `render_athlete_block` now fans out `AthleteSettings.get_thresholds`/`AthleteGoal.get_goal_dto`/`AthleteSettings.get_all`/`compute_personal_patterns`/`UserFact.list_active` via `asyncio.gather` (parallel). `_safe_compute_personal_patterns` wraps the patterns coro in try/except — a transient DB error drops the patterns block, never breaks the chat prompt.
- Weekly report already had `get_personal_patterns` in the whitelist; now Claude actually has data to call it on.
- **Deviation from spec:** no cron, no persistence (originally proposed Sunday 18:00 weekly cron + `personal_patterns` table). Compute is sub-millisecond, parallel-fetched with the rest of the athlete block. Doc'd inline in spec §«Периодический анализ» with the rationale.

---

## Ramp test Run — pace-driven protocol + pipeline fixes (2026-05-07)

Run ramp test rebuilt around pace as control variable (HR/DFA observed). See `docs/ADAPTIVE_TRAINING_PLAN_SPEC.md` §«Фаза 4».

**Triggers** (`tasks/utils.py:RampTrainingSuggestion`): added `tsb > -10` and `recovery_score >= 70` gates; default `sports = ["Run", "Ride"]` (was Run only). Detector skips deep-fatigue / low-recovery days that produce noisy DFA fits.

**Run protocol** (`data/ramp_tests.py:build_ramp_steps_run`): 10 work steps × 3 min, `pace.units = "%pace"`, ladder 85→130% of athlete's threshold. Replaced fixed-LTHR ladder. Intervals.icu converts `%pace` to absolute pace using athlete's `threshold_pace`; Garmin renders the resulting target on the watch.

**Critical fix:** `data/intervals/dto.py:to_intervals_event` now sets `event.target = "PACE"` automatically when Run/Swim has pace-targeted terminal steps (new `has_pace_steps` property). Without this Intervals.icu defaults to `AUTO` → HR for Run, and Garmin **silently drops** pace cells from the workout step view. Verified live with the owner's account on 2026-05-07.

**Pipeline fixes:**
- **Fix A** — `_is_ramp_test_activity` (`tasks/actors/activities.py`) gains AiWorkout fallback. Defense-in-depth against missed `CALENDAR_UPDATED` webhook; `AiWorkout` is our local record written at `actor_push_workout` time.
- **Fix B** — `_ramp_failure_advice` (`tasks/formatter.py`) emits actionable next-step guidance per `diagnose_hrv_thresholds` code (`too_few_points` → "30+ min work phase", `noisy_fit` → "treadmill", `positive_slope` → "check chest strap", etc.). Surfaces as `💡 {advice}` line under the failure reason.
- **Fix C** — `User.detect_threshold_drift` bootstrap path: single sample with `R²>0.85` and `|drift|>10%` triggers alert. Avoids waiting for sample #2 on stale config (the first ramp test after setup would otherwise be wasted). Mirrored in `build_ramp_test_message` button condition.
- **Fix F** — `actor_update_zones` extended to push Run `THRESHOLD_PACE` alongside `LTHR`. Drift detection runs over `ActivityHrv.hrvt1_pace` (string `"M:SS"`/km, parsed via `parse_pace_to_sec`). Same gating as LTHR (≥2 sample standard / 1 sample bootstrap). Push converts our DB `sec/km` → Intervals.icu API `m/s` via `1000 / sec_per_km`. The "Update zones" button now lights up when either metric drifts.

**OAuth scope impact:** unchanged — `SETTINGS:WRITE` already required for LTHR push, threshold_pace rides on the same scope.

---

## Ramp drift — HRVT2 mapping fix + latest-only logic + recalibrated protocol (2026-05-08)

Three coupled changes that landed together. Spec context lives in `docs/ADAPTIVE_TRAINING_PLAN_SPEC.md` §«Threshold drift detection».

**1. HRVT1→HRVT2 semantic fix.** `actor_update_zones` previously pushed `ActivityHrv.hrvt1_hr` (aerobic threshold, DFA α1=0.75) into Intervals.icu's `lthr` field — but Intervals' `lthr` field semantically equals LTHR = HRVT2 = anaerobic threshold (α1=0.50). Result: Z3-Z7 zones in Intervals were calibrated against the wrong physiological point, sliding all training zones ~13% lower than intended (Z4 SubThreshold → effectively Z2 by real load). Fix: drift detector and actor now push **HRVT2 HR** to `lthr` and **pace at HRVT2** to `threshold_pace`. Affected: `data/db/user.py:detect_threshold_drift`, `tasks/actors/athlets.py:actor_update_zones`, `tasks/formatter.py:build_ramp_test_message`.

**2. Latest-only drift detection.** Replaced the `≥2 samples + avg-drift > 5%` standard path + `1 sample bootstrap (R²>0.85, drift>10%)` path with a single rule: **latest valid ramp test, gated by `|drift|>5%` AND `R²≥0.7`**. The 3-sample average was smoothing real progress away (after a successful test that shifted thresholds 8%, the rolling avg with two older samples still showed only 3-4% — under gate). `R²≥0.7` is a gentler quality gate than the bootstrap's 0.85 (R²=0.72 was common in real ramps), but tied to the latest test only — no avg dilution. `DriftAlertDTO`: `measured_avg → measured`, `tests_count` removed.

**3. New schema field — `hrvt2_pace`.** Migration `v2c3d4e5f6a7` adds `activity_hrv.hrvt2_pace` (nullable string, `"M:SS"` format). The DFA detector (`data/hrv_activity.py:detect_hrv_thresholds`) now interpolates pace at both HRVT1 and HRVT2 via the same speed↔HR linear regression that previously yielded only `hrvt1_pace`. Drift detector reads `hrvt2_pace` for the THRESHOLD_PACE alert. Old rows have `hrvt2_pace = NULL` until reprocessed.

**4. Run ramp protocol recalibration.** `data/ramp_tests.py:_RUN_RAMP_PCT` changed from `[85..130]` (10 steps) to `[80..115]` (8 steps), CD shortened 10→7 min. Old protocol implicitly assumed `threshold_pace ≈ HRVT1 pace` (so step 10 at 130% landed near LT2). After the HRVT2 mapping fix, `threshold_pace` IS pace at HRVT2 — step 10 at 130% would translate to ~3:41/km (raw 130% velocity above LT2), unrealistically fast. New ladder: step 5 = 100% = HRVT2 exactly; steps 6-8 (105-115%) push α1 below 0.5 cleanly without forcing a bail-out at unachievable paces. Total workout: 41 min (was 50).

**5. Pace formatting in zones notification.** `tasks/actors/athlets.py:actor_update_zones` now renders `THRESHOLD_PACE` updates as `Threshold pace Run: 4:55/km → 4:47/km` instead of raw seconds. Reuses `tasks.formatter.format_pace` (also consumed by the morning-report drift line in `tasks/actors/reports.py`).

**6. CLI: `reprocess-ramp-test`.** New command `python -m cli reprocess-ramp-test <user_id> <activity_id> [--push]` for back-filling `hrvt2_pace` on existing ramp tests post-migration. Re-runs `detect_hrv_thresholds` against stored `dfa_timeseries` + `work_segments`, patches **only** `hrvt2_pace` (other threshold fields untouched to avoid float-rounding drift). With `--push`, dispatches `actor_update_zones` so the new HRVT2-aligned values flow to Intervals.icu in one shot.

**Migration path for existing users:** apply `v2c3d4e5f6a7`, run `reprocess-ramp-test --push` per-user against their last valid Run ramp activity. The first updated zones notification will show big jumps (e.g. `LTHR 152 → 172`) — intentional, reflecting the corrected HRVT1→HRVT2 mapping.

---

## Zones tool reshape + FTP drift detection (2026-05-08)

Issue #313 fix. Spec: `docs/ZONES_FIX_SPEC.md`. Two coupled changes shipped in one PR.

**1. `get_zones` MCP tool reshape.** Original tool wrote a single untagged `power_zones` key in a per-sport loop — last sport won, athletes with both Stryd Run power (FTP=366W) and Bike power (FTP=208W) lost one side. Plus `min_w`/`max_w` were emitting raw `%FTP` boundaries from DB (per `data/db/athlete.py:33` units contract) as if they were absolute watts — internally inconsistent. Same shape bug in `pace_zones`. Fix: sport-tagged keys (`power_zones_bike` / `power_zones_run`, `pace_zones_run` / `pace_zones_swim`), dual-unit zone objects with both `min_pct/max_pct` (raw %) and `min_w/max_w` (or `min_sec_per_km`/`min_sec_per_100m`). New helpers `_dual_unit_power_zones` / `_dual_unit_pace_zones` in `mcp_server/tools/zones.py`. Sentinel `999` collapses to «no upper bound» cleanly. Untagged `power_zones`/`pace_zones` keys dropped (no in-repo consumers per Q5 audit). `bot/prompts.py:_zones_block` left alone — already correct (treats power zones as %FTP with explicit `units: %ftp` label).

**2. FTP drift detection — Ride.** Mirrors the LTHR/threshold_pace pattern: pushes `pow at HRVT2` (Coggan FTP ≈ pow at LT2 ≈ pow at HRVT2) to Intervals' `ftp` field. Pushing `hrvt1_power` (older shape) would under-shift cycling zones the same ~13% way HRVT1→`lthr` did.

- New schema column `activity_hrv.hrvt2_power FLOAT NULL` (migration `w3d4e5f6a7b8`, chains off `v2c3d4e5f6a7`).
- Detector (`data/hrv_activity.py`) extends the existing power↔HR regression with a parallel `hrvt2_power` interpolation, gated on `hrvt2_hr_safe` (the bound-checked HRVT2). Upper bound for HRVT2 raised to 800W (HRVT1's 500W ceiling is too tight for strong cyclists at FTP).
- New helper `_drift_alert_ftp(sport, hrvt2_power, r_squared, config_ftp)` in `data/db/user.py`, same `|drift|>5%` ∧ `R²≥0.7` gate. Branch added to `detect_threshold_drift` (Ride only).
- `actor_update_zones` (`tasks/actors/athlets.py`): new elif `metric == "FTP"` → push `client.update_sport_settings(sport, {"ftp": new_value})` + persist locally + notify «FTP Ride: 208 → 240 W».
- Formatter (`tasks/formatter.py:build_ramp_test_message`) shows HRVT2 power on the HRVT2 line and renders «текущий FTP» drift line for Ride ramps.
- MCP tool `activity_hrv` exposes `hrvt2_power` in `get_threshold_analysis` + `get_thresholds_history` (M5 fix continuation).
- CLI `reprocess-ramp-test` patches both `hrvt2_pace` (Run) and `hrvt2_power` (Ride). Idempotent. `--push` only when activity is the latest valid ramp for its sport.

**Test coverage:** `TestDriftAlertHelpers` +5 unit cases (`_drift_alert_ftp`), `TestFtpDrift` +5 integration cases via SQL, `TestActorUpdateZones.test_ftp_alert_pushes_watts`, `TestBuildRampTestMessage` +3 Ride/FTP cases, **new file `tests/mcp/test_zones.py`** (18 tests for the reshape — closes the previously-zero coverage on `get_zones` output shape, surfaced in §2 Q7 audit), **new file `tests/mcp/test_update_zones.py`** (8 tests — closes the Q4 gap where the MCP tool that pushes raw FTP/LTHR had no test coverage).

**Migration path for existing users:** apply `w3d4e5f6a7b8`, run `python -m cli reprocess-ramp-test <user_id> <activity_id> --push` against the latest valid Ride ramp activity. The notification will report «FTP Ride: <old> → <new> W» if drift fires.

---

## Ramp test protocol rebuild + drift detection upgrade (2026-05-08)

Six coupled changes shipped under one PR. Specs: `docs/RAMP_TEST_BIKE_SPEC.md` (protocol design), `docs/DFA_REGRESSION_METHODOLOGY_SPEC.md` (analytical pipeline + deferred sigmoid rewrite).

**1. Bike ramp protocol rebuilt.** Replaced static `RAMP_STEPS_RIDE` constant (6 steps, 65→103% FTP, uneven 7-8% increments — α1 didn't penetrate 0.5 cleanly, R²=0.62 typical) with `build_ramp_steps_ride()`: 2-phase WU (5min @ 50% + 5min @ 60% FTP) → 11 work steps × 3min @ 60-110% (uniform 5%) → final 1 × 4min @ 120% «push to failure» (deliberate 10% jump — calibration-trap insurance for athletes with undercalibrated FTP) → CD 10min @ 50%. Total 57 min. Three points below HRVT1 (≈75% FTP) for clean linear-fit at α1=0.75. Run protocol unchanged from previous PR (8 work steps × 3min @ 80→115%).

**2. Builder signature `(steps, warnings)`.** Both `build_ramp_steps_run` and `build_ramp_steps_ride` now return a tuple — second element accumulates per-test warnings (default fallback used: Run 295 s/km, Bike 200W; Run treadmill cap exceeded). Consumers (`tasks/utils.py:plan_ramp`, `mcp_server/tools/ramp_tests.py:create_ramp_test_tool`) updated. Workout rationale baked with §6 description templates (equipment list, pacing guidance, failure signals, cadence/cooling for bike).

**3. Drift detection switched from relative to absolute gates.** `data/db/dto.py` defines `DRIFT_LTHR_BPM = 3`, `DRIFT_PACE_SEC_PER_KM = 5`, `DRIFT_FTP_WATTS = 5`. The flat 5% relative gate was clinically too loose for LTHR (8 bpm at LTHR=160) and tighter than power-meter repeatability for FTP (10 W at 200W). Helpers `_drift_alert_lthr` / `_pace` / `_ftp` in `data/db/user.py` rewritten; `_drift_button_status` mirror in `tasks/formatter.py`.

**4. R² 3-tier confidence + auto-update.** `DRIFT_R2_HIGH = 0.85` triggers `actor_update_zones` automatically without user button (zones change silently with audit log line «Auto-update zones dispatched ...»). `0.70 ≤ R² < 0.85` shows the «Обновить зоны» button (current default UX). `R² < 0.70` only emits a soft hint. `build_ramp_test_message` returns `(msg, show_button, auto_update_fired)`; activities actor dispatches auto-update on the third flag.

**5. Phase-aware test cadence.** `RampTrainingSuggestion._staleness_threshold_days` reads `AthleteGoal.get_all`, picks the **nearest upcoming** active goal (not `get_active` which returned RACE_A first regardless of date — broke the «RACE_A in 200d + RACE_B in 7d» case). Returns: `None` (suppress) if ≤14 days to nearest race, `BASE_PHASE_CADENCE_DAYS=56` if ≤56d, `BUILD_PHASE_CADENCE_DAYS=42` else, or `DEFAULT_CADENCE_DAYS=30` if no active goal. Replaces the hardcoded 30-day staleness check.

**6. DFA detector E1+E2+E3 quality gates.**
- **E1**: `data/hrv_activity.py` slope sign sanity check now logs warning on positive slope (was silent return None) — physiologically α1 must monotonically fall with HR, positive slope = corrupt RR data.
- **E2**: Power bound check (50 < pow < 500/800W) emits explicit warning when out of range (was silent skip). Same for `np.linalg.LinAlgError` exception path.
- **E3**: New schema columns `hrvt1_confidence` / `hrvt2_confidence` (migration `x4e5f6a7b8c9`). Per-threshold confidence combines local point density (n_local in α1 ∈ ±0.15 of crossing) with global R² via `_per_threshold_tier(n, r²)` — `high` if `n≥5 AND r²≥0.85`, `medium` if `n≥3 AND r²≥0.70`, else `low`. Stored + exposed via `get_activity_hrv` MCP tool. **Drift gate keeps R²-based logic unchanged in this PR** — switching to per-threshold tier is part of the deferred H1 (sigmoid fit + per-step steady-state averaging) per `docs/DFA_REGRESSION_METHODOLOGY_SPEC.md` §3.

**Test coverage:** `TestRampAutoUpdateWiring` (3 tests guarding the auto-update dispatch), `TestPhaseAwareCadence` (7 tests covering peak/taper/base/build/multi-goal/inactive), `TestPerThresholdTier` (5 unit cases) + `TestPerThresholdConfidenceInDetectorOutput` (1 e2e), `TestActivityHrvCRUD.test_per_threshold_confidence_round_trip` (ORM mapping pin), and various boundary rewrites for the absolute-units switch. All 269 tests in the focused suite green.

**i18n updates:** new keys in `locale/en/LC_MESSAGES/messages.po` for «Зоны обновлены автоматически (high confidence)», plus pre-existing leak fix for «✅ Зоны обновлены», «ℹ️ Drift не обнаружен, зоны актуальны» in `tasks/actors/athlets.py` (pre-existing bug found during review, fixed in same PR).

**Migration path:** apply `x4e5f6a7b8c9`, no manual reprocessing needed. New columns default NULL on old rows; populated on next ramp test. Pre-existing migration `w3d4e5f6a7b8` (hrvt2_power) still requires `reprocess-ramp-test --push` for back-fill if needed (separate, earlier in this branch).

---

## Weekly changelog publisher — PR1 + PR2 complete (2026-05-10)

End-to-end implementation of `docs/WEEKLY_CHANGELOG_SPEC.md`. Athlete-facing «What's new» digest auto-generated weekly from merged PRs and published as a GitHub Discussion in `Announcements`; webapp sidebar shows an unread-badge link until the athlete clicks.

### PR1 — backend actor + cron

- **Actor:** `tasks/actors/changelog.py:actor_publish_weekly_changelog` (sync Dramatiq, `max_retries=0`). Pipeline: GitHub REST `pulls?state=closed&base=main` (paginated, stops once `updated_at < since`) → pre-filter (drop `chore|ci|build|test|docs:` regex, `user.type=='Bot'`, `SKIP_AUTHORS` allowlist, `skip-changelog`/`internal`/`dependencies` labels, non-`main` base, dedup on `(title.lower(), sha1(body[:200])[:8])`) → top-50 by `merged_at desc` → Claude `claude-sonnet-4-6` (max_tokens=800, temp=0.3, body trunc at 1500 chars + `... [truncated]`) → if Claude returns `NO_USER_FACING_CHANGES` skip → GraphQL `createDiscussion` mutation. Fail-soft on every branch — `{"status": "skipped_error", "stage": ..., "error": ...}` plus `sentry_sdk.capture_exception`, never raises. Status return values: `skipped_disabled` / `skipped_no_prs` / `skipped_all_filtered` / `skipped_internal` / `skipped_already_published` / `skipped_error` / `published`.
- **Cron:** `bot/scheduler.py:scheduler_publish_weekly_changelog_job` (Sun 15:00 Belgrade, `misfire_grace_time=7200, coalesce=True`). 4h buffer before the weekly report (Sun 19:00) so the owner can patch the Discussion by hand if Claude misfires.
- **Idempotency by week:** before publishing, the actor calls `fetch_latest_discussion()` (sync GraphQL helper, mirrors the FastAPI endpoint) and checks if the latest Discussion is within the last 7d 12h (`now - timedelta(days=7, hours=12)`). The 12h padding is in the past direction — it widens the window to catch a Discussion that's *just over* 7 days old when cron fires N seconds late (jitter). A flat `-7d` cutoff would have produced a duplicate on every late-firing Sunday. Lookup is best-effort: a GraphQL failure logs a warning and falls through to publish (worst case a duplicate, recoverable via `gh`).
- **Env vars (opt-in):** `CHANGELOG_REPO_ID` and `CHANGELOG_DISCUSSION_CATEGORY_ID` default to empty in `config.py`; production values for `radikkhaziev/triathlon-agent` (`R_kgDORnuZCQ` / `DIC_kwDORnuZCc4C8reQ`) live in `.env.example` and must be copied into prod `.env` to enable publishing. Empty defaults protect forks from accidentally publishing into the upstream repo's Announcements category. Also gated on `GITHUB_TOKEN` and `ANTHROPIC_API_KEY` non-empty (M2 from review — empty Anthropic key shouldn't burn a GitHub fetch first).
- **CLI:** `python -m cli publish-changelog [--force]` — manual trigger, idempotent by default, `--force` bypasses the lookup. Cron always runs without `--force`.
- **Tests:** 26 cases in `tests/tasks/test_weekly_changelog.py`. Pre-filter table-driven (10), prompt truncation (3), title formatting (2 — same-month + cross-month), end-to-end status branches (8 incl. concrete frozen-time title `неделя 04–10 мая 2026` to lock the C1 7-day Mon-Sun fix from review), idempotency (5 incl. boundary at 7d 0m and 7d 13h to lock the `-12h` padding constant). Sentry capture-fixture asserts `capture_exception` fires on each error branch.

### PR2 — REST endpoint + webapp link

- **Endpoint:** `api/routers/changelog.py:get_latest_changelog` — `GET /api/changelog/latest`, `require_viewer`. GraphQL `discussions(first:1, categoryId, orderBy:CREATED_AT desc)`. Returns `{url, title, published_at}` or 404 if no Discussion. **1h in-process cache** for both 200 AND 404 (a fresh repo with no Discussion yet would otherwise burn a GraphQL call per page load until first publish lands). **`asyncio.Lock` single-flight** on cache miss — concurrent requests at TTL boundary share one upstream fetch instead of fanning out to GitHub (per Copilot review #335). Lock pattern: check cache → acquire lock → re-check cache → upstream fetch → release. 503 + `Retry-After: 300` on GitHub upstream failure — must go on the `HTTPException(headers=...)`, not the `Response` object, because FastAPI replaces body with error JSON and drops `response.headers`. Module-level `_CACHE` dict — fine on single-worker uvicorn; `# NOTE` comment flags Redis migration trigger when `--workers N` flips.
- **Webapp:** shared `webapp/src/hooks/useChangelog.ts` — `useState` + `useEffect` + module-level `_inFlight: Promise | null` singleton so Sidebar (desktop) and BottomTabs More-menu (mobile) divide one fetch per session, not two. Hook gated on `useAuth().isAuthenticated` (H1 from review — without the gate the centralized `apiFetch` 401 handler force-redirects unauthenticated users on `/login` to `/login`, breaking login flow). Compares `cl.url` to `localStorage["changelog.last_seen_url"]`; renders a `● What's new` link **only** when unread (visual-debt avoidance — постоянная эмодзи-ссылка для not-readers = noise, §10 deviation). Click → `localStorage.setItem` (wrapped in `try/catch` for Safari private mode `QuotaExceededError`, H2 from review) + `setUnread(false)` → link disappears in-session.
- **Placement (dual-viewport):** the link sits **right after «План»** in both `webapp/src/components/Sidebar.tsx` (desktop ≥768px) and `webapp/src/components/BottomTabs.tsx` More-menu (mobile/Telegram Mini App). Both components iterate via `flatMap` and inject the changelog `<a>` immediately after `/plan`. BottomTabs additionally renders a small `●` indicator dot on the More-button itself when unread+menu-closed, so a mobile athlete sees the signal without opening the menu.
- **Types:** `webapp/src/api/types.ts:ChangelogLatest` interface.
- **i18n:** `sidebar.whats_new` — «Что нового» / «What's new».
- **Tests:** 9 cases in `tests/api/test_changelog_routes.py` — happy path shape, 404 empty, 1h cache (200), 1h cache (404), 503+Retry-After header survives, 503 doesn't poison cache, disabled→404 without GitHub call, demo viewer reads, **cache shared across users** (M1 from review — two stub user_ids, assert single GraphQL call to lock the «one Discussion per repo, global cache» contract).

### Decisions log (deviations from spec)

- **§4 hard-drop regex narrowed** to `chore|ci|build|test|docs:` (was `+perf|style|refactor`). `perf:` улучшения user-facing («дашборд в 3× быстрее»), `style:` чаще про UI Tailwind, `refactor:` иногда меняет UX. Trust Claude's «only what athlete notices» rule; +5-7k tokens worst-case ≈ $0.02/week.
- **§4 dedup key** changed to `(title.lower().strip(), sha1(body[:200])[:8])` — stacked PRs with same title but different bodies survive, while accidental re-merges (POC #318/#320 byte-identical title and body) collapse.
- **§5 body truncation** 500 → 1500 chars + `... [truncated]` suffix. Our PR bodies are 800-1500 chars («What was done / How to verify» template) — 500 cut exactly the «How to verify» block, where Claude reads user-facing impact. With top-50 cap: ~$0.04-0.06/week worst-case (was «<$0.01/week» in spec §12; new range ~$2-3/year still negligible).
- **§10 sidebar** — unread-only via localStorage, NOT permanent emoji link. Visual-debt avoidance for athletes who don't read changelogs.
- **§13 idempotency padding** — `-7d 12h` cutoff (NOT `-6d 12h` as initially suggested in review; that direction shrinks the window and worsens late-jitter). Caught by boundary tests during fix-cycle.
- **§9 cache 404** — endpoint caches the «no Discussion yet» state too, not just happy path. Cheap protection against fresh-repo page-load amplification.
- **Copilot review #335 fixes** — opt-in defaults (`config.py:""`) so a fork with `GITHUB_TOKEN` doesn't accidentally publish into the upstream Announcements; `LATEST_DISCUSSION_QUERY` extracted to `data/github.py` (no actor↔API coupling); `asyncio.Lock` single-flight against thundering herd on cache miss; CLI help text aligned with actor padding (`~7d 12h`); a11y `aria-label` with unread signal + `sidebar.unread` i18n key.
- **Dual-viewport placement** — link rendered right after `/plan` in both `Sidebar.tsx` and `BottomTabs.tsx` More-menu. Mobile More-button additionally shows a `●` indicator dot when unread+closed. Single fetch per session via `useChangelog` hook + module-level singleton Promise — Sidebar and BottomTabs share one `/api/changelog/latest` call.

### Operational follow-ups (organizational, not code)

- §16 step 3 — 4 weeks observation after first real cron run, tune prompt if Claude output drifts.
- §15.2 — owner enables GitHub email notification on `Announcements` category (one-click in repo settings) so athlete comments don't go unread.
- §15.3 — empirical decision on a predefined emoji-section allowlist after 3-4 real publications.

### Phase 2 (deferred until trigger)

- Bilingual single Discussion with `<!--LANG-SEPARATOR-->` (§11). Trigger: first active EN athlete (`SELECT COUNT(*) FROM users WHERE is_active AND athlete_id IS NOT NULL AND language = 'en'`).
- Email digest opt-in (`User.email_digest_optin` column). Only if requested.
- Inline Markdown rendering (vs current open-in-new-tab). Overkill without explicit ask.

### Skipped (consciously)

- §12 `ApiUsageDaily.increment` cost tracking — single weekly call ≈ $0.04-0.06, not worth the sync-helper churn. Add later if cost monitoring becomes useful.
- §15.5 dedup across weeks — superseded by weekly idempotency at the actor level (Wed manual + Sun cron no longer overlap).

### POC artifacts

POC Discussion #334 was created manually 2026-05-09 to validate the end-to-end flow before writing the spec. Deleted via `gh api graphql` 2026-05-10 as part of deploy prep so the first legitimate cron run isn't blocked by the idempotency check. Repo ID + Category ID resolved during the POC are now baked into `config.py` defaults.

---

## Weekly report archive — PR1+PR2+PR3 complete (2026-05-10)

End-to-end fix for the long-standing «Sunday weekly report disappears from chat» bug. Telegram returns `ok=true` to `sendMessage` for Claude's ~4 KB markdown but the recipient never sees it on some weeks (visible-text limit + opaque spam heuristic). Solution: persist the markdown server-side in a new `weekly_reports` table, swap the chat send to a short notification + WebApp button into the webapp's archive view. The chat becomes a pointer; the archive is the source of truth.

### PR1 — backend storage + actor wiring

- **Schema:** migration `bb8c9d0e1f2a` adds `weekly_reports` (`id`, `user_id` FK, `week_start DATE`, `content_md TEXT`, `model TEXT`, `generated_at TIMESTAMPTZ`). UNIQUE on `(user_id, week_start)` is the idempotency anchor — cron coalesce / manual rerun / watchdog re-kick all resolve to UPSERT, never duplicate. Composite index `ix_weekly_reports_user_week_desc` on `(user_id, week_start DESC)` drives the history list endpoint (PR2). FK without `ON DELETE CASCADE` — auditable history that survives user deactivation.
- **ORM:** `data/db/weekly_report.py` with three `@dual` methods. `upsert` uses Postgres `ON CONFLICT DO UPDATE` — atomic, no SELECT-then-update race. `RETURNING cls` + project-wide `expire_on_commit=False` keeps the detached row readable post-commit; no `session.refresh` needed (would only add a round-trip). `now_utc` materialised once so INSERT and ON CONFLICT branches stamp identical `generated_at`. `list_for_user(user_id, *, limit, before)` is the cursor-paginated read for the history endpoint — strict `<` semantics on `week_start` so the cursor row never echoes across pages. `get_one(user_id, week_start)` — single-row lookup, scoped by `user_id` from auth (path's `week_start` is a filter, never a tenant key).
- **Actor split:** extracted `tasks/actors/reports.py:generate_and_save_weekly_report(user) → (content_md, week_start) | None` — the «generate via Claude+MCP and persist» path. `actor_compose_weekly_report` is now a thin wrapper that calls the helper, and on success builds preview + WebApp button + Telegram send. CLI shares the same helper without touching Telegram.
- **Persist-before-send:** `WeeklyReport.upsert` runs BEFORE `tg.send_message`, so the original Telegram-drop bug no longer loses content — the archive is durable regardless of chat delivery.
- **Chat send:** notification label «📊 Недельный отчёт готов» + extracted preview + inline keyboard with `web_app` button → `{API_BASE_URL}/weekly/<iso_monday>`. Stays well under 4 KB (notification text ~250 chars), so the original drop-bug surface is gone in addition to being durable.
- **Preview helper:** `data/weekly_preview.py:extract_weekly_preview` — leaf-module pure function, no DB / network / secrets surface, safe for both router and actor to import without dragging dramatiq+sentry+MCPTool. Anchor regex `^[\s#*_>\-]*📊` + `re.MULTILINE` matches «📊 Итог недели» heading at line-start (allowing `**📊 …`, `## 📊 …`); rejects inline 📊 mentions in body text. Fallback path skips leading `#` headings, `---`/`===` dividers, blank lines until prose. Returns `—` placeholder when anchor matched but document is heading-only (rather than echoing `Итог`). Strips bold/italic markdown, truncates at word boundary with ellipsis (`rstrip(" .,;:…—")` + `…`).
- **Model name single-source:** `MCPTool.WEEKLY_MODEL: ClassVar[str] = "claude-sonnet-4-6"` — read by the API call AND stored in `weekly_reports.model`. Eliminates drift between the hardcoded literal and the actual Claude model used. `ClassVar` annotation matches the `_TG_400_PERMANENT_SUBSTRINGS` precedent in the same file.
- **Tests:** 21 cases across `tests/db/test_weekly_report.py` + `tests/data/test_weekly_preview.py`. ORM: upsert idempotency (insert + overwrite + audit-bump), cursor pagination strict-`<`, cross-tenant isolation on both `list_for_user` and `get_one`, sync-path round-trip via `asyncio.to_thread` (so the sync `@dual` branch isn't dead-untested). Preview: anchor path, line-start-only anchor (rejects inline 📊), bold-before-emoji `**📊`, `## 📊` heading, fallback skips `#`/`---`/blank, heading-only → `—` placeholder, word-boundary truncation.

### PR2 — REST endpoints

- **List:** `GET /api/weekly-reports?limit=20&before=<iso>` — returns `{items: [{week_start, preview, generated_at}], next_before}`. `next_before` is the oldest row's `week_start` when the page filled the limit (more history available); `null` means end-of-history. Hard cap `limit ≤ 50` via `Query(le=50)` — anything above returns 422. Server-renders `preview` from `extract_weekly_preview(content_md)` so the list payload stays small (21 cards × ~220 chars ≈ 5 KB vs 21 × full markdown ≈ 80 KB).
- **Detail:** `GET /api/weekly-reports/{week_start}` — full markdown + `model` + `generated_at`. FastAPI auto-parses `week_start` as `date`; malformed string → 422 before our code runs. Cross-tenant defence: 404 (not 200) for a week that exists but belongs to another user.
- **Auth:** both `require_athlete` — own-history-only, no demo read-through. Weekly summaries reference `user_facts` (injuries, family context) so demo cross-read would leak athlete-private context that the rest of the dashboard already gates on athlete identity.
- **Tests:** 10 cases in `tests/api/test_weekly_reports_routes.py`. List: empty-state, ordering, `next_before` set when page fills, `before` cursor returns older rows, limit-above-cap → 422, cross-tenant isolation. Detail: full content round-trip, 404 on missing, 404 on cross-tenant, 422 on malformed ISO date.

### PR3 — webapp archive UI + chat-link restoration

- **Routes:** `/weekly` (list) and `/weekly/:weekStart` (detail), both gated by `dataRoute(...)` so the existing onboarding/sports gates apply.
- **List page** (`webapp/src/pages/WeeklyReports.tsx`): infinite-scroll via `Load more` button + cursor `before`. Each card = Mon-Sun range + 3-line preview + chevron. Empty-state copy «No weekly reports yet — first arrives Sunday evening». Per-page error rendered inline below the list so the first page stays readable instead of collapsing into a full-screen error on a `Load more` failure.
- **Detail page** (`webapp/src/pages/WeeklyReport.tsx`): `react-markdown@^9` rendered with custom Tailwind component overrides (h1/h2/h3, p, ul/ol/li, strong/em, hr, code, a) — project doesn't ship the typography plugin so we override per-element. Prev/next chevrons shift the URL by ±7 days; `next` disabled when it'd land on a future Monday. Malformed path / missing report → 404-state with «Back to list» CTA. Trade-off: prev/next don't pre-validate existence (would require an extra API call or a `neighbours` field on the detail response), so one click can land on an empty week — accepted for API minimalism.
- **Navigation:** `/weekly` added to `MORE_NAV_ITEMS` between `/plan` and `/settings`. Sidebar (desktop ≥768px) and BottomTabs More-menu (mobile) inherit it via `ALL_NAV_ITEMS`.
- **Types & i18n:** `WeeklyReportListItem` / `WeeklyReportListResponse` / `WeeklyReportDetail` in `webapp/src/api/types.ts`. i18n keys `nav.weekly` + `weekly.{title,empty,load_more,loading_more,error_load,not_found,back_to_list,prev_week,next_week}` ru/en.
- **Bundle cost:** +50 KB gzipped from `react-markdown` (built JS 700 → 751 KB). Above the 500 KB Vite warning but acceptable for now; code-split is a separate concern.

### CLI — manual backfill

- `python -m cli create-weekly-report` — sweeps all active athletes via `User.get_active_athletes()`, runs `generate_and_save_weekly_report(user_dto)` per user, NEVER touches Telegram. Per-user `try/except` with `sentry_sdk.capture_exception` + tally `saved/skipped/failed` so one bad athlete (Intervals 5xx, expired OAuth, transient Anthropic) doesn't abort the whole sweep. Sequential — ~30-40s/user, ~$0.04/user via Claude. Used to backfill missed Sunday cron firings (the original drop-bug) or seed the webapp history view in dev. Idempotent per user: re-running overwrites the existing row for the current Mon-Sun window.

### Decisions log

- **No webapp-route stub in PR2** — the `/weekly/<date>` page lives in PR3. Between PR1 merge and PR3 merge the actor sent the FULL markdown (pre-PR1 chat behaviour), no preview or button — a button to a non-existent route silent-redirects to `/wellness` via the catch-all and breaks the implied UX. Persistence still happened during that window, so PR3 launches with archive content already accumulated.
- **Preview-helper leaf module** — `data/weekly_preview.py` rather than `tasks/actors/reports.py`. The API router would otherwise transitively pull dramatiq+sentry+MCPTool just to compute a string. Mirrors the «no API import path crosses tasks/» discipline from MT spec §6.
- **Auth: `require_athlete` not `require_viewer`** — weekly summaries surface `user_facts` (injuries, schedule, family), demo cross-read would expose private context. Different from the changelog endpoint where the data is global-per-repo.
- **Cursor `<` strict, not `<=`** — same convention as Stripe/GitHub list APIs. The cursor row never echoes across pages.

### Operational follow-ups

- First real cron under the new path: Sun 17 May 19:00 Belgrade. Smoke check: actor logs `Weekly report saved+sent for user N week=YYYY-MM-DD` and the chat shows preview+button (not full markdown).
- Manual prod backfill: `docker compose run --rm api python -m cli create-weekly-report` to seed week 4–10 May for all athletes — saves ~$0.85 cost, ~10-15 min sequential.

### Pending / deferred

- PR4 (optional, not scheduled): user-controlled regeneration from the webapp («mark as bad, regenerate» button). Trigger: athlete feedback that a particular week's summary is off.

---

## Pending

- MT Phase 2 (JWT upgrade): tenant_id, role, scope claims, bot middleware (resolve_tenant). See `docs/MULTI_TENANT_SECURITY_SPEC.md`.
- Retire legacy `INTERVALS_API_KEY` env vars (OAuth Phase 5).
- User-memory Phase 2 extractor — gated on `tool_facts_per_100_msgs_30d < 3` with `chat_msgs ≥ 100`.
- When scaling to multi-worker uvicorn, migrate `_retry_backfill_last_success` and `_mcp_config_last_access` to Redis INCR+EXPIRE.
- **DFA H1+H2** (per `docs/DFA_REGRESSION_METHODOLOGY_SPEC.md`): sigmoidal regression replacing linear fit + per-step steady-state averaging for power-HR regression. Validation pipeline + lazy migration story documented in spec.
