export type Lang = 'ru' | 'en'

const dict = {
  ru: {
    hero_tagline: 'Recovery score, HRV-анализ и AI-рекомендация — каждое утро в Telegram',
    hero_cta_preview: 'Смотреть демо',
    hero_cta_telegram: 'Открыть в Telegram',
    hero_subline: 'Прокрути вниз — покажу пример утреннего отчёта',

    preview_title: 'Как выглядит утро атлета',
    preview_subtitle: 'Пример утреннего отчёта',
    preview_recovery: 'Recovery',
    preview_good: 'Хорошее восстановление',
    preview_zone2: 'Зона 2 — полный объём',
    preview_hrv: 'HRV',
    preview_rhr: 'Пульс покоя',
    preview_tsb: 'TSB',
    preview_tsb_status: 'Оптимально',
    preview_ctl: 'Нагрузка по видам спорта',
    preview_ai_title: 'Рекомендация от AI',
    preview_cta_login: 'Хочешь видеть свои данные? Войти',

    features_title: 'Что внутри',
    features_morning_title: 'Утренний отчёт',
    features_morning_body: 'Recovery score, HRV, RHR, TSB и AI-рекомендация приходят в Telegram каждое утро.',
    features_load_title: 'Тренировочная нагрузка',
    features_load_body: 'CTL, ATL, TSB от Intervals.icu + per-sport разбивка. Тренд за 30 дней.',
    features_dfa_title: 'DFA α1 анализ',
    features_dfa_body: 'HRVT1/HRVT2 пороги, Readiness (Ra) и Durability (Da) после каждой тренировки.',
    features_race_title: 'Прогресс к гонке',
    features_race_body: 'Целевой CTL к дате старта, ramp rate, авто-мониторинг гонок.',
    features_ai_title: 'Claude AI',
    features_ai_body: 'Claude Sonnet анализирует все метрики и корректирует план с учётом данных.',
    features_evening_title: 'Вечерний дайджест',
    features_evening_body: 'Итог дня, тренировка vs план, план на завтра — в 21:00.',

    howitworks_title: 'Как это работает',
    howitworks_step1: 'Система собирает wellness, HRV и данные о тренировках',
    howitworks_step2: 'Recovery engine считает метрики, статусы и тренды',
    howitworks_step3: 'Claude анализирует всё через MCP и формирует рекомендацию',
    howitworks_step4: 'Утренний отчёт приходит в Telegram',

    deepdive_title: 'Утренний отчёт — разбор',
    deepdive_recovery_title: 'Recovery Analysis',
    deepdive_recovery_body:
      'Взвешенная комбинация четырёх сигналов: HRV, тренировочная нагрузка, пульс покоя, сон.',
    deepdive_hrv_title: 'Двойной HRV-алгоритм',
    deepdive_hrv_body:
      'Один алгоритм реагирует быстро на дневные колебания. Второй ловит хроническую усталость через недельные тренды. Вместе — устойчивая оценка восстановления.',
    deepdive_hrv_fast: 'Быстрый отклик',
    deepdive_hrv_chronic: 'Хронический тренд',
    deepdive_ai_title: 'AI-рекомендация',
    deepdive_ai_body:
      'Claude Sonnet анализирует все метрики через MCP-протокол и выдаёт конкретную рекомендацию — интенсивность, зону, длительность.',
    deepdive_ai_thinking: 'Claude думает',

    opensource_title: 'Open Source',
    opensource_body: 'Проект открыт на GitHub. Репозиторий, код, история миграций — всё публично.',
    opensource_metric_tools: 'MCP-инструментов',
    opensource_cta: 'Смотреть на GitHub',

    footer_built_by: 'Сделано',
    footer_github: 'GitHub',
    footer_telegram: 'Telegram',
  },
  en: {
    hero_tagline: 'Recovery score, HRV analysis and AI recommendations — every morning in Telegram',
    hero_cta_preview: 'See it in action',
    hero_cta_telegram: 'Open in Telegram',
    hero_subline: 'Scroll down to see a sample morning report',

    preview_title: 'Your morning at a glance',
    preview_subtitle: 'Sample morning report',
    preview_recovery: 'Recovery',
    preview_good: 'Good recovery',
    preview_zone2: 'Zone 2 OK',
    preview_hrv: 'HRV',
    preview_rhr: 'Resting HR',
    preview_tsb: 'TSB',
    preview_tsb_status: 'Optimal',
    preview_ctl: 'Training load by sport',
    preview_ai_title: 'AI Recommendation',
    preview_cta_login: 'Want to see your own data? Log in',

    features_title: 'What is inside',
    features_morning_title: 'Morning report',
    features_morning_body: 'Recovery score, HRV, RHR, TSB and an AI recommendation delivered to Telegram every morning.',
    features_load_title: 'Training load',
    features_load_body: 'CTL, ATL, TSB from Intervals.icu with per-sport breakdown. 30-day trend.',
    features_dfa_title: 'DFA α1 analysis',
    features_dfa_body: 'HRVT1/HRVT2 thresholds, Readiness (Ra) and Durability (Da) after every session.',
    features_race_title: 'Race progress',
    features_race_body: 'Target CTL to race day, ramp rate, auto race tracking and post-race context.',
    features_ai_title: 'Claude AI',
    features_ai_body: 'Claude Sonnet reviews every metric and adjusts your plan based on the data.',
    features_evening_title: 'Evening digest',
    features_evening_body: 'Day summary, workout vs plan, next-day plan — every evening at 9 PM.',

    howitworks_title: 'How it works',
    howitworks_step1: 'The system gathers wellness, HRV and training data',
    howitworks_step2: 'Recovery engine computes metrics, states and trends',
    howitworks_step3: 'Claude reviews everything via MCP and builds a recommendation',
    howitworks_step4: 'The morning report arrives in Telegram',

    deepdive_title: 'Morning report — deep dive',
    deepdive_recovery_title: 'Recovery Analysis',
    deepdive_recovery_body:
      'A weighted combination of four signals: HRV, training load, resting HR, sleep.',
    deepdive_hrv_title: 'Dual HRV algorithm',
    deepdive_hrv_body:
      'One algorithm reacts quickly to day-to-day swings. The other catches chronic fatigue through weekly trends. Together — a stable recovery signal.',
    deepdive_hrv_fast: 'Fast response',
    deepdive_hrv_chronic: 'Chronic trend',
    deepdive_ai_title: 'AI recommendation',
    deepdive_ai_body:
      'Claude Sonnet reads every metric through the MCP protocol and returns a concrete call — intensity, zone, duration.',
    deepdive_ai_thinking: 'Claude is thinking',

    opensource_title: 'Open Source',
    opensource_body: 'The project is open on GitHub. Repository, code, migration history — all public.',
    opensource_metric_tools: 'MCP tools',
    opensource_cta: 'View on GitHub',

    footer_built_by: 'Built by',
    footer_github: 'GitHub',
    footer_telegram: 'Telegram',
  },
} as const

export type TranslationKey = keyof typeof dict.ru

function detectLang(): Lang {
  if (typeof window === 'undefined') return 'ru'
  const override = new URLSearchParams(window.location.search).get('lang')
  if (override === 'en' || override === 'ru') return override
  const nav = (navigator.language || '').toLowerCase()
  return nav.startsWith('ru') ? 'ru' : 'en'
}

export const lang: Lang = detectLang()

export function t(key: TranslationKey): string {
  return dict[lang][key]
}
