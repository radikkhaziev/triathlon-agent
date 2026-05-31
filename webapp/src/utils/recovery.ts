/**
 * Recovery score → category → recommendation mapping.
 *
 * Source of truth: `data/metrics.py · combined_recovery_score()` +
 * `tasks/formatter.py`. This is DISPLAY logic — re-implemented on the client
 * so the chip label / rec text are derived locally, not sent by the backend.
 * Keep in sync with the backend if thresholds change.
 *
 * Ported verbatim from the Halo handoff (README §6). See
 * docs/WEBAPP_HALO_REDESIGN_SPEC.md.
 *
 * NOTE: the TSB-zone mapping is intentionally NOT here — it's an open
 * reconcile item (spec F15, Phase 6 / Dashboard·Load), boundaries still
 * disputed vs CLAUDE.md. Do not add it until F15 is closed.
 */

export type RecoveryCategory = 'excellent' | 'good' | 'moderate' | 'low'
export type RecoveryRec = 'zone2_ok' | 'zone1_long' | 'zone1_short' | 'skip'
export type RmssdStatus = 'green' | 'yellow' | 'red' | 'insufficient_data'

export function classifyRecovery(score: number): RecoveryCategory {
  // Boundaries are STRICT >. 85.0 → good; 85.1 → excellent.
  if (score > 85) return 'excellent'
  if (score > 70) return 'good'
  if (score > 40) return 'moderate'
  return 'low'
}

/**
 * Period-summary statistics for the «что это значит» card on
 * `/wellness/recovery`. Computed frontend-side from the existing
 * `RecoveryTrendSeries` array — no new backend endpoint.
 *
 *  - `days` — count of non-null recovery scores in the period (denominator).
 *  - `goodPct` — share of those days with score > 70 (good + excellent zone).
 *  - `lowPct`  — share of those days with score ≤ 40 (low / red zone).
 *  - `avg`     — arithmetic mean of non-null scores, rounded to int.
 *  - `todayCategory` — last non-null score's category, drives template choice.
 *
 * Returns null when the period is entirely empty (cold-start / first day
 * after onboarding) so the caller can hide the card.
 */
export interface RecoveryMeaningStat {
  days: number
  goodPct: number
  lowPct: number
  avg: number
  todayCategory: RecoveryCategory | null
}

export function computeRecoveryMeaningStat(series: readonly (number | null)[]): RecoveryMeaningStat | null {
  let total = 0
  let good = 0
  let low = 0
  let sum = 0
  for (const v of series) {
    if (v == null) continue
    total += 1
    sum += v
    if (v > 70) good += 1
    if (v <= 40) low += 1
  }
  if (total === 0) return null
  // STRICT: «today» is the LAST array element specifically, not the latest
  // non-null. If the wellness sync hasn't landed today's row yet, the meaning
  // card shows the period summary with `no_today` copy instead of a 2-day-old
  // category passed off as «сегодня».
  const todayVal = series.length > 0 ? series[series.length - 1] : null
  return {
    days: total,
    goodPct: Math.round((good / total) * 100),
    lowPct: Math.round((low / total) * 100),
    avg: Math.round(sum / total),
    todayCategory: todayVal != null ? classifyRecovery(todayVal) : null,
  }
}

/**
 * Sleep score 4-category classifier — finalised by the Halo "Sleep trend"
 * design (`design-package/endurai/direction-b-halo.jsx` `SLEEP_SCORE_ZONES`).
 *
 * Boundaries `<50 poor / 50-69 fair / 70-89 good / ≥90 excellent` (the
 * 2026-05-20 provisional Garmin/Whoop banding — confirmed by the designer's
 * Sleep-trend legend, no longer provisional).
 *
 * Backend (Intervals.icu wellness payload) категории sleep_score не
 * определяет — raw 0-100 number, вес 0.20 в `recovery_score` weighted sum
 * (см. `data/metrics.py`). Эта функция + {@link SLEEP_ZONE} — **frontend-only
 * source of truth** для Sleep bar-strip (Wellness) + Sleep-trend экрана.
 *
 * Boundaries STRICT ≥/<: 49.x → poor; 50.0 → fair; 89.x → good; 90.0 →
 * excellent.
 */
export type SleepCategory = 'excellent' | 'good' | 'fair' | 'poor'

export function classifySleep(score: number): SleepCategory {
  if (score >= 90) return 'excellent'
  if (score >= 70) return 'good'
  if (score >= 50) return 'fair'
  return 'poor'
}

export interface SleepZone {
  id: SleepCategory
  label: string
  /** Inclusive lower score bound (`poor` floors at 0). */
  lo: number
  /** Exclusive upper score bound (`excellent` opens upward → `Infinity`). */
  hi: number
  /** Solid colour — line / bar / chip text. */
  line: string
  /** Translucent colour — score-chart zone band / chip background. */
  fill: string
}

/**
 * The four sleep zones, ascending (poor → excellent) — direct port of the
 * design's `SLEEP_SCORE_ZONES`. Ascending order mirrors the zone-band stack
 * on the Sleep-trend score chart (poor at the bottom); `lo`/`hi` are
 * contiguous (`zone.lo === prevZone.hi`) so consumers never re-derive a
 * bound from a neighbour. Single source of truth for every sleep colour: the
 * Wellness 7-night bars, the Sleep-trend duration bars (coloured by score
 * zone) and the score chart's bands + legend.
 */
export const SLEEP_ZONES: SleepZone[] = [
  { id: 'poor', label: 'Poor', lo: 0, hi: 50, line: '#dc2626', fill: 'rgba(239, 68, 68, 0.10)' },
  { id: 'fair', label: 'Fair', lo: 50, hi: 70, line: '#d18b00', fill: 'rgba(209, 139, 0, 0.10)' },
  { id: 'good', label: 'Good', lo: 70, hi: 90, line: '#3b6dff', fill: 'rgba(59, 109, 255, 0.10)' },
  { id: 'excellent', label: 'Excellent', lo: 90, hi: Infinity, line: '#16a34a', fill: 'rgba(34, 197, 94, 0.12)' },
]

export const SLEEP_ZONE: Record<SleepCategory, SleepZone> = Object.fromEntries(
  SLEEP_ZONES.map(z => [z.id, z]),
) as Record<SleepCategory, SleepZone>

/** Sleep zone for a raw 0-100 score. */
export function sleepZoneOf(score: number): SleepZone {
  return SLEEP_ZONE[classifySleep(score)]
}

export function recommendTraining(
  category: RecoveryCategory,
  rmssd: RmssdStatus,
): RecoveryRec {
  // Override: rmssd red → skip regardless of category.
  if (rmssd === 'red') return 'skip'
  if (category === 'excellent' || category === 'good') return 'zone2_ok'
  if (category === 'moderate') return 'zone1_long'
  return 'zone1_short'
}

// Per-category chip — both languages.
export const RECOVERY_CHIP = {
  en: {
    excellent: { emoji: '🟢', label: 'EXCELLENT RECOVERY' },
    good: { emoji: '🟢', label: 'READY TO TRAIN' },
    moderate: { emoji: '🟡', label: 'MODERATE LOAD' },
    low: { emoji: '🔴', label: 'REST RECOMMENDED' },
  },
  ru: {
    excellent: { emoji: '🟢', label: 'ОТЛИЧНОЕ ВОССТАНОВЛЕНИЕ' },
    good: { emoji: '🟢', label: 'ГОТОВ К НАГРУЗКЕ' },
    moderate: { emoji: '🟡', label: 'УМЕРЕННАЯ НАГРУЗКА' },
    low: { emoji: '🔴', label: 'РЕКОМЕНДОВАН ОТДЫХ' },
  },
} as const

export const RECOVERY_REC_COPY = {
  en: {
    zone2_ok: 'Train in Z2 — full volume',
    zone1_long: 'Aerobic base only — Z1–Z2',
    zone1_short: 'Light activity, 30–45 min',
    skip: 'Rest day — no training',
  },
  ru: {
    zone2_ok: 'Тренируйся в Z2 — полный объём',
    zone1_long: 'Только аэробная база — Z1–Z2',
    zone1_short: 'Лёгкая активность, 30–45 мин',
    skip: 'День отдыха — без тренировки',
  },
} as const

/**
 * Three gotchas (README §6):
 *  1. Strict `>` boundaries — edge-case tooltips ("need X more pts for
 *     `good`") rely on this.
 *  2. `rmssd === 'red'` overrides the recommendation to `skip` regardless
 *     of the computed score (score 90 + red rmssd → skip-day notice). The
 *     Wellness card must not contradict it.
 *  3. `insufficient_data` (<14 days of HRV) is its own status with ⚪ — NOT
 *     a green/yellow/red dot. Recovery score isn't reliable yet.
 */

export const STATUS_EMOJI: Record<RmssdStatus, string> = {
  green: '🟢',
  yellow: '🟡',
  red: '🔴',
  insufficient_data: '⚪',
}

// STATUS_COLOR / RMSSD_TONE / StatusTone / rmssdToTone — удалены: ноль
// внешних потребителей после Halo-порта (screens тинтят через
// `var(--color-status-*)` напрямую). README §6 контракт остаётся в коде через
// `STATUS_EMOJI` + inline CSS-vars; «kept for documentation» антипаттерн
// устранён (Halo-v3 holistic-review M2).
