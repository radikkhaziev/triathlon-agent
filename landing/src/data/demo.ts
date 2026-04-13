export const DEMO_RECOVERY = {
  score: 78,
  category: 'good' as const,
}

export const DEMO_HRV = {
  status: 'green' as const,
  today: 62.3,
  mean_7d: 58.1,
  delta_pct: 7.2,
}

export const DEMO_RHR = {
  status: 'green' as const,
  today: 52,
  mean_30d: 53,
}

export const DEMO_TRAINING_LOAD = {
  ctl: 52,
  atl: 48,
  tsb: 4.2,
  sport_ctl: { swim: 42, bike: 58, run: 51 },
}

export const DEMO_AI_TEXT_RU =
  'Сегодня хороший день для Zone 2 велотренировки. HRV стабильно выше среднего три дня подряд, recovery 78/100. Рекомендую: 90 мин на станке, пульс 125-135.'

export const DEMO_AI_TEXT_EN =
  "Today's a solid day for a Zone 2 bike session. HRV has been consistently above baseline for three days, recovery 78/100. Suggest: 90 min on the trainer, HR 125-135."
