import { MONTHS, WEEKDAY_RU } from './constants'

export function formatDate(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00')
  const days = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота']
  return `${days[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`
}

export function formatWeekLabel(start: string, end: string): string {
  const s = new Date(start + 'T00:00:00')
  const e = new Date(end + 'T00:00:00')
  const sm = MONTHS[s.getMonth()]
  const em = MONTHS[e.getMonth()]
  if (sm === em) {
    return `${sm} ${s.getDate()} \u2013 ${e.getDate()}, ${s.getFullYear()}`
  }
  return `${sm} ${s.getDate()} \u2013 ${em} ${e.getDate()}, ${e.getFullYear()}`
}

export function formatDayDate(dateStr: string, weekday: string): string {
  const d = new Date(dateStr + 'T00:00:00')
  return `${WEEKDAY_RU[weekday] || weekday} ${d.getDate()}`
}

export function formatDateDisplay(d: Date): string {
  const days = ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб']
  return `${days[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]} ${d.getFullYear()}`
}

export function fmtDateShort(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00')
  return `${MONTHS[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`
}

export function relativeTime(isoStr: string | null): string {
  if (!isoStr) return ''
  const diffMin = Math.floor((Date.now() - new Date(isoStr).getTime()) / 60000)
  if (diffMin < 1) return 'только что'
  if (diffMin < 60) return `${diffMin} мин назад`
  const diffH = Math.floor(diffMin / 60)
  if (diffH < 24) return `${diffH} ч назад`
  return `${Math.floor(diffH / 24)} дн назад`
}

export function num(v: number | null | undefined, decimals = 1): string {
  if (v == null) return '--'
  return Number(v).toFixed(decimals)
}

export function fmtPace(secPerKm: number | null): string | null {
  if (!secPerKm || secPerKm <= 0) return null
  const m = Math.floor(secPerKm / 60)
  const s = Math.round(secPerKm % 60)
  return `${m}:${String(s).padStart(2, '0')}`
}

/**
 * Intervals.icu's `pace` / `gap` fields arrive with an ambiguous unit —
 * sometimes seconds-per-km (as the name suggests), sometimes meters-per-second
 * (same value as `average_speed`). See issue #44 for the observed case where
 * a 1.98 m/s run rendered as `0:02/km` because the field was treated as
 * already being sec/km.
 *
 * We disambiguate by magnitude using a range that has no overlap for humans:
 *   - real running speed tops out at ~12 m/s (world sprint records)
 *   - real running pace is ~95 sec/km at best (sprint) and ~500 sec/km typical
 *
 * Anything below PACE_UNIT_THRESHOLD is treated as m/s and converted;
 * anything at or above is treated as already-normalized sec/km.
 *
 * Prefer deriving pace directly from `moving_time / distance` when those
 * fields are available — this function exists for values like `gap`
 * (grade-adjusted pace) that cannot be trivially recomputed on the client.
 */
const PACE_UNIT_THRESHOLD_SEC_PER_KM = 30

export function normalizePaceSecPerKm(value: number | null | undefined): number | null {
  if (!value || value <= 0) return null
  if (value < PACE_UNIT_THRESHOLD_SEC_PER_KM) return 1000 / value
  return value
}

export function fmtSpeed(ms: number | null): string | null {
  if (!ms || ms <= 0) return null
  return (ms * 3.6).toFixed(1)
}

export function fmtDuration(secs: number | null | undefined): string {
  if (!secs) return '-'
  secs = Math.round(secs)
  const h = Math.floor(secs / 3600)
  const m = Math.floor((secs % 3600) / 60)
  const s = secs % 60
  if (h) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

export function sportLabel(type: string | null): string {
  if (!type) return 'Activity'
  return type.replace(/([A-Z])/g, ' $1').trim()
}

export function stripWorkoutPrefix(name: string | null): string {
  if (!name) return 'Тренировка'
  return name.replace(/^[A-Z]+:/, '').trim() || name
}

export function fmtDateYmd(d: Date): string {
  return d.getFullYear() + '-' +
    String(d.getMonth() + 1).padStart(2, '0') + '-' +
    String(d.getDate()).padStart(2, '0')
}
