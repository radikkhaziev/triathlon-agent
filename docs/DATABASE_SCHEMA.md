# Database Schema

Eight tables in PostgreSQL 16 (async via SQLAlchemy + Alembic).

## `wellness` — daily data from Intervals.icu
| Column | Type | Notes |
|---|---|---|
| `id` | String PK | "YYYY-MM-DD" |
| `ctl`, `atl`, `ramp_rate` | Float | training load from Intervals.icu |
| `ctl_load`, `atl_load` | Float | absolute load values |
| `sport_info` | JSON, nullable | per-sport breakdown |
| `weight`, `body_fat`, `vo2max` | Float, nullable | body metrics |
| `resting_hr` | Integer, nullable | resting heart rate |
| `hrv` | Float, nullable | RMSSD from wearable |
| `sleep_secs`, `sleep_score`, `sleep_quality` | nullable | sleep data |
| `steps` | Integer, nullable | daily steps |
| `ess_today`, `banister_recovery` | Float, nullable | stress/recovery |
| `recovery_score` | Float, nullable | combined 0-100 |
| `recovery_category` | String, nullable | excellent/good/moderate/low |
| `recovery_recommendation` | String, nullable | zone2_ok/zone1_long/zone1_short/skip |
| `readiness_score` | Integer, nullable | derived from recovery_score |
| `readiness_level` | String, nullable | green/yellow/red |
| `ai_recommendation` | Text, nullable | Claude AI output |
| `ai_recommendation_gemini` | Text, nullable | Gemini AI output (optional, only if GOOGLE_AI_API_KEY set) |

## `hrv_analysis` — dual-algorithm HRV baselines
| Column | Type | Notes |
|---|---|---|
| `date` | String PK, FK → wellness | |
| `algorithm` | String PK | "flatt_esco" or "ai_endurance" |
| `status` | String | green/yellow/red/insufficient_data |
| `rmssd_7d`, `rmssd_sd_7d` | Float | 7-day baseline |
| `rmssd_60d`, `rmssd_sd_60d` | Float | 60-day baseline |
| `lower_bound`, `upper_bound` | Float | decision bounds |
| `cv_7d` | Float | coefficient of variation % |
| `swc` | Float | smallest worthwhile change |
| `days_available` | Integer | data points used |
| `trend_direction`, `trend_slope`, `trend_r_squared` | nullable | 7d trend |

Both algorithms are **always computed** on every save. `settings.HRV_ALGORITHM` selects which one feeds the recovery score.

## `rhr_analysis` — resting HR baselines
| Column | Type | Notes |
|---|---|---|
| `date` | String PK, FK → wellness | |
| `status` | String | green/yellow/red (inverted: high RHR = red) |
| `rhr_today` | Float | today's value |
| `rhr_7d`, `rhr_sd_7d` | Float | 7-day baseline |
| `rhr_30d`, `rhr_sd_30d` | Float | 30-day baseline (used for bounds) |
| `rhr_60d`, `rhr_sd_60d` | Float | 60-day baseline (context) |
| `lower_bound`, `upper_bound` | Float | ±0.5 SD of 30d |
| `cv_7d` | Float | coefficient of variation % |
| `days_available` | Integer | data points used |
| `trend_direction`, `trend_slope`, `trend_r_squared` | nullable | 7d trend |

## `scheduled_workouts` — planned workouts from Intervals.icu calendar
| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | Intervals.icu event ID |
| `start_date_local` | String | "YYYY-MM-DD" |
| `end_date_local` | String, nullable | end date for multi-day events |
| `name` | String, nullable | workout name (e.g. "CYCLING:Endurance w/ 2min tempo") |
| `category` | String | WORKOUT / RACE_A / RACE_B / RACE_C / NOTE |
| `type` | String, nullable | canonical sport type: Ride, Run, Swim, Other |
| `description` | Text, nullable | full workout structure (intervals, zones, power targets from HumanGo) |
| `moving_time` | Integer, nullable | planned duration in seconds |
| `distance` | Float, nullable | planned distance in km |
| `workout_doc` | JSON, nullable | native Intervals.icu workout format |
| `updated` | DateTime(tz), nullable | last update timestamp |
| `last_synced_at` | DateTime(tz), nullable | set to `now(UTC)` on every upsert in `save_scheduled_workouts()` |

Synced every 1 hour (at :00, hours 4-23) via scheduler. Upserted by Intervals.icu event ID.

## `activities` — completed activities from Intervals.icu
| Column | Type | Notes |
|---|---|---|
| `id` | String PK | Intervals.icu activity ID (e.g. "i12345") |
| `start_date_local` | String | "YYYY-MM-DD" |
| `type` | String, nullable | canonical sport type: Ride, Run, Swim, Other |
| `icu_training_load` | Float, nullable | TSS/hrTSS/ssTSS from Intervals.icu |
| `moving_time` | Integer, nullable | duration in seconds |
| `average_hr` | Float, nullable | average heart rate during activity |
| `is_race` | Boolean, default false | sticky race flag — once set to true (via `tag_race` MCP tool or Intervals.icu `race=true`) it cannot be cleared by re-sync |
| `sub_type` | String, nullable | `NONE` / `COMMUTE` / `WARMUP` / `COOLDOWN` / `RACE` — kept across re-syncs via `COALESCE` |
| `rpe` | Integer, nullable | Borg CR-10 (1-10) post-workout subjective effort rating. `CHECK (rpe IS NULL OR (rpe BETWEEN 1 AND 10))`. Single source of truth — `training_log` reads via JOIN on `actual_activity_id`. Written via single-shot Telegram inline-keyboard callback (`rpe:{activity_id}:{value}`), never from MCP tools. See `docs/RPE_SPEC.md`. |
| `last_synced_at` | DateTime(tz), nullable | set to `now(UTC)` on every upsert in `save_activities()` |

Synced every hour at :30 via scheduler. Used for per-sport CTL calculation (EMA τ=42d).
Indexed on `start_date_local` for range queries.

**Save merge semantics** (`Activity.save_bulk`): on `ON CONFLICT`, `is_race` uses `OR` and `sub_type` uses `COALESCE(existing, excluded)`. Locally tagged races survive Intervals.icu re-syncs where `race=false` — Intervals.icu is not the source of truth for race tagging.

## `races` — extended race context (see `docs/RACE_TAGGING.md`)
| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | autoincrement |
| `user_id` | Integer FK → users | tenant scope |
| `activity_id` | String FK → activities, UNIQUE | one race per activity |
| `name` | String | "Novi Sad Marathon 25K" |
| `race_type` | String, default "C" | A / B / C |
| `goal_id` | Integer FK → athlete_goals, nullable | link to goal event |
| `distance_m`, `finish_time_sec`, `goal_time_sec` | nullable | result vs goal |
| `placement`, `placement_total`, `placement_ag` | nullable | rank |
| `surface`, `weather`, `elevation_gain_m` | nullable | conditions |
| `race_day_ctl`, `race_day_atl`, `race_day_tsb`, `race_day_hrv_status`, `race_day_recovery_score`, `race_day_weight` | nullable | wellness snapshot on race day (auto-filled) |
| `avg_pace_sec_km`, `normalized_pace_sec_km` | nullable | computed pace metrics |
| `splits` | JSON, nullable | per-km splits |
| `rpe`, `notes` | nullable | subjective |
| `created_at`, `updated_at` | DateTime(tz) | |

Auto-created by `actor_fill_training_log._ensure_race_record` when `activity.is_race=true`, or manually via MCP `tag_race`. Linked from `training_log.race_id` FK.

## `activity_hrv` — post-activity DFA alpha 1 analysis (Level 2)
| Column | Type | Notes |
|---|---|---|
| `activity_id` | String PK, FK → activities | |
| `date` | String | "YYYY-MM-DD" |
| `activity_type` | String | "Ride" or "Run" |
| `hrv_quality` | String, nullable | good/moderate/poor |
| `artifact_pct` | Float, nullable | % of corrected RR intervals |
| `rr_count` | Integer, nullable | total RR intervals extracted |
| `dfa_a1_mean` | Float, nullable | mean DFA alpha 1 across activity |
| `dfa_a1_warmup` | Float, nullable | DFA alpha 1 during first 15 min |
| `hrvt1_hr`, `hrvt1_power`, `hrvt1_pace` | nullable | aerobic threshold (a1=0.75) |
| `hrvt2_hr` | Float, nullable | anaerobic threshold HR (a1=0.50) |
| `threshold_r_squared`, `threshold_confidence` | nullable | regression quality |
| `ra_pct`, `pa_today` | Float, nullable | Readiness (Ra) vs baseline |
| `da_pct` | Float, nullable | Durability (Da) first vs second half |
| `processing_status` | String | processed/no_rr_data/low_quality/too_short/error |
| `dfa_timeseries` | JSON, nullable | sampled every 30s for charts |

Processed every 5 min via scheduler. Only bike/run activities ≥15 min with chest strap HRM (ANT+).

## `pa_baseline` — Pa baseline for Readiness (Ra) calculation
| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | autoincrement |
| `activity_type` | String | "Ride" or "Run" |
| `date` | String | "YYYY-MM-DD" |
| `pa_value` | Float | power (bike) or speed (run) at fixed DFA a1 during warmup |
| `dfa_a1_ref` | Float, nullable | reference DFA a1 level |
| `quality` | String, nullable | good/moderate/poor |

Ra baseline = average Pa over last 14 days (≥3 data points required).

## `mood_checkins` — emotional state tracking
| Column | Type | Notes |
|---|---|---|
| `id` | Integer PK | autoincrement |
| `timestamp` | DateTime(tz) | момент записи (UTC) |
| `energy` | Integer, nullable | 1-5 (1 = нет сил, 5 = полон энергии) |
| `mood` | Integer, nullable | 1-5 (1 = плохое, 5 = отличное) |
| `anxiety` | Integer, nullable | 1-5 (1 = спокоен, 5 = сильная тревога) |
| `social` | Integer, nullable | 1-5 (1 = изоляция, 5 = много общения) |
| `note` | Text, nullable | свободный текст |

Записи создаются через MCP tool `save_mood_checkin`. Claude предлагает записать, пользователь подтверждает.

## Planned: `activity_details` — extended activity statistics

> Full spec: `docs/ACTIVITY_DETAILS_PHASE1.md`

| Column | Type | Notes |
|---|---|---|
| `activity_id` | String PK, FK → activities | |
| `max_hr` | Integer, nullable | max heart rate |
| `avg_power` | Integer, nullable | average power watts (bike) |
| `normalized_power` | Integer, nullable | NP watts (bike) |
| `avg_speed` | Float, nullable | m/s |
| `max_speed` | Float, nullable | m/s |
| `pace` | Float, nullable | **⚠ Ambiguous unit from Intervals.icu** — observed as m/s for runs (same value as `avg_speed`), not sec/km as the field name suggests. Don't read directly in UI — derive pace from `moving_time / distance` instead (see `webapp/src/pages/Activity.tsx`, issue #44). |
| `gap` | Float, nullable | grade-adjusted pace, presumed sec/km (run). Guard UI reads with `> 60` in case upstream ever ships m/s here too. |
| `distance` | Float, nullable | meters |
| `elevation_gain` | Float, nullable | meters |
| `avg_cadence` | Float, nullable | rpm (bike) or spm (run) |
| `avg_stride` | Float, nullable | meters (run) |
| `calories` | Integer, nullable | kcal |
| `intensity_factor` | Float, nullable | IF = NP/FTP (from Intervals.icu) |
| `variability_index` | Float, nullable | VI = NP/avg power |
| `efficiency_factor` | Float, nullable | EF from Intervals.icu |
| `power_hr` | Float, nullable | power:HR ratio |
| `decoupling` | Float, nullable | aerobic decoupling % (<5% = good aerobic base) |
| `trimp` | Float, nullable | training impulse |
| `hr_zones` | JSON, nullable | array of seconds per HR zone |
| `power_zones` | JSON, nullable | array of seconds per power zone (bike) |
| `pace_zones` | JSON, nullable | array of seconds per pace zone (run/swim) |
| `intervals` | JSON, nullable | per-interval breakdown from Intervals.icu |
