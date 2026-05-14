// Auth
export interface AuthVerifyCodeResponse {
  token: string
  role: 'owner' | 'viewer' | 'demo'
  expires_in_days: number
}

export interface IntervalsStatus {
  method: 'oauth' | 'api_key' | 'none'
  athlete_id: string | null
  scope: string | null
}

// Race-goal classification (issue #323 Strand A) — mirrors backend
// ``data.sport_map.RACE_SPORT_TYPES``. Distinct from ``SportTag`` (training
// disciplines): a race can be multi-sport (triathlon) or non-race (fitness),
// and the user picks this on the Settings → Race Goal section dropdown.
export type SportType = 'triathlon' | 'duathlon' | 'aquathlon' | 'run' | 'ride' | 'swim' | 'fitness'

export interface AuthMeGoal {
  id?: number | null
  event_name: string
  event_date: string
  sport_type?: SportType
  ctl_target?: number | null
  per_sport_targets?: { swim?: number; ride?: number; run?: number } | null
}

// Race goal category — RACE_A is the season anchor, B is a tune-up, C is a
// fitness check. Mirrors backend `athlete_goals.category` value-set; tightened
// from `string` to a literal union so i18n key building (`settings.goal.
// category.${category}`) is typo-safe (Copilot review #325).
export type GoalCategory = 'RACE_A' | 'RACE_B' | 'RACE_C'

// Single active goal entry returned by `GET /api/athlete/goals` (#323 Strand C).
// Distinct from `AuthMeGoal` — this one carries `category` (RACE_A/B/C) for the
// list view's badge, while `AuthMeGoal` is the legacy single-anchor shape.
export interface AthleteGoal {
  id: number
  category: GoalCategory
  event_name: string
  event_date: string
  sport_type: SportType
  ctl_target?: number | null
  per_sport_targets?: { swim?: number; ride?: number; run?: number } | null
}

export interface AthleteGoalsResponse {
  goals: AthleteGoal[]
}

export type SportTag = 'swim' | 'ride' | 'run'

export interface AuthMeResponse {
  role: 'owner' | 'viewer' | 'demo' | 'anonymous'
  authenticated: boolean
  language?: string
  intervals?: IntervalsStatus
  // Issue #266: false means the user authed via Login Widget but never
  // pressed /start in the bot, so Telegram has no chat to receive messages.
  // Settings page renders a "Open bot first" CTA instead of the OAuth button
  // until this flips true.
  bot_chat_initialized?: boolean
  bot_username?: string | null
  // Goal block: null when athlete has no active race. Dashboard hides the
  // Goal tab entirely in that case (END-12 scoping).
  goal?: AuthMeGoal | null
  // Sport selection. null = athlete hasn't passed through SportsPicker yet
  // → App-level gate shows the picker. Otherwise a non-empty subset of
  // {swim, ride, run} (server enforces ≥1 entry).
  sports?: SportTag[] | null
}

// Recovery
export interface RecoveryData {
  score: number | null
  category: string | null
  emoji: string
  title: string
  recommendation: string
  readiness_score: number | null
  readiness_level: string | null
}

// HRV
export interface HRVTrend {
  direction: string | null
  slope: number | null
  r_squared: number | null
}

export interface HRVBlock {
  status: string
  status_emoji: string
  today: number | null
  mean_7d: number | null
  sd_7d: number | null
  mean_60d: number | null
  sd_60d: number | null
  delta_pct: number | null
  lower_bound: number | null
  upper_bound: number | null
  swc: number | null
  swc_verdict: string | null
  cv_7d: number | null
  cv_verdict: string | null
  days_available: number
  trend: HRVTrend | null
}

// RHR
export interface RHRBlock {
  status: string
  status_emoji: string
  today: number | null
  mean_7d: number | null
  sd_7d: number | null
  mean_30d: number | null
  sd_30d: number | null
  mean_60d: number | null
  sd_60d: number | null
  delta_30d: number | null
  lower_bound: number | null
  upper_bound: number | null
  cv_7d: number | null
  cv_verdict: string | null
  days_available: number
  trend: HRVTrend | null
}

// Sleep
export interface SleepData {
  score: number | null
  duration: string | null
  duration_secs: number | null
}

// Training Load
export interface TrainingLoadData {
  ctl: number | null
  atl: number | null
  tsb: number | null
  ramp_rate: number | null
  sport_ctl: {
    swim: number | null
    ride: number | null
    run: number | null
  }
}

// Body
export interface BodyData {
  weight: number | null
  body_fat: number | null
  vo2max: number | null
  steps: number | null
}

// Stress
export interface StressData {
  ess_today: number | null
  banister_recovery: number | null
}

// Wellness / Report response
export interface WellnessResponse {
  date: string
  has_data: boolean
  is_today?: boolean
  has_prev?: boolean
  has_next?: boolean
  role?: 'owner' | 'viewer' | 'anonymous'
  recovery: RecoveryData
  hrv: HRVBlock
  rhr: RHRBlock
  sleep: SleepData
  training_load: TrainingLoadData
  body: BodyData
  stress: StressData
  ai_recommendation: string | null
  updated_at?: string | null
}

// Scheduled Workouts
export interface ScheduledWorkout {
  id: number
  type: string | null
  name: string | null
  category: string
  duration: string | null
  duration_secs: number | null
  distance_km: number | null
  description: string | null
}

// One step in a structured workout. Mirrors WorkoutStepDTO (data/intervals/dto.py).
// `hr` / `power` / `pace` targets carry % corridor; absolute values derived
// on the frontend using `WorkoutDetailThresholds`.
// `end` is optional — backend validator accepts targets with only `start` (e.g.
// a single-value target rather than a corridor). Renderer must degrade
// gracefully to "{start}%" when `end` is absent.
export interface WorkoutTarget {
  units: string  // "%lthr" | "%ftp" | "%pace" | "rpm"
  start: number
  end?: number | null
}

export interface WorkoutStep {
  text: string
  duration: number  // seconds (0 for repeat groups)
  distance: number | null  // meters
  reps: number | null
  hr: WorkoutTarget | null
  power: WorkoutTarget | null
  pace: WorkoutTarget | null
  cadence: WorkoutTarget | null
  steps: WorkoutStep[] | null  // sub-steps for repeat groups
}

export interface WorkoutDetailThresholds {
  lthr_run: number | null
  lthr_bike: number | null
  ftp: number | null
  threshold_pace_run_sec_per_km: number | null
  css_sec_per_100m: number | null
}

// Intervals.icu enrichment populated on POST /events.
// All fields nullable — some sports lack the relevant signal (Swim has no power).
// `intensity_pct` is Intervals.icu's `icu_intensity` field — 0-100 percent
// (NOT the 0-1 decimal used by TrainingPeaks).
export interface WorkoutEnrichment {
  tss: number | null
  normalized_power: number | null
  variability_index: number | null
  polarization_index: number | null
  intensity_pct: number | null
  zone_times: { id: string; secs: number | null }[] | null
}

// Per-sport zone boundaries from AthleteSettings (units differ per kind —
// see CLAUDE.md «HR / Power / Pace Zones» section).
export interface WorkoutDetailZones {
  hr: number[] | null      // absolute bpm, ascending
  power: number[] | null   // %FTP, ascending (NOT watts)
  pace: number[] | null    // %threshold (100.0 = threshold), ascending
}

export interface ScheduledWorkoutDetail {
  id: number
  type: string | null
  name: string | null
  category: string
  date: string
  duration: string | null
  duration_secs: number | null
  distance_km: number | null
  description: string | null
  steps: WorkoutStep[] | null
  rationale: string | null
  enrichment: WorkoutEnrichment
  thresholds: WorkoutDetailThresholds
  zones: WorkoutDetailZones
}

export interface ScheduledWorkoutsDay {
  date: string
  weekday: string
  workouts: ScheduledWorkout[]
}

export interface ScheduledWorkoutsResponse {
  week_start: string
  week_end: string
  week_offset: number
  today: string
  last_synced_at: string | null
  has_prev: boolean
  has_next: boolean
  role: 'owner' | 'viewer' | 'anonymous'
  days: ScheduledWorkoutsDay[]
}

// Activities
export interface ActivityItem {
  id: string
  type: string | null
  moving_time: number
  duration: string | null
  icu_training_load: number | null
  average_hr: number | null
  is_race?: boolean
}

export interface ActivitiesDay {
  date: string
  weekday: string
  activities: ActivityItem[]
}

export interface ActivitiesWeekResponse {
  week_start: string
  week_end: string
  week_offset: number
  today: string
  last_synced_at: string | null
  has_prev: boolean
  role: 'owner' | 'viewer' | 'anonymous'
  days: ActivitiesDay[]
}

// Activity Details
export interface ActivityDetails {
  max_hr: number | null
  avg_power: number | null
  normalized_power: number | null
  avg_speed: number | null
  max_speed: number | null
  pace: number | null
  gap: number | null
  distance: number | null
  elevation_gain: number | null
  avg_cadence: number | null
  avg_stride: number | null
  calories: number | null
  intensity_factor: number | null
  variability_index: number | null
  efficiency_factor: number | null
  power_hr: number | null
  decoupling: number | null
  trimp: number | null
  hr_zones: number[] | null
  power_zones: number[] | null
  pace_zones: number[] | null
  hr_zone_times: number[] | null
  power_zone_times: number[] | null
  pace_zone_times: number[] | null
  intervals: ActivityInterval[] | null
}

export interface ActivityInterval {
  moving_time?: number
  elapsed_time?: number
  average_watts?: number
  weighted_average_watts?: number
  average_heartrate?: number
  average_cadence?: number
  average_speed?: number
  gap?: number
}

export interface ActivityHRV {
  dfa_a1_mean: number | null
  hrv_quality: string | null
  ra_pct: number | null
  da_pct: number | null
  hrvt1_hr: number | null
  hrvt1_power: number | null
  hrvt1_pace: string | null
  hrvt2_hr: number | null
  processing_status: string
}

export interface RaceInfo {
  name: string
  race_type: string | null
  distance_km: number | null
  finish_time_sec: number | null
  goal_time_sec: number | null
  placement: number | null
  placement_total: number | null
  placement_ag: string | null
  surface: string | null
  weather: string | null
  avg_pace_sec_km: number | null
  rpe: number | null
  notes: string | null
  race_day_ctl: number | null
  race_day_atl: number | null
  race_day_tsb: number | null
  race_day_recovery_score: number | null
  race_day_hrv_status: string | null
}

// Outdoor weather block — from `ACTIVITY_UPLOADED` webhook when
// `dto.has_weather=True`. Indoor / virtual rides return `weather: null`.
export interface ActivityWeatherInfo {
  avg_temp_c: number | null
  min_temp_c: number | null
  max_temp_c: number | null
  avg_feels_like_c: number | null
  avg_wind_speed_mps: number | null
  avg_wind_gust_mps: number | null
  prevailing_wind_deg: number | null
  headwind_pct: number | null
  tailwind_pct: number | null
  avg_clouds: number | null
  max_rain_mm: number | null
  max_snow_mm: number | null
}

export interface ActivityDetailsResponse {
  activity_id: string
  type: string | null
  date: string
  moving_time: number
  duration: string | null
  icu_training_load: number | null
  average_hr: number | null
  rpe: number | null
  // Intervals.icu native workout compliance (0-100 %, planned vs actual).
  // null when no scheduled workout matched the activity.
  compliance: number | null
  // Intervals.icu native pairing — scheduled_workouts.id of the planned event
  // this activity was matched against. Drives the «open planned workout» link.
  paired_event_id: number | null
  is_race?: boolean
  race?: RaceInfo | null
  details: ActivityDetails | null
  hrv: ActivityHRV | null
  weather: ActivityWeatherInfo | null
}

// Dashboard
export interface DashboardResponse {
  has_data: boolean
  readiness_level: string
  readiness_score: number
  hrv_last: number
  hrv_baseline: number
  sleep_score: number
  resting_hr: number
  ctl: number
  atl: number
  tsb: number
  ai_recommendation: string
}

export interface TrainingLoadSeries {
  dates: string[]
  ctl: number[]
  atl: number[]
  tsb: number[]
}

export interface ActivitiesSeries {
  activities: { date: string; sport: string; tss: number }[]
}

// Forward CTL projection from the recent 14-day ramp rate. ``projected_date``
// is filled when a date is reachable; ``reason`` explains the null cases
// (declining/flat/insufficient_data) or ``already_at_target`` for an honest
// success. ``on_track`` is the caller-friendly verdict — backend computes it
// as ``predicted_CTL_at_event_date >= target`` (linear extrapolation from
// today's CTL using the regression slope), NOT as a date comparison; this
// avoids float-rounding flips on the boundary. Null when there's no event
// date to compare against, or when the projection itself is unavailable.
export interface GoalProjection {
  ramp_per_week: number | null
  projected_date: string | null
  reason: 'insufficient_data' | 'declining' | 'flat' | 'already_at_target' | null
  on_track: boolean | null
}

export interface GoalSportProgress {
  ctl_current: number | null
  ctl_target: number
  pct: number | null
  projection: GoalProjection | null
}

// One goal's progress block — overall CTL bar always present, ``per_sport``
// only when ``per_sport_targets`` is set on the AthleteGoal (END-12 scoping
// decision — don't fake per-sport bars from a single overall target).
export interface GoalProgress {
  id: number
  category: GoalCategory
  event_name: string
  event_date: string
  sport_type: SportType
  weeks_remaining: number
  days_remaining: number
  ctl_current: number | null
  ctl_target: number | null
  overall_pct: number | null
  projection: GoalProjection | null
  per_sport?: {
    swim?: GoalSportProgress
    ride?: GoalSportProgress
    run?: GoalSportProgress
  }
}

// ``has_goals: false`` means the athlete has no active future race and the
// Goal tab is hidden; ``goals`` is then ``[]``. When true, ``goals`` holds
// one progress block per active future goal (sort: ``event_date ASC``,
// nearest first). Shape changed from single-goal to list in #323 Strand C —
// Dashboard Goal tab now mirrors Settings' all-goals view.
export interface GoalResponse {
  has_goals: boolean
  goals: GoalProgress[]
}

export interface WeeklyRecapBucket {
  week_start: string
  week_end: string
  by_sport: Record<string, { duration_sec: number; distance_m: number; tss: number }>
  ctl_start: number | null
  ctl_end: number | null
  ctl_delta: number | null
  tsb_end: number | null
}

export interface WeeklyRecapResponse {
  weeks: WeeklyRecapBucket[]
  offset: number
  today: string
  has_prev: boolean
}

// Recovery Trend (Dashboard)
export interface RecoveryTrendSeries {
  dates: string[]
  recovery: (number | null)[]
  hrv: (number | null)[]
}

// Progress / Efficiency Trends
export interface ProgressActivity {
  date: string
  id: string
  duration_min: number
  avg_hr: number | null
  ef?: number
  pace_100m?: number
  swolf?: number | null
  decoupling?: number | null
  decoupling_status?: 'green' | 'yellow' | 'red'
  np?: number | null
  pace?: number | null
  distance?: number | null
  pool_length?: number | null
}

export interface ProgressWeekly {
  week: string
  sessions: number
  ef_mean?: number | null
  pace_mean?: number | null
  swolf_mean?: number | null
  decoupling_mean?: number | null
  decoupling_median?: number | null
}

export interface ProgressTrend {
  direction: 'rising' | 'falling' | 'stable' | 'insufficient_data'
  pct: number
}

export interface ProgressMetricInfo {
  unit: string
  trend: ProgressTrend
}

export interface DecouplingTrend {
  last_n: number
  median: number
  status: 'green' | 'yellow' | 'red'
  values: number[]
  latest: {
    value: number
    status: 'green' | 'yellow' | 'red'
    date: string
    days_since: number
  }
}

export interface ProgressResponse {
  sport: string
  period: string
  data_points: number
  activities: ProgressActivity[]
  weekly?: ProgressWeekly[]
  metric?: string
  unit?: string
  trend?: ProgressTrend
  metrics?: Record<string, ProgressMetricInfo>
  decoupling_trend?: DecouplingTrend
}

// ---------------------------------------------------------------------------
// Race plan (PR2 surface — backed by api/routers/race_plan.py)
// ---------------------------------------------------------------------------

export type ConfidenceTier = 'final' | 'late' | 'mid' | 'early'

export interface PacingCorridor {
  low: string
  target: string
  cap: string
}

export interface RacePlanLeg {
  leg: string
  distance?: string
  pacing: PacingCorridor
  hr_ceiling_bpm?: number
  notes?: string
}

export interface RacePlanFueling {
  carbs_g_per_hour: number
  fluid_ml_per_hour?: number
  sodium_mg_per_hour?: number
  notes?: string
}

export interface RacePlanContingency {
  scenario: string
  plan: string
}

export interface RacePlanTransition {
  name: string
  checklist: string[]
  target_time_sec?: number
}

export interface RacePlanInner {
  headline?: string
  warmup: string
  legs: RacePlanLeg[]
  fueling: RacePlanFueling
  transitions?: RacePlanTransition[]
  contingencies: RacePlanContingency[]
}

export interface RacePlanPayload {
  plan: RacePlanInner
  // Inline race-block (spec §11.3 — accepted as snapshot for goal-deletion resilience).
  race: Record<string, unknown>
  confidence_tier: ConfidenceTier
  // generated_at / model_version are mirrored on the top-level RacePlanResponse
  // (sourced from the row columns by api/routers/race_plan.py:_format_plan_response).
  // The service writes them into payload too, but UI should read the top-level
  // fields — declare them optional here so test fixtures and any future
  // payload-only-or-top-level-only response shapes both type-check.
  generated_at?: string
  model_version?: string
  // PR2.3: tracks per-day force_regen quota (resets implicitly per UTC day).
  regen_count_today?: number
}

// Shape returned by GET /api/race-plan and POST /api/race-plan/generate.
// confidence_tier is surfaced to the top level so UI can render a badge
// without digging into payload (matches _format_plan_response in the router).
export interface RacePlanResponse {
  id: number | null
  goal_id?: number | null
  model_version: string
  generated_at?: string | null
  confidence_tier: ConfidenceTier
  payload: RacePlanPayload
  // dry_run / note / regen status surface in some responses; UI treats them as optional.
  dry_run?: boolean
  note?: string
}

// PR2.5: optional course/weather hints. Mirrors RaceConditions Pydantic model
// in api/routers/race_plan.py — both fields optional, frontend may submit one.
export interface RaceConditionsInput {
  elevation_gain_m?: number | null
  expected_temp_c?: number | null
}

// PR2.5: shape returned by GET /api/race-plan/inheritable-conditions.
// One row per past Race for the goal's sport_type, capped at 5. Some fields
// may be null when the past Race wasn't tagged with that detail.
export interface InheritableRace {
  id: number
  name: string
  date: string | null
  elevation_gain_m: number | null
  weather: string | null
}

export interface InheritableConditionsResponse {
  races: InheritableRace[]
}

// Weekly report archive (PR2/PR3 of weekly-report feature).
// `preview` is server-rendered (`tasks/actors/reports.py:extract_weekly_preview`)
// — the headline paragraph stripped of markdown markers, ≤220 chars. Detail
// view fetches the full markdown via `WeeklyReportDetail` only on click.
export interface WeeklyReportListItem {
  week_start: string  // ISO Monday, e.g. "2026-05-04"
  preview: string
  generated_at: string  // ISO timestamp
}

export interface WeeklyReportListResponse {
  items: WeeklyReportListItem[]
  // ISO Monday — pass back as `before=` for the next page. `null` means the
  // client has reached the end of history and should stop fetching.
  next_before: string | null
}

export interface WeeklyReportDetail {
  week_start: string
  content_md: string
  generated_at: string
  model: string
}

// Latest weekly changelog Discussion (PR2 of WEEKLY_CHANGELOG_SPEC). The
// sidebar fetches this on mount; 404/503 → no link rendered. Unread state
// is computed locally in the component by comparing ``url`` against
// ``localStorage["changelog.last_seen_url"]`` — see spec §10.
export interface ChangelogLatest {
  url: string
  title: string
  published_at: string  // ISO timestamp from GitHub
}

// Marathon Shape — Runalyze-style basic-endurance metric. Endpoint returns 12
// weekly buckets (newest first); the widget computes distance-specific
// required shape client-side from `distance_km ** 1.23`.
export interface MarathonShapeComponents {
  actual_weekly_km: number
  target_weekly_km: number               // raw marathon-baseline = V^1.135
  longjog_score: number
  target_longjog_km: number              // scoring-internal = ln(V/4)*12 − 13
  displayed_target_long_run_km: number   // UI = target_longjog_km + 13 = ln(V/4)*12 (Runalyze parity)
  actual_longjog_km: number
}

export interface MarathonShapeWeek {
  week_start: string
  week_end: string
  // shape_pct / vo2max_used / components are null together — vo2max gates the
  // entire week's computation (see `_vo2max_at` in api/routers/dashboard.py).
  shape_pct: number | null
  vo2max_used: number | null
  components: MarathonShapeComponents | null
}

// Phase 1.5 — ML-predicted finish time + pace per distance. Each value is
// either a full envelope or null (cold-start / below-acceptance / ML failure).
// See spec §13.
export interface MarathonShapePredicted {
  total_sec: number
  total_sec_ci_low: number
  total_sec_ci_high: number
  pace_sec_per_km: number
  pace_ci_low: number
  pace_ci_high: number
}

export interface MarathonShapeResponse {
  weeks: MarathonShapeWeek[]
  current_components: (MarathonShapeComponents & { vo2max: number }) | null
  predicted_times: Record<'10K' | 'HM' | 'Marathon', MarathonShapePredicted | null>
}
