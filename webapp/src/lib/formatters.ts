import { MONTHS } from './constants'

export function formatDate(dateStr: string): string {
  const d = new Date(dateStr + 'T00:00:00')
  const days = ['Воскресенье', 'Понедельник', 'Вторник', 'Среда', 'Четверг', 'Пятница', 'Суббота']
  return `${days[d.getDay()]}, ${d.getDate()} ${MONTHS[d.getMonth()]}`
}

export function formatWeekLabel(start: string, end: string, lang: string = 'ru'): string {
  const s = new Date(start + 'T00:00:00')
  const e = new Date(end + 'T00:00:00')
  const months = MONTHS[lang] || MONTHS.en
  const sm = months[s.getMonth()]
  const em = months[e.getMonth()]
  if (sm === em) {
    return `${sm} ${s.getDate()} \u2013 ${e.getDate()}, ${s.getFullYear()}`
  }
  return `${sm} ${s.getDate()} \u2013 ${em} ${e.getDate()}, ${e.getFullYear()}`
}

const _WEEKDAYS: Record<string, Record<string, string>> = {
  ru: { Mon: 'пн', Tue: 'вт', Wed: 'ср', Thu: 'чт', Fri: 'пт', Sat: 'сб', Sun: 'вс' },
  en: { Mon: 'Mon', Tue: 'Tue', Wed: 'Wed', Thu: 'Thu', Fri: 'Fri', Sat: 'Sat', Sun: 'Sun' },
}

const _WEEKDAYS_SHORT: Record<string, string[]> = {
  ru: ['вс', 'пн', 'вт', 'ср', 'чт', 'пт', 'сб'],
  en: ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'],
}

export function formatDayDate(dateStr: string, weekday: string, lang: string = 'ru'): string {
  const d = new Date(dateStr + 'T00:00:00')
  const wd = (_WEEKDAYS[lang] || _WEEKDAYS.en)[weekday] || weekday
  return `${wd} ${d.getDate()}`
}

export function formatDateDisplay(d: Date, lang: string = 'ru'): string {
  const days = _WEEKDAYS_SHORT[lang] || _WEEKDAYS_SHORT.en
  const months = MONTHS[lang] || MONTHS.en
  return `${days[d.getDay()]}, ${d.getDate()} ${months[d.getMonth()]} ${d.getFullYear()}`
}

export function fmtDateShort(dateStr: string, lang: string = 'ru'): string {
  const d = new Date(dateStr + 'T00:00:00')
  const months = MONTHS[lang] || MONTHS.en
  return `${months[d.getMonth()]} ${d.getDate()}, ${d.getFullYear()}`
}

const _RELATIVE: Record<string, { just_now: string; min: string; h: string; d: string }> = {
  ru: { just_now: 'только что', min: 'мин назад', h: 'ч назад', d: 'дн назад' },
  en: { just_now: 'just now', min: 'min ago', h: 'h ago', d: 'd ago' },
}

export function relativeTime(isoStr: string | null, lang: string = 'ru'): string {
  if (!isoStr) return ''
  const t = _RELATIVE[lang] || _RELATIVE.en
  const diffMin = Math.floor((Date.now() - new Date(isoStr).getTime()) / 60000)
  if (diffMin < 1) return t.just_now
  if (diffMin < 60) return `${diffMin} ${t.min}`
  const diffH = Math.floor(diffMin / 60)
  if (diffH < 24) return `${diffH} ${t.h}`
  return `${Math.floor(diffH / 24)} ${t.d}`
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
