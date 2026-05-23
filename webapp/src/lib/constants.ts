// Halo per-sport accent (prototype `sportColor` map: Run=coral, Ride=brand
// cobalt, Swim=amber). Returns a CSS var so it composes with color-mix tints.
const SPORT_COLOR: Record<string, string> = {
  Run: 'var(--color-coral)',
  Ride: 'var(--color-brand)',
  Swim: 'var(--color-amber)',
}

export const sportColor = (sport: string | null | undefined): string =>
  SPORT_COLOR[sport || ''] || 'var(--color-ink-dim)'

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
