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
├── data/         # Domain: metrics.py, hrv_activity.py, workout_adapter.py, ramp_tests.py, crypto.py
│   ├── intervals/  # Intervals.icu client + DTOs
│   ├── garmin/     # Garmin GDPR parser + importer
│   └── db/         # SQLAlchemy ORM (@dual sync/async), all models, decorators
├── api/          # FastAPI: server.py, auth.py, deps.py, routers/ (wellness, activities, workouts, jobs, auth)
├── mcp_server/   # 60 MCP tools + 3 resources, context.py (user_id contextvars), sentry.py
├── webapp/       # React 18 SPA (Vite + TypeScript + Tailwind)
├── migrations/ / templates/ / static/ / locale/ / docs/ / tests/
```

---

## Database Schema

36 tables. Full column specs in `data/db/`. Key tables:

**Core:** `users` (multi-tenant, chat_id, role, api_key_encrypted, mcp_token, is_active, last_donation_at, + Intervals.icu OAuth: `intervals_access_token_encrypted` / `intervals_oauth_scope` / `intervals_auth_method` — `"api_key"` | `"oauth"` | `"none"` — see `api/routers/intervals/oauth.py`), `athlete_settings` (per-sport thresholds), `athlete_goals` (race goals + CTL targets), `wellness` (daily Intervals.icu data + recovery score + AI recommendations).

**Analysis:** `hrv_analysis` (dual-algorithm baselines), `rhr_analysis` (RHR baselines, inverted), `activity_details` (zones, intervals, EF, decoupling), `activity_hrv` (DFA a1, Ra/Da), `pa_baseline` (14d rolling), `fitness_projection` (CTL/ATL/rampRate decay curve from `FITNESS_UPDATED` webhook, dates can be future), `activity_achievements` (per-activity PRs from `ACTIVITY_ACHIEVEMENTS` webhook — power PRs / FTP changes / future milestone types; raw payload preserved in `extra` JSON; UNIQUE on user+activity+achievement_id).

**Training:** `scheduled_workouts`, `activities` (incl. `is_race`/`sub_type`/`rpe` — Borg CR-10 1-10 with `CHECK` constraint), `ai_workouts`, `training_log` (pre/actual/post + compliance + `race_id` FK), `exercise_cards`, `workout_cards`, `races` (name, distance, finish/goal time, placement, surface/weather, RPE, notes, race-day CTL/ATL/TSB/HRV/recovery snapshot, `carbs_consumed_g` for fueling-compliance metric).

**Race execution plans (PR1+PR2+PR3, see `docs/RACE_PLAN_SPEC.md`):** `race_plans` (per-goal AI-generated execution plan in JSONB — warmup / per-leg pacing corridors / fueling / contingencies / `confidence_tier` / `regen_count_today` / `pushed_for_race_date`; partial UNIQUE on `(goal_id, UTC day)` — idempotent same-day generation; `ondelete='SET NULL'` on `goal_id` + inline `payload.race` block as goal snapshot), `race_plan_compliance` (per-leg post-race metrics: HR-corridor / pace-power-band / fueling compliance — Phase 3 schema, writer-stub via `data/race_plan_compliance_service.py:compute_compliance`).

**Tracking:** `mood_checkins` (1-5 scales), `iqos_daily`, `api_usage_daily`, `star_transactions` (Telegram Stars donation ledger, `UNIQUE(charge_id)` for webhook idempotency, `refunded_at` nullable), `user_backfill_state` (1 row/user, cursor-based bootstrap progress: `oldest_dt`/`newest_dt`/`cursor_dt`/`chunks_done`/`status`+`last_error` + `hey_message` (datetime?) — post-onboarding nudge timestamp, see `docs/OAUTH_BOOTSTRAP_SYNC_SPEC.md`), `user_facts` (long-term memory: free-text traits per `topic` with `fact_language` (BCP-47), `source` (`tool`/`extractor`/`user`), `expires_at`, and soft-delete `deactivated_at`+`deactivated_reason` (`user_request`/`topic_cap`/`hard_cap`/`expired`/`contradicted`) — see `docs/USER_CONTEXT_SPEC.md`), `weekly_reports` (Sun 19:00 cron output: per-`(user_id, week_start)` markdown archive served by `/api/weekly-reports` history; UNIQUE `(user_id, week_start)` makes upsert idempotent under cron-coalesce / manual rerun).

**Garmin (9 tables):** `garmin_sleep`, `garmin_daily_summary`, `garmin_training_readiness`, `garmin_health_status`, `garmin_training_load`, `garmin_fitness_metrics`, `garmin_race_predictions`, `garmin_bio_metrics`, `garmin_abnormal_hr_events`.

---

## Implementation Status

All core modules done. Multi-tenant Phase 1.3 + Intervals.icu OAuth Phase 2 + OAuth bootstrap backfill Phase 1+2 + Webhook data capture Phase 1+2 + User-memory facts Phase 1 + ATP Phase 3 personal-patterns prompt enrichment complete. HRV collapsed to single algorithm (Flatt/Esco) in #307 — AIEndurance retired. Ramp-test protocols rebuilt 2026-05-08 against `docs/RAMP_TEST_BIKE_SPEC.md`: Run pace-driven 8-step `80→115%`, Bike power-driven 11+1 step `60→110% + 1×120%` push-to-failure (each calibrated against pace/pow at HRVT2). Both builders return `(steps, warnings)` with default fallbacks (Run 295 s/km, Bike 200W) when sport-settings missing. Phase-aware test cadence (`tasks/utils.py:RampTrainingSuggestion`): peak/taper (≤14d to nearest race) suppress, base (≤56d) 8w cadence, build (>56d) 6w cadence, no goal 30d default — multi-goal aware (nearest race wins, not RACE_A first). Drift detection: absolute per-metric gates (`DRIFT_LTHR_BPM=3`, `DRIFT_PACE_SEC_PER_KM=5`, `DRIFT_FTP_WATTS=5`) replace 5% relative; R² 3-tier (`DRIFT_R2_HIGH=0.85` → auto-fire `actor_update_zones`, medium → button, low → soft hint). `actor_update_zones` pushes HRVT2 (anaerobic) into Intervals' `lthr`/`threshold_pace`/`ftp` (Ride only for FTP — issue #313, 2026-05-08; prior HRVT1→`lthr` was mis-aligning every Intervals zone by ~13%). DFA detector gained slope-sign sanity check + power-bound WARN logging + per-threshold confidence (`hrvt1_confidence`/`hrvt2_confidence` columns combine `n_local` ±0.15 around α1 crossing × global R²) — see `docs/DFA_REGRESSION_METHODOLOGY_SPEC.md` for the deferred sigmoid-fit + per-step steady-state averaging. `get_zones` MCP tool reshape (issue #313): sport-tagged keys, dual-unit zone objects (raw % + absolute watts/sec). Webhook dispatchers 8/10 implemented. **Race-goal cleanup (issue #323, 2026-05-09):** dropped orphan `disciplines` JSON column from `athlete_goals`; race-goal sport_type enum (`triathlon`/`duathlon`/`aquathlon`/`run`/`ride`/`swim`/`fitness`) lives in `data/sport_map.py:RACE_SPORT_TYPES` with `resolve_race_sport_type` resolver — wired into `actor_sync_athlete_goals` + `suggest_race` (no more hardcoded `"triathlon"` from Intervals webhooks). User-editable via Settings dropdown (`PATCH /api/athlete/goal/{id}` + `sport_type` field). Settings page now shows ALL active future goals (`GET /api/athlete/goals`, `require_viewer` so demo can browse) — was single-anchor before. Prompt templates surface RACE_A + nearest race (helper `AthleteGoal.get_goals_for_prompt` returns 0/1/2 DTOs) with `Goals:` block + sport_type, replacing the legacy single-line `Goal:`. **Pending:** retire legacy `INTERVALS_API_KEY`, user-memory Phase 2 extractor, DFA H1+H2 (sigmoid + per-step averaging). MT Phase 2 (auth upgrade) и Phase 3 (security hardening) — deferred с зафиксированным audit + punch-list в `docs/MULTI_TENANT_SECURITY_SPEC.md` §9; триггеры для перезапуска описаны там же. **Race execution plans (commit `1d68ca6`, 2026-05-09):** PR1+PR2+PR3 done — backend `data/race_plan_service.py:build_race_plan` (Claude opus + forced JSON schema + race history + user_facts whitelist + sport-role/language prompt + bike→run/negative-split/contingencies-relevance prompt rules + corridor/units/HR/transitions validator + `confidence_tier` enum + 200d gate); REST `api/routers/race_plan.py` (GET/POST/inheritable-conditions, 1/day regen rate-limit + 5/day dry_run Redis quota); webapp `RacePlanPanel` + `RaceConditionsForm` on Goal tab (i18n ru/en); 24h pre-race push via `tasks/actors/race_plan.py` + `bot/scheduler.py:scheduler_pre_race_plan_push_job` (08:00 Belgrade, idempotent via `payload.pushed_for_race_date`); Phase 3 metrics shape define-not-ship (`race_plan_compliance` table + `Race.carbs_consumed_g` + `data/race_plan_compliance_service.py:compute_compliance` writer-stub, NO auto-trigger). Pending: PR4 (Phase 2.5 enrichment after Радик's 2 races), Phase 3 actor + dashboards, geo-source upgrade (issue #331). **Weekly changelog publisher (PR1+PR2, 2026-05-10):** `actor_publish_weekly_changelog` (Sun 15:00 Belgrade, `misfire_grace_time=7200, coalesce=True, max_retries=0`) собирает merged PR'ы за 7 дней, фильтрует hard-drop regex `chore|ci|build|test|docs:` + `user.type=='Bot'`/`SKIP_AUTHORS` + dedup по `(title, sha1(body[:200])[:8])`, отдаёт top-50 (body[:1500]) в `claude-sonnet-4-6` (~$0.04-0.06/неделю), публикует в GitHub Discussion (`Announcements`). **Opt-in:** `CHANGELOG_REPO_ID` + `CHANGELOG_DISCUSSION_CATEGORY_ID` дефолтят к `""` — нужно явно прописать в prod `.env` (значения в `.env.example`); пустые дефолты защищают форки от непреднамеренной публикации. Weekly idempotency: actor смотрит latest Discussion; если ≤ 7d 12h → `skipped_already_published` (Wed manual run не дублируется в Sun cron'е, padding 12h в past — против late-jitter). CLI: `python -m cli publish-changelog [--force]`. Fail-soft: любая ошибка → `skipped_error` + Sentry. PR2: `GET /api/changelog/latest` (require_viewer, 1h cache на 200 и 404, `asyncio.Lock` single-flight против thundering herd, 503+`Retry-After:300` через `HTTPException(headers=...)`). Webapp UI через `useChangelog` хук (singleton Promise — Sidebar и BottomTabs делят один fetch); ссылка рендерится сразу после «План» в обоих — desktop sidebar (≥768px) и mobile More-menu, плюс unread-индикатор-точка на самой More-кнопке. `localStorage["changelog.last_seen_url"]` — линк показывается ТОЛЬКО когда url ≠ last_seen (visual-debt avoidance, §10 deviation). i18n: `sidebar.whats_new`/`sidebar.unread` ru/en. Spec: `docs/WEEKLY_CHANGELOG_SPEC.md`. **Weekly report archive (PR1+PR2+PR3, commit `78f48f6`, 2026-05-10):** new `weekly_reports` table (UNIQUE `(user_id, week_start)`, atomic ON-CONFLICT upsert via `pg_insert.on_conflict_do_update`; `RETURNING cls` + project-wide `expire_on_commit=False` keeps the row readable post-commit, no `session.refresh` needed). Sunday 19:00 cron actor (`tasks/actors/reports.py:actor_compose_weekly_report`) now persists Claude's markdown BEFORE chat send so a Telegram-side silent-drop on long messages no longer loses content. Chat send is a short notification: «📊 Недельный отчёт готов» + extracted preview (`data/weekly_preview.py:extract_weekly_preview` — line-anchored `^[\s#*_>\-]*📊` regex, fallback skips `#`/`---`/blank lines, returns `—` placeholder when heading runs to EOF) + WebApp-кнопка → `{API_BASE_URL}/weekly/<iso_monday>`. REST: `GET /api/weekly-reports?limit=20&before=<iso>` (cursor pagination, strict `<` semantics, hard cap `limit ≤ 50`) + `GET /api/weekly-reports/{week_start}` (full markdown), оба `require_athlete` (own-history-only, no demo read-through — содержат `user_facts` чувствительный контекст). Webapp `/weekly` (More-меню под «План») и `/weekly/:weekStart` (react-markdown@^9, ←/→ навигация по неделям, future-disabled, 404 → CTA «К списку»). CLI `python -m cli create-weekly-report` — обходит всех активных атлетов, сохраняет в DB БЕЗ Telegram-отправки (для backfill пропущенных воскресений или dev-теста webapp); per-user `try/except` с `sentry_sdk.capture_exception`. `MCPTool.WEEKLY_MODEL: ClassVar[str]` — single source of truth для названия Claude-модели, читается и actor'ом и API-клиентом, eliminates drift между литералом и `weekly_reports.model`. i18n: `nav.weekly`/`weekly.{title,empty,load_more,not_found,prev_week,next_week,...}` ru/en. **Editable athlete age (2026-05-11):** `users.age` теперь редактируется из Settings → Athlete Profile через `PATCH /api/athlete/profile` (DTO `AthleteProfilePatchRequest`, bounds `ge=18, le=90`, `require_athlete` — демо получает 403, `model_fields_set` semantics; response возвращает валидированный input напрямую, без refetch — `age: int` строго провалидирован pydantic'ом и `update_age` не трансформирует). ORM `User.update_age` (`@dual`). Webapp использует `EditableNumberRow` (компонент параметризован опциональными `min`/`max`, дефолты 0/200 для обратной совместимости с CTL-вызовами; validation errors через `t('settings.editable_number.{error_invalid,error_out_of_range}')` с `{{min}}`/`{{max}}` интерполяцией), `patchProfile({age})` с optimistic update + rollback (без monotonic-seq guard — один редактируемый числовой field, редкая гонка в DB/UI desync принята). i18n: `settings.profile.{age_edit_hint,save_failed}` + `settings.editable_number.{error_invalid,error_out_of_range}` ru/en. Запись age — единственный writer этого поля (раньше заполняли через psql); read-sites (`bot/prompts.py`, `mcp_server/tools/zones.py`, `mcp_server/resources/athlete_profile.py`, `data/db/athlete.py:AthleteThresholdsDTO`, `api/routers/auth.py:/api/auth/me`) подхватывают на следующем запросе — prompt-кеш инвалидирует только динамический хвост (~240 ток.). **Post-activity card enrichment (2026-05-11):** `tasks/formatter.py:build_post_activity_message` переписан layered (header с distance/elevation, sport-specific HR/⚡power/🏃pace/🏊pace/100m, EF/Decoupling traffic-light/VI, weather из `ActivityWeather` с 8-octant ветром + headwind%, PI для ≥60 мин, CTL/ATL/TSB snapshot из `ActivityDetail.{ctl,atl}_snapshot`, achievement-блок с priority sort `FTP_CHANGE → BEST_POWER desc` capped at 4, Unicode-zone-bars `█▏▎▍▌▋▊▉` proportional + `░` padding до `_BAR_WIDTH=18` — фикс «не во всю длину» + лейблы Z1 32m · Z2 14m). Actor `_actor_send_activity_notification` фетчит `ActivityDetail`/`ActivityWeather`/`ActivityAchievement[]` (tenant-scoped по `user_id+activity_id`) в той же sync-сессии. Achievement notification остаётся отдельным actor'ом — accept rare double-display как safety net. **Tenant guard:** `if activity_row.user_id != user.id: return` после Activity fetch — `ActivityDetail`/`ActivityWeather` без `user_id` колонки (transitive FK scoping), guard защищает от Dramatiq-replay с foreign `activity_id`. Webapp Activity detail: узкий chart.js `ZoneChart` (50px в padded card) заменён на `ZoneBar size="detail"` (24px bar + grid с mins/% per zone), 7-zone palette (blue/green/amber/orange/red/magenta/purple, modulo fallback для будущих профилей). `format_pace` сменил truncation → rounding (290.6 → 4:51, не 4:50; ramp-test path не задет — там уже int). i18n: `ощущается`→`feels`, `встречный`→`headwind`. **Race-projection ML (Phase 1, 2026-05-11):** XGBRegressor per discipline (Run/Ride/Swim) + bootstrap residuals для 90% CI, MCP tool `get_race_projection(mode={today,race_day}, race_date, race_distance_*_m, target_hr_*)` — Mode 1 from current state, Mode 2 overrides CTL/ATL+per-sport eFTP из `FitnessProjection` на race_date с inflation `sqrt(days/30)` для CI; cold-start → `{available:False, reason:"model_not_trained"}`. New columns: `fitness_projection.sport_info JSONB` (per-sport eFTP/wPrime/pMax из FITNESS_UPDATED webhook — migration `b8c9d0e1f2a3`). Helpers: `data/ml/race_features.py:_compute_sport_ctl_series` (per-sport EMA inline в feature builder — webhook не отдаёт CTL per sport), `FitnessProjection.get(user_id, date)` + `sport_info_by_type(type, key)`. Modules: `data/ml/race_features.py` + `race_train.py` + `race_predict.py`, models → `static/models/race_{user}_{discipline}.joblib`. Retrain: `actor_retrain_race_models` (separate actor от progression, shared Sun 16:00 slot с 15s offset, `time_limit=600s, max_retries=0`). CLI: `python -m cli train-race-models <user_id>`. `_STATIC_PROMPT_CHAT` (cache segment #1) расширен секцией `## Race projection` с триггерами «прогноз/как пойду/if I raced today». Weekly integration: `SYSTEM_PROMPT_WEEKLY` step 8 + `WEEKLY_TOOL_NAMES` (`tasks/tools.py`) включают `get_race_projection(mode="race_day")` — one-line «🏁 Race-day прогноз ({date}): Swim X · Bike Y · Run Z → ~total (±N мин)» в секцию 📈 Прогресс, gated на `goal_event_date ∈ [30, 200]` дней + `available=True` (cold-start silently skip). Acceptance bar (user 1): Run MAE ≤10 sec/km, Ride MAE ≤15W, Swim MAE ≤8 sec/100m. Phase 2 (scenario engine, chart, race-specific Ride/Swim calibration) — deferred. Spec: `docs/ML_RACE_PROJECTION_SPEC.md`. **Webhook-time noise classification (Phase 1.6, 2026-05-12):** новые колонки `activities.noise_reason TEXT NULL` + `noise_scored_at TIMESTAMP NULL` (migration `aab8c9d0e1f2`) — persisted per-row tag заменяет live-фильтр в `race_features.py`. Trigger: `tasks/actors/activities.py:actor_update_activity_details` после `ActivityDetail.save()` (zones+pace ready), вызывает `data/ml/noise_classifier.py:classify_activity_row(activity, detail, thresholds)` + `Activity.set_noise_classification(user_id, activity_id, reason=, scored_at=, session=)` в той же sync-сессии. Phase 1.6 enum (Run-only): `run_walk` (pace > `threshold_pace × 1.6` AND avg_hr < `lthr × 0.65`, personalized через `AthleteSettings.get_thresholds`) + `run_recovery_jog` (relocated Phase 1.5 logic: Z1≥70% AND TSS<40). Priority: walk > jog (mistagged sport severe-er чем legit recovery). Fallback constants (6:30/km + 120bpm) для атлетов без synced settings (onboarding window). Three-state semantics: `NULL+NULL` not classified, `NULL+<dt>` checked clean (skip legacy fallback), `<reason>+<dt>` noise. Read-side `race_features.py:build_dataset` приоритизирует persisted tag, fallback на live `is_run_recovery_jog` только для `noise_scored_at IS NULL` legacy строк; logging разделено на `n_filtered_persisted` vs `n_filtered_legacy` для ops visibility. Backfill: `python -m cli classify-noise [--user-id=N] [--since-days=365] [--dry-run]` — без `--user-id` обходит всех active athletes, per-user `try/except` + `sentry_sdk.capture_exception`. NoiseReason Literal type (`data/ml/noise_classifier.py`) — single source of truth, no DB CHECK constraint (TEXT column, Python boundary validation). Phase 2 deferred (требует empirical calibration): `ride_recovery_spin`/`ride_commute`/`ride_indoor_test`; Swim — небольшое n у топ-атлетов делает fly-by classifier'ы опасными. Tests: 33 unit cases в `tests/ml/test_noise_classifier.py` (3 athlete cohorts × walk/jog/threshold scenarios) + 3 integration в `tests/ml/test_race_features.py::TestBuildDataset` (persisted-tag drop / scored-clean skip / legacy fallback). Spec: `docs/ML_RACE_PROJECTION_SPEC.md` §6.4. **CI inflation cap + OOS CTL warning (issues #350 + #359, 2026-05-12):** `INFLATION_MAX=1.8` + `MIN_RACE_DAYS_FOR_FORECAST=14` в `data/ml/race_predict.py` — на 200d capped 1.8× (вместо 2.6×); внутри 14d падаем на Mode 1 inflation=1.0 (taper-CTL ≈ today). Out-of-sample CTL warning: train сохраняет `metrics.ctl_feature_p90` в bundle, predict-time проверяет scaled `ctl_<discipline> > p90` после Mode 2 ratio, surface'ит `run: projected ctl_run=66.0 > train p90=30.0 — out-of-sample, model held conservative` в `envelope.warnings`. XGBoost trees не extrapolate, поэтому Mode 2 на CTL=66 у user 1 (train 15-45) даёт лишь 4 sec/km bump — это правильно, но раньше не коммуницировалось. Phase 2 root fix (formula-anchored baseline blend через Jack Daniels equivalent-pace) — deferred в spec §10.5 / §18 без отдельного issue. Tests: 7 новых в `test_race_predict.py` (TestPredictSplitsWithCi inflation cap×3 + TestOutOfSampleCtl ×4). Spec: §10.2 + §10.4 + §10.5 + §18. **Phase 2.0β2 — ML residual bias correction (2026-05-12):** root fix для #359 Q1. Linear `bias(d) = a + b*d` fitted per-athlete via mini-simulation across historical Run races × horizons {30,60,90,120,150}; saved в bundle metrics `bias_intercept`/`bias_slope`/`bias_n_races_fit`/`bias_fit_method`. Cold-start (n_races < 5) → pool constants `POOL_BIAS_INTERCEPT=6.178, POOL_BIAS_SLOPE=0.126` из `data/ml/bias_constants.py` (derived from user 1 simulation, retune через β2.1). Applied в `_predict_one` для **обоих** today + race_day mode'ов (schema parity); legacy bundles без bias keys skip silently (backwards-compat). Envelope surface: `bias_correction_applied: float` + `bias_fit_method: str|null`. Validation (LOO CV на user 1, n=22 races × 5 horizons = 90 points): MAE 55.04 → 50.04 sec/km (−9%), z=2.63 (p<0.01), per-horizon drop scales correctly (+1.72 sec/km @30d → +8.29 sec/km @150d). **Concrete effect for user 1 на Ironman 70.3 Belgrade (126d out):** bias = 22.05 sec/km → race-day Run prediction shifts с 5:51/км на ~5:29/км. Phase 2.0a formula blend (vLT × distance penalty) — **deprecated** после simulation показала RED verdict (MAE drop 0.69 sec/km, z=1.13, below noise floor). Tests: 6 новых в `test_race_predict.py::TestBiasCorrection` (race_day apply + today apply + intercept-only @0d + monotonic scaling + legacy backwards-compat + pool_fallback tag). Issues: #361 closed (envelope metadata), #362 deprecated (formula blend RED), #363 tracks β1/β2/β3 follow-ups. Spec: §10.5.5 (formula RED) + §10.5.6 (bias correction GREEN ship). **CTL projection consolidation (2026-05-11):** `predict_ctl` MCP tool (`mcp_server/tools/ctl_prediction.py`) переписан thin wrapper над `data.metrics.project_ctl_target` — раньше было два разных слоупа для одной задачи (morning report endpoint-difference vs Dashboard polyfit-regression), теперь обе поверхности (утренний отчёт + webapp Goal-tab) считают ETA одним numpy.polyfit на 14d окне. Response shape `predict_ctl` сохранён 1-в-1 (`current_ctl/target_ctl/ramp_rate_per_week/estimated_date/confidence/note/error`) — Claude-промпт форматирует это в живой текст «достигнешь 75 CTL к 12 июня», ломать ключи нельзя. Sport-filter сохранён. Mapping `reason → response`: `insufficient_data → {error}`, `already_at_target → {note: "Target already reached"}`, `flat/declining → {note: "CTL is declining or flat..."}`. Tests: 9 кейсов error envelopes + reason mapping + confidence heuristic (`tests/mcp/test_ctl_prediction.py`).

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
- **`render_athlete_block` / `get_static_system_prompt`** (`bot/prompts.py`) — chat system prompt is assembled in two cache segments: `_STATIC_PROMPT_CHAT` (invariant) + `render_athlete_block(user)` (per-user, includes `{zones_block}`). `_zones_block` writes the athlete's own Run/Ride/Swim boundaries so workout generation uses real zones rather than a hardcoded model. Treats `power_zones` / `pace_zones` as percentages directly (no dual-unit transform — Claude works fine with %). Fallbacks (Friel 5-zone): Run `_FALLBACK_RUN_HR_PCT` Z1 0-72%…Z5 92-100%, Bike HR `_FALLBACK_BIKE_HR_PCT` Z1 0-68%…Z5 105-120%, Ride power `_FALLBACK_RIDE_POWER_PCT` Z1 0-55%…Z5 105-120%. Each rendered branch always emits a concrete Example Z2 JSON step so Claude never invents the target shape.

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

**Intensity target mandate:** `PlannedWorkoutDTO._check_steps_have_targets` rejects any terminal (non-repeat-group) step without `hr` / `power` / `pace`. Garmin/Wahoo watches only alert on the target corridor when a numeric target is present, so text-only steps (`"Z2" label + duration`) are forbidden. **Exception:** sport `Other` (yoga, stretching, mobility) skips this validation — watches don't need intensity targets for these activities. Per-sport convention: Run → `hr` with `%lthr` units, Ride → `power` with `%ftp`, Swim → `pace` with `%pace`. Use `value` (low) + `end` (high) for a corridor. The `suggest_workout` MCP tool docstring and `_STATIC_PROMPT_CHAT` workout-generation section both enforce this contract — the validator is the backstop if the model forgets.

**Strava source filter:** Intervals.icu returns 422 `Cannot read Strava activities via the API` for `source == STRAVA` activities (licensing). `actor_fetch_user_activities` drops them **before** `Activity.save_bulk` so they never enter the DB or trigger downstream pipelines. `ActivityDTO.source` carries `GARMIN_CONNECT` / `OAUTH_CLIENT` / `STRAVA` / etc. from Intervals.icu.

---

## Operations

> Bot commands, API endpoints, webapp routes, CLI, migrations, onboarding, Docker — full reference in **`docs/OPERATIONS.md`**.

**Quick orientation:**

- **Bot commands** (`bot/main.py`) — `/start`, `/dashboard`, `/workout`, `/race`, `/web`, `/donate`, `/lang`, `/silent`, `/whoami`, `/health` (owner), `/stick` (owner). Free-form `<text>`/`<photo>` go to AI chat. Decorators: `@athlete_required` vs `@user_required`.
- **API** (`api/routers/`) — `/api/report`, `/api/wellness-day`, `/api/scheduled-workouts`, `/api/activities-week`, `/api/activity/{id}/details`, `/api/progress`, `/api/polarization`, `/api/fitness-projection`, `/api/race-plan` (GET/POST/inheritable-conditions — see `docs/RACE_PLAN_SPEC.md`), `/api/athlete/goals` (GET, list active future races, `require_viewer`) + `/api/athlete/goal/{id}` (PATCH `ctl_target`/`per_sport_targets`/`sport_type`, `require_athlete`) + `/api/athlete/profile` (PATCH `age`, `require_athlete`), dashboard routes, `/api/auth/*`, `/api/intervals/{auth,webhook}`, `/api/jobs/*`, `/health`, `/mcp`. Auth: Telegram initData or `Bearer <jwt>`; deps `require_viewer` / `require_athlete` / `require_owner`.
- **Webapp** (`webapp/`) — React 18 SPA, routes `/wellness` (home), `/plan`, `/activities`, `/activity/:id`, `/dashboard`, `/progress`, `/settings`, `/login`. Global auth gate: no `athlete_id` → `<OnboardingPrompt/>`.
- **CLI** (`cli.py`) — `shell`, `sync-{settings,wellness,activities,training-log}`, `import-garmin`, `backfill-races`, `bootstrap-sync`, `broadcast-migration`. Period formats: `2025Q4` / `2025-11` / `2025-01-01:2025-03-31`.
- **Migrations** — `alembic upgrade head`, `alembic revision --autogenerate -m "..."`. Auto-applied on deploy via `migrate` compose service.
- **Onboarding** — default path is automatic OAuth (user `/start` → connect Intervals.icu → fast-path + slow-path bootstrap). Manual CLI path exists for legacy/admin use.
- **Docker** — `docker compose up -d` (full stack); `docker compose run --rm api python -m cli ...` for CLI in container.

**Two-phase mutation flows:** `/workout` and free-form race creation use a **dry-run preview → inline-button confirm** pattern. The handler stores Claude's `tool_use` block from the first call and replays it directly via `MCPClient.call_tool` on confirm — no re-inference, bit-for-bit identical to preview, prevents prompt-injection on the state-mutating step. See `bot/main.py:_PREVIEWABLE_TOOLS` and the relevant section of `docs/OPERATIONS.md`.

---

## Key Implementation Notes

- **Intervals.icu API** — wellness every 10 min (4-8h) then every 30 min (9-22h), workouts hourly at :00 (4-23h), activities every 10 min (4-23h), DFA every 5 min (5-22h), evening report Mon–Sat 19:00 (`misfire_grace_time=3600, coalesce=True` — Sunday slot taken by weekly), weekly report Sunday 19:00 (`misfire_grace_time=7200, coalesce=True`, replaces Sunday evening report — contains the weekly summary + next week's plan), progression-model retrain Sunday 16:00 (`misfire_grace_time=7200, coalesce=True`), **24h pre-race plan push daily 08:00 Belgrade** (`misfire_grace_time=7200, coalesce=True` — fires when any active goal has `event_date == tomorrow`; idempotent via `payload.pushed_for_race_date`), **weekly changelog publisher Sunday 15:00 Belgrade** (`misfire_grace_time=7200, coalesce=True, max_retries=0` — 4h buffer до weekly report даёт окно поправить Discussion вручную). Misfire grace covers restart/deploy within the cron-tick window — without it APScheduler's default `misfire_grace_time=1` silently drops the user-facing report
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

## MCP Server (60 tools + 3 resources)

Run: `python -m mcp_server`. Production: mounted at `/mcp` (Streamable HTTP, per-user Bearer auth via `User.mcp_token`).

**Auth:** `MCPAuthMiddleware` resolves user by `User.get_by_mcp_token(token)` → sets `user_id` in `contextvars`. All tools call `get_current_user_id()` — user cannot manipulate `user_id` via tool parameters.

**60 tools** covering: wellness, HRV/RHR analysis, activities, training load/recovery, workouts (suggest/adapt/remove), training log, exercise/workout cards, mood/IQOS tracking, Garmin data (6 tools), efficiency trends, polarization index, goal progress, zones, races (`get_races`/`tag_race`/`update_race`/`suggest_race` for future-race creation with dry-run preview/`delete_race_goal` for removal), **race execution plans** (`generate_race_plan(goal_id?, dry_run, force_regen)` — thin wrapper over `data/race_plan_service.py:build_race_plan`; AI-generated structured plan from 6w training + race history + zones + race-day projection; idempotent same-day, regen 1/day rate-limit, dry_run 5/day per-user Redis cap; see `docs/RACE_PLAN_SPEC.md`), **race-projection ML** (`get_race_projection(mode, race_date, race_distance_*_m, target_hr_*)` — thin wrapper over `data/ml/race_predict.py:predict_splits_with_ci`; per-discipline XGBRegressor + bootstrap residuals → splits with 90% CI; Mode 1 (today) vs Mode 2 (race_day, CTL/eFTP from `fitness_projection` + sqrt(days/30) CI inflation); cold-start returns `{available:False, reason:"model_not_trained"}`; see `docs/ML_RACE_PROJECTION_SPEC.md`), **long-term user memory** (`save_fact`/`list_facts`/`deactivate_fact`/`reactivate_fact`/`get_fact_metrics` — see `docs/USER_CONTEXT_SPEC.md`), GitHub issues (`create_github_issue` available to athletes, sliding-window cap 5/24h per user, attribution in body — `user_id` only, no `@username`/`athlete_id`, `title ≤ 200` / `body ≤ 8000` cap; see `docs/MULTI_TENANT_SECURITY_SPEC.md` §13), API usage. **3 resources:** `athlete://profile`, `athlete://goal`, `athlete://thresholds`.

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
