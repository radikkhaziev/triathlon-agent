export const SPORT_ICONS: Record<string, string> = {
  Swim: '\u{1F3CA}',
  Ride: '\u{1F6B4}',
  VirtualRide: '\u{1F6B4}',
  GravelRide: '\u{1F6B4}',
  MountainBikeRide: '\u26F0\uFE0F',
  Run: '\u{1F3C3}',
  VirtualRun: '\u{1F3C3}',
  TrailRun: '\u26F0\uFE0F',
  WeightTraining: '\u{1F3CB}\uFE0F',
}

export const BIKE_TYPES = ['Ride', 'VirtualRide', 'GravelRide', 'MountainBikeRide']
export const RUN_TYPES = ['Run', 'VirtualRun', 'TrailRun']

export const WEEKDAY_RU: Record<string, string> = {
  Mon: 'Пн', Tue: 'Вт', Wed: 'Ср', Thu: 'Чт', Fri: 'Пт', Sat: 'Сб', Sun: 'Вс',
}

export const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']

export const ZONE_COLORS = ['#6b7280', '#22c55e', '#f59e0b', '#f97316', '#ef4444']
export const ZONE_LABELS = ['Z1', 'Z2', 'Z3', 'Z4', 'Z5']

export const CATEGORY_COLORS: Record<string, string> = {
  excellent: '#22c55e',
  good: '#22c55e',
  moderate: '#f59e0b',
  low: '#ef4444',
}

export const STATUS_BADGE_MAP: Record<string, { cls: string; label: string }> = {
  green: { cls: 'bg-[#22c55e20] text-green', label: 'Норма' },
  yellow: { cls: 'bg-[#f59e0b20] text-yellow', label: 'Внимание' },
  red: { cls: 'bg-[#ef444420] text-red', label: 'Снижен' },
  insufficient_data: { cls: 'bg-[#88888820] text-text-dim', label: 'Нет данных' },
}

export const CHART_COLORS = {
  ctl: 'rgb(59, 130, 246)',
  atl: 'rgb(239, 68, 68)',
  tsb: 'rgb(34, 197, 94)',
  swim: 'rgb(59, 130, 246)',
  bike: 'rgb(34, 197, 94)',
  run: 'rgb(245, 158, 11)',
}
