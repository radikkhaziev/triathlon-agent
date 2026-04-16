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

export const ZONE_COLORS = ['#6b7280', '#22c55e', '#f59e0b', '#f97316', '#ef4444']
export const ZONE_LABELS = ['Z1', 'Z2', 'Z3', 'Z4', 'Z5']

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

export const CHART_COLORS = {
  ctl: 'rgb(59, 130, 246)',
  atl: 'rgb(239, 68, 68)',
  tsb: 'rgb(34, 197, 94)',
  swim: 'rgb(59, 130, 246)',
  ride: 'rgb(34, 197, 94)',
  run: 'rgb(245, 158, 11)',
}
