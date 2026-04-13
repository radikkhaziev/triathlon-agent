export type Lang = 'ru' | 'en'

const dict = {
  ru: {
    hero_tagline: 'Recovery score, HRV-анализ и AI-рекомендация — каждое утро в Telegram',
    hero_cta_preview: 'Смотреть демо',
    hero_cta_telegram: 'Открыть в Telegram',
    hero_subline: 'Прокрути вниз — покажу пример утреннего отчёта',

    preview_title: 'Твоё утро с первого взгляда',
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

    tech_title: 'Стек проекта',
    tech_backend: 'Backend',
    tech_frontend: 'Frontend',
    tech_integrations: 'Интеграции',
    tech_infra: 'Инфраструктура',
    tech_note: 'Сам лендинг использует только React и Tailwind.',

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

    tech_title: 'Project stack',
    tech_backend: 'Backend',
    tech_frontend: 'Frontend',
    tech_integrations: 'Integrations',
    tech_infra: 'Infrastructure',
    tech_note: 'The landing itself uses only React and Tailwind.',

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
