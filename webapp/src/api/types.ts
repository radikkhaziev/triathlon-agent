// Auth
export interface IntervalsStatus {
  athlete_id: string | null
  scope: string | null
  // True iff an OAuth access_token is stored — false post-disconnect/revoke
  // even when athlete_id lingers. UI uses this to pick "Connected" vs
  // "Reconnect" panel.
  connected: boolean
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

// Our-measured ramp-test threshold (DFA α1) for one sport — distinct from the
// Intervals.icu-synced values in AuthMeResponse.profile. Served by
// GET /api/athlete/measured-thresholds. HRVT2 ≈ LTHR/FTP, confidence = R² tier.
export interface MeasuredThreshold {
  sport: 'Run' | 'Ride'
  measured_at: string // ISO date the threshold was detected on
  activity_id: string
  hrvt2_hr: number | null
  hrvt2_power: number | null // W (Ride)
  hrvt2_confidence: 'high' | 'medium' | 'low' | null
}

export interface MeasuredThresholdsResponse {
  thresholds: MeasuredThreshold[]
}

export type SportTag = 'swim' | 'ride' | 'run'

export interface AuthMeResponse {
  role: 'owner' | 'viewer' | 'demo' | 'anonymous'
  authenticated: boolean
  language?: string
  // Telegram identity of the authenticated user (first+last → display_name).
  // null for legacy rows created before the column / via CLI.
  display_name?: string | null
  username?: string | null
  // Public URL of the cached Telegram avatar — null when the user has no
  // photo, hid it via privacy settings, or the morning-report refresh
  // hasn't run yet for this account. UI falls back to initials when null.
  avatar_url?: string | null
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
}

// HRV
export interface HRVTrend {
  direction: string | null
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
  swc_verdict: string | null
  cv_7d: number | null
  cv_verdict: string | null
  trend: HRVTrend | null
  // Consecutive days (including today) where wellness.hrv > rmssd_60d. Server-
  // computed over the last 7 days; backs the «N утра подряд» prefix in `meaning`
  // when status='green'. 0 for any other status / cold-start.
  streak_above_baseline?: number
  // Pre-localized one-sentence interpretation for the «what this means» card
  // on `/wellness/:metric`. Rule-based (status × streak), NOT AI — see
  // `api/routers/wellness.py:_hrv_meaning`. Always rendered verbatim.
  meaning: string | null
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
  trend: HRVTrend | null
  // Consecutive days where rhr_today < rhr_30d (RHR is inverted: lower = better).
  streak_below_baseline?: number
  // Pre-localized one-sentence interpretation, same contract as HRV.meaning.
  meaning: string | null
}

// Sleep
export interface SleepData {
  score: number | null
  duration: string | null
  duration_secs: number | null
  // Last 7 nights' sleep scores, oldest → newest (target day = last item).
  // `null` slots = missing wellness row for that day (sync gap, cold start).
  last_7_nights: (number | null)[]
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
  banister_recovery: number | null
}

// Wellness / Report response — discriminated union on `has_data`. The backend
// (`api/routers/wellness.py`) omits every metric block when the day's wellness
// row hasn't synced yet, so a flat interface would be lying about which fields
// are present. Splitting on `has_data` lets `data.has_data` narrow the type:
// inside a `data.has_data` guard the metric blocks are guaranteed.

// Nav / identity fields present regardless of `has_data`. `is_today` /
// `has_prev` / `has_next` come only from `/api/wellness-day` (not `/api/report`),
// hence optional.
interface WellnessResponseBase {
  date: string
  is_today?: boolean
  has_prev?: boolean
  has_next?: boolean
}

// `has_data: false` — the wellness row for this day hasn't synced. Only the
// nav/identity fields exist; every metric block is absent.
export interface WellnessResponseEmpty extends WellnessResponseBase {
  has_data: false
}

// `has_data: true` — full metric payload (`_build_wellness_response`).
export interface WellnessResponseData extends WellnessResponseBase {
  has_data: true
  recovery: RecoveryData
  hrv: HRVBlock
  rhr: RHRBlock
  sleep: SleepData
  training_load: TrainingLoadData
  body: BodyData
  stress: StressData
  ai_recommendation: string | null
  // Demo sessions get the AI text stubbed server-side; the UI renders a
  // canned English sample instead (docs/DEMO_PUBLIC_ACCESS_SPEC.md Phase 2).
  demo_stub?: boolean
  updated_at?: string | null
}

export type WellnessResponse = WellnessResponseEmpty | WellnessResponseData

// Scheduled Workouts
export interface ScheduledWorkout {
  id: number
  type: string | null
  name: string | null
  duration: string | null
  duration_secs: number | null
  distance_km: number | null
  description: string | null
  // Planned TSS from Intervals.icu enrichment. NULL until the event is
  // enriched (fresh HumanGo events arrive un-enriched). Drives Plan vs
  // Actual TSS roll-up on the Week tab.
  icu_training_load: number | null
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
  paired_activity: {
    id: number
    type: string | null
    duration: string | null
  } | null
}

export interface ScheduledWorkoutsDay {
  date: string
  weekday: string
  workouts: ScheduledWorkout[]
}

export interface ScheduledWorkoutsResponse {
  week_start: string
  week_end: string
  today: string
  has_prev: boolean
  has_next: boolean
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
  // Intervals.icu planned-vs-actual compliance (0-100 %). NULL when the
  // activity wasn't paired with a planned workout.
  compliance: number | null
  // Intervals' native pairing — FK-less reference to `scheduled_workouts.id`.
  // NULL when the activity wasn't paired. Drives the Week-tab merge logic so
  // a planned session covered by an actual doesn't render twice.
  paired_event_id: number | null
}

export interface ActivitiesDay {
  date: string
  weekday: string
  activities: ActivityItem[]
}

export interface ActivitiesWeekResponse {
  today: string
  has_prev: boolean
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
}

export interface RaceInfo {
  name: string
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
  race_day_tsb: number | null
  race_day_recovery_score: number | null
  race_day_hrv_status: string | null
}

// Outdoor weather block — from `ACTIVITY_UPLOADED` webhook when
// `dto.has_weather=True`. Indoor / virtual rides return `weather: null`.
export interface ActivityWeatherInfo {
  avg_temp_c: number | null
  avg_feels_like_c: number | null
  avg_wind_speed_mps: number | null
  prevailing_wind_deg: number | null
  headwind_pct: number | null
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
  // Resolved paired workout — name + planned duration + planned TSS — used
  // for the «PLAN | <name> ›» breadcrumb pill on the Activity detail screen
  // and the Plan vs Actual mini-table. NULL when no pairing.
  paired_workout: {
    id: number
    name: string | null
    duration_secs: number | null
    icu_training_load: number | null
  } | null
  is_race?: boolean
  race?: RaceInfo | null
  // "This session vs your norm" — deterministic markers against a pool of
  // similar past sessions (same sport, duration ±30%, IF ±12, 120d). Computed
  // server-side (`compute_activity_comparison`). `available=false` (thin/no
  // pool, unsupported sport) → the block is hidden.
  comparison?: ActivityComparison | null
  details: ActivityDetails | null
  hrv: ActivityHRV | null
  weather: ActivityWeatherInfo | null
}

export interface ActivityComparisonMarker {
  // efficiency/intensity marker compared against the athlete's own median
  key: 'decoupling' | 'ef' | 'pace' | 'np' | 'avg_hr' | 'vi'
  value: number
  norm_median: number
  pool_n: number
  delta: number
  // verdict vs norm — 'neutral' both for no-verdict markers (avg_hr, vi) and
  // for verdict markers landing within 5% of the norm
  band: 'better' | 'worse' | 'neutral'
  // present only for `decoupling` — its own traffic-light status
  status?: string
}

export interface ActivityComparison {
  available: boolean
  pool_n: number
  reason?: string
  // pool lookback window in days — server-driven so the «{n} за {days} дн.»
  // header can't drift from the backend's `_CMP_WINDOW_DAYS`
  window_days?: number
  markers?: ActivityComparisonMarker[]
}

export interface TrainingLoadSeries {
  dates: string[]
  // ISO date marking the actual/forecast split. Values at indices where
  // `dates[i] <= today_date` are actual; later indices are computed-on-read
  // forward projections (same EMA model for overall and per-sport).
  today_date: string
  // Overall ctl/atl/tsb are projected forward past `today_date` so the TSB
  // Form line continues into the forecast region (otherwise the chart goes
  // blank at today). Null only when there's no past anchor to extrapolate
  // from (brand-new account). See docs/PER_SPORT_LOAD_SPEC.md decision #12.
  ctl: (number | null)[]
  atl: (number | null)[]
  tsb: (number | null)[]
  // Per-discipline CTL+ATL trend (Wellness "Training load" detail by-sport
  // breakdown) — null on days a sport has no value. Past segment is parsed
  // from wellness.sport_info; future segment is plan-aware forecast (see
  // docs/PER_SPORT_LOAD_SPEC.md Step 3.5).
  ctl_swim: (number | null)[]
  ctl_ride: (number | null)[]
  ctl_run: (number | null)[]
  atl_swim: (number | null)[]
  atl_ride: (number | null)[]
  atl_run: (number | null)[]
}

export interface ActivitiesSeries {
  activities: { date: string; sport: string; tss: number }[]
  // Future scheduled workouts (strictly past today), same {date, sport, tss}
  // shape — drives forecast bars in the daily-TSS chart. Empty when the user
  // has no plan.
  planned: { date: string; sport: string; tss: number }[]
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
  event_name: string
  event_date: string
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

// Recovery Trend — Dashboard Load tab chart + Wellness "Recovery trend" detail.
export interface RecoveryTrendSeries {
  dates: string[]
  recovery: (number | null)[]
  hrv: (number | null)[]
  rhr: (number | null)[]
}

// Sleep Trend — Wellness "Sleep trend" detail screen. `duration_min` is whole
// minutes; `score` is the raw 0-100 sleep score.
export interface SleepTrendSeries {
  dates: string[]
  duration_min: (number | null)[]
  score: (number | null)[]
}

// Body Trend — Wellness "Body trend" detail screen.
export interface BodyTrendSeries {
  dates: string[]
  weight: (number | null)[]
  body_fat: (number | null)[]
  vo2max: (number | null)[]
  steps: (number | null)[]
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

// Both fields optional: the demo stub returns `payload: {}` (see
// GET /api/race-plan demo branch), and the renderer already treats every
// section as absent-able (JSONB has no schema enforcement).
export interface RacePlanPayload {
  plan?: RacePlanInner
  confidence_tier?: ConfidenceTier
}

// Shape returned by GET /api/race-plan and POST /api/race-plan/generate.
// confidence_tier is surfaced to the top level so UI can render a badge
// without digging into payload (matches _format_plan_response in the router).
export interface RacePlanResponse {
  model_version: string
  generated_at?: string | null
  confidence_tier: ConfidenceTier
  payload: RacePlanPayload
  // note surfaces on regenerate / cached responses; UI renders it as a hint.
  note?: string
  // Demo sessions get `payload: {}` + this flag (server-side stub). The panel
  // skips the fetch for demo anyway — this is the wire contract for tests.
  demo_stub?: boolean
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

// Weekly report archive (PR2/PR3 of weekly-report feature). Backs both the
// `/weekly` list page and the Dashboard Recap tab.
// `headline` is the report's leading `# ` H1 (`extract_weekly_headline`) —
// `null` for legacy reports written before the prompt change; the card falls
// back to `preview` (`extract_weekly_preview` — first body paragraph stripped
// of markdown, ≤220 chars). `by_sport` + the CTL/ramp/TSB fields are that
// week's training volume + load bookends, so the Recap card renders without a
// second round-trip. Detail view fetches full markdown via `WeeklyReportDetail`.
export interface WeeklyReportListItem {
  week_start: string  // ISO Monday, e.g. "2026-05-04"
  headline: string | null
  preview: string
  generated_at: string  // ISO timestamp
  by_sport: Record<string, { duration_sec: number; distance_m: number; tss: number }>
  ctl_start: number | null
  ctl_end: number | null
  ctl_delta: number | null
  ramp: number | null
  tsb_end: number | null
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
  displayed_target_long_run_km: number   // UI = ln(V/4)*12 (Runalyze parity)
  actual_longjog_km: number
}

export interface MarathonShapeWeek {
  week_start: string
  week_end: string
  // null when `wellness.vo2max` is missing for week_end — gates the entire
  // week's computation (see `_vo2max_at` in api/routers/dashboard.py).
  shape_pct: number | null
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
  // Longest Run-race distance (meters) in the user's history. Null if no Run
  // races logged. Used by the widget to flag ML predictions that extrapolate
  // outside the training distribution — XGBoost can't extrapolate beyond seen
  // distances, so e.g. Marathon prediction is unreliable for users with no
  // races > 30 km.
  max_run_race_distance_m: number | null
}

// Bike Readiness — 3-signal (Volume / Long ride / Durability) bike-leg
// readiness, no synthetic Bike Shape %. Endpoint returns 12 weekly CTL_bike
// snapshots + current 3-signal envelope; the widget computes
// distance-specific targets (Olympic / 70.3 / IM) and the traffic-light
// verdict client-side from the empirical target table (spec §3.1).
export interface BikeReadinessWeek {
  week_start: string
  week_end: string
  // null when `wellness.sport_info` is missing for week_end and the 7-day
  // back-walk also yields nothing (e.g. fresh user, backfill gap).
  ctl_bike: number | null
}

export interface BikeReadinessComponents {
  ctl_bike: number | null               // newest week's CTL_bike — Volume signal
  longest_ride_hours: number | null     // max moving_time over last 28d, hours
  longest_ride_date: string | null      // "YYYY-MM-DD" of the longest ride
  decoupling_median_pct: number | null  // median of last 5 valid bike rides over 84d
  decoupling_status: 'green' | 'yellow' | 'red' | null
  decoupling_n: number                  // count of valid rides used (0 = insufficient)
  // Signed % EF change over the 84-day window. >0 = improving aerobic fitness.
  // null when fewer than 2 weekly samples landed (insufficient_data).
  ef_trend_pct: number | null
}

export interface BikeReadinessResponse {
  weeks: BikeReadinessWeek[]
  current_components: BikeReadinessComponents
}

// Endurance Score — composite endurance state across all sports.
// See docs/ENDURANCE_SCORE_SPEC.md. Phase 1: weekly trend on-the-fly, no
// daily snapshots. Frontend toggles a detail view via local state.
export type EnduranceZoneId =
  | 'detrained'
  | 'recovering'
  | 'maintaining'
  | 'productive'
  | 'peaking'

export interface EnduranceComponents {
  base: number
  long_term: number
  recent: number
  duration: number
  consistency: number
  recovery: number
}

export interface EnduranceSportShare {
  name: 'Bike' | 'Run' | 'Swim' | 'Other'
  pct: number
  sub_score: number | null
}

export interface EnduranceBadge {
  id: 'new_zone' | 'best_90d' | 'top_10_percentile' | 'in_form_3m'
  label: string
  icon: string
}

export interface EnduranceCurrent {
  score: number
  zone: EnduranceZoneId
  vo2max_composite: number
  components: EnduranceComponents
  per_sport: EnduranceSportShare[]
  delta_vs_week_ago: number
  badge: EnduranceBadge | null
  insufficient_data: boolean
}

export interface EnduranceTrendPoint {
  date: string  // "YYYY-MM-DD" daily snapshot
  score: number
  zone: EnduranceZoneId
}

export type EndurancePeriod = '1m' | '3m' | '6m' | '1y'

export interface EnduranceScoreResponse {
  current: EnduranceCurrent
  trend: EnduranceTrendPoint[]
  period: EndurancePeriod
}

// ─── Training Strain (Foster monotony/strain + ACWR) ──────────────────────

export type StrainZoneId = 'calm' | 'building' | 'overload'
export type AcwrStatus = 'low' | 'sweet' | 'caution' | 'danger'
export type StrainBandSource = 'percentile' | 'monotony_fallback'

export interface StrainBands {
  calm_max: number
  hard_min: number
  source: StrainBandSource
}

export interface TrainingStrainCurrent {
  strain: number
  monotony: number
  weekly_load: number
  weekly_load_delta: number
  acwr: number | null
  acwr_status: AcwrStatus | null
  zone: StrainZoneId
  bands: StrainBands
  insufficient_data: boolean
}

export interface StrainDailyLoad {
  date: string  // "YYYY-MM-DD"
  tss: number
}

export interface StrainTrendPoint {
  date: string  // "YYYY-MM-DD"
  strain: number
  monotony: number
  weekly_load: number
}

export interface TrainingStrainResponse {
  current: TrainingStrainCurrent
  daily_load_7d: StrainDailyLoad[]
  trend: StrainTrendPoint[]
  period: EndurancePeriod
}
