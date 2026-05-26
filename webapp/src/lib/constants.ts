// Halo per-sport accent (prototype `sportColor` map: Run=coral, Ride=brand
// cobalt, Swim=amber). Returns a CSS var so it composes with color-mix tints.
const SPORT_COLOR: Record<string, string> = {
  Run: 'var(--color-coral)',
  Ride: 'var(--color-brand)',
  Swim: 'var(--color-amber)',
}

export const sportColor = (sport: string | null | undefined): string =>
  SPORT_COLOR[sport || ''] || 'var(--color-ink-dim)'

// Pill tone — accent fg + a color-mix'd bg. Used by every sport pill in the
// app: Week-tab day cards (10%), Wellness Today card (10%), Activity hero
// pill (12% — stronger because the hero pill is larger). Unknown sport
// returns a neutral surface-2 tint instead of mixing with the fallback ink,
// which would render as a dirty grey wash on the pill.
export function sportTone(
  sport: string | null | undefined,
  mixPct = 10,
): { fg: string; bg: string } {
  const fg = sportColor(sport)
  if (fg === 'var(--color-ink-dim)') return { fg, bg: 'var(--color-surface-2)' }
  return { fg, bg: `color-mix(in srgb, ${fg} ${mixPct}%, transparent)` }
}

export const MONTHS: Record<string, string[]> = {
  ru: ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек'],
  en: ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'],
}

// 7-zone palette: blue (recovery) → green (aerobic) → amber (tempo) → orange
// (threshold) → red (VO2/anaerobic) → magenta (anaerobic capacity) → purple
// (neuromuscular). Ride power_zone_times comes in 7 zones; HR is usually 5
// but can be 7 on user-configured profiles, so ZoneBar walks all 7 and
// ZoneBar's modulo fallback keeps cycling colors if a future profile adds more.
export const ZONE_COLORS = ['#3b82f6', '#22c55e', '#f59e0b', '#f97316', '#ef4444', '#ec4899', '#8b5cf6']
export const ZONE_LABELS = ['Z1', 'Z2', 'Z3', 'Z4', 'Z5', 'Z6', 'Z7']

// Hex (not rgb()) so call sites can append a 2-char alpha suffix —
// e.g. `CHART_COLORS.ctl + '20'` → `'#3b82f620'` (valid 8-digit hex with
// ~12% opacity). Appending the same suffix to an `rgb(...)` string yields
// invalid CSS and Chart.js silently falls back to its default
// backgroundColor, which renders as a dark fill on dark UI.
export const CHART_COLORS = {
  ctl: '#3b82f6',
  atl: '#ef4444',
  tsb: '#22c55e',
  // Per-sport — the single canonical palette: Swim amber / Ride cobalt / Run
  // coral (design `SPORT_COLOR`, `sportColor()` above, CLAUDE.md sport-colour
  // rule). Hex (not var()) so Chart.js call sites can append a 2-char alpha
  // suffix; values mirror the `--color-amber/brand/coral` CSS tokens that the
  // var()-based call sites use. Keep all three in sync.
  swim: '#d18b00',
  ride: '#3b6dff',
  run: '#d94640',
}

// TSB zone bands — frontend is the source of truth (CLAUDE.md «Business Rules»
// references this list); `data/utils.py:tsb_zone` mirrors the same five ids.
// 5-band PMC-style banding.
export interface TsbZone {
  id: 'risk' | 'optimal' | 'gray' | 'fresh' | 'transition'
  label: string
  lo: number
  hi: number
  fill: string
  line: string
}
export const TSB_ZONES: TsbZone[] = [
  { id: 'risk', label: 'High risk', lo: -Infinity, hi: -30, fill: 'rgba(239, 68, 68, 0.10)', line: '#dc2626' },
  { id: 'optimal', label: 'Optimal', lo: -30, hi: -10, fill: 'rgba(34, 197, 94, 0.10)', line: '#16a34a' },
  { id: 'gray', label: 'Gray zone', lo: -10, hi: 5, fill: 'rgba(148, 163, 184, 0.10)', line: '#6b7280' },
  { id: 'fresh', label: 'Fresh', lo: 5, hi: 25, fill: 'rgba(59, 109, 255, 0.10)', line: '#3b6dff' },
  { id: 'transition', label: 'Transition', lo: 25, hi: Infinity, fill: 'rgba(209, 139, 0, 0.12)', line: '#d18b00' },
]
export function tsbZoneOf(v: number): TsbZone {
  for (const z of TSB_ZONES) if (v < z.hi) return z
  return TSB_ZONES[TSB_ZONES.length - 1]
}
