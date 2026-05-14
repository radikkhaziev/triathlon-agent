export const SPORT_ICONS: Record<string, string> = {
  Swim: '\u{1F3CA}',
  Ride: '\u{1F6B4}',
  Run: '\u{1F3C3}',
  Other: '\u{1F3CB}\uFE0F',
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

export const CATEGORY_COLORS: Record<string, string> = {
  excellent: '#22c55e',
  good: '#22c55e',
  moderate: '#f59e0b',
  low: '#ef4444',
}

export const STATUS_BADGE_MAP: Record<string, { cls: string; labelKey: string }> = {
  green: { cls: 'bg-[#22c55e20] text-green', labelKey: 'status.green' },
  yellow: { cls: 'bg-[#f59e0b20] text-yellow', labelKey: 'status.yellow' },
  red: { cls: 'bg-[#ef444420] text-red', labelKey: 'status.red' },
  insufficient_data: { cls: 'bg-[#88888820] text-text-dim', labelKey: 'status.insufficient_data' },
}

// Hex (not rgb()) so call sites can append a 2-char alpha suffix —
// e.g. `CHART_COLORS.ctl + '20'` → `'#3b82f620'` (valid 8-digit hex with
// ~12% opacity). Appending the same suffix to an `rgb(...)` string yields
// invalid CSS and Chart.js silently falls back to its default
// backgroundColor, which renders as a dark fill on dark UI.
export const CHART_COLORS = {
  ctl: '#3b82f6',
  atl: '#ef4444',
  tsb: '#22c55e',
  swim: '#3b82f6',
  ride: '#22c55e',
  run: '#f59e0b',
}

// TSB zone hex palette — solid (not rgba) because we render these as inline
// SVG/text fills, not Chart.js datasets. Bands match `data/utils.py:tsb_zone`:
// >+10 under, -10..+10 optimal, -25..-10 productive, <-25 risk.
export const TSB_ZONE_COLORS = {
  under: '#3b82f6',
  optimal: '#22c55e',
  productive: '#f59e0b',
  risk: '#ef4444',
} as const
