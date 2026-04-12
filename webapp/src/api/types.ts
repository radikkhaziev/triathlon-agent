// Auth
export interface AuthVerifyCodeResponse {
  token: string
  role: 'owner' | 'viewer'
  expires_in_days: number
}

export interface AuthMeResponse {
  role: 'owner' | 'viewer' | 'anonymous'
  authenticated: boolean
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

export interface HRVData {
  primary_algorithm: string
  flatt_esco: HRVBlock
  ai_endurance: HRVBlock
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
  quality: number | null
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
  hrv: HRVData
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

export interface SyncResponse {
  status: string
  synced_count: number
  last_synced_at: string | null
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

export interface ActivityDetailsResponse {
  activity_id: string
  type: string | null
  date: string
  moving_time: number
  duration: string | null
  icu_training_load: number | null
  average_hr: number | null
  is_race?: boolean
  details: ActivityDetails | null
  hrv: ActivityHRV | null
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
  ctl_swim: number[]
  ctl_ride: number[]
  ctl_run: number[]
}

export interface ActivitiesSeries {
  activities: { date: string; sport: string; tss: number }[]
}

export interface GoalResponse {
  event_name: string
  event_date: string
  weeks_remaining: number
  overall_pct: number
  swim_pct: number
  swim_ctl: number
  swim_target: number
  bike_pct: number
  bike_ctl: number
  bike_target: number
  run_pct: number
  run_ctl: number
  run_target: number
}

export interface WeeklySummary {
  week_start: string
  week_end: string
  by_sport: Record<string, { duration_sec: number; distance_m: number; tss: number }>
}

export interface ScheduledList {
  workouts: { date: string; sport: string; workout_name: string; planned_tss: number }[]
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
