// ── Endurai landing · brand tokens + bilingual copy ─────────────────────
// Ported 1:1 from the Claude Design handoff (final revision q3xdSpTQ...).
// Single "Спокойный" (calm / premium) variant. Rendered with inline styles to
// stay pixel-faithful, so BRAND lives here as a self-contained token set.

export const BRAND = {
  cobalt: '#3b6dff',
  cobaltDark: '#1f4ad0',
  cobaltLite: '#e1e8ff',
  ink: '#0a0d18',
  dim: '#5f6573',
  dimmer: '#9498a4',
  warm: '#faf9f5',
  cool: '#f2f4f8',
  surface: '#ffffff',
  border: 'rgba(10,13,24,0.08)',
  amber: '#d18b00',
  coral: '#d94640',
  green: '#16a34a',
  greenWash: '#dcfce7',
  // sport vocabulary (kept identical to the app)
  swim: '#d18b00',
  ride: '#3b6dff',
  run: '#d94640',
  sans: '"Inter", -apple-system, BlinkMacSystemFont, "Segoe UI", system-ui, sans-serif',
}

export type Lang = 'ru' | 'en'

export interface HowStep {
  n: string
  t: string
  d: string
}
export interface Feat {
  t: string
  d: string
  tag: string
}
export interface ChatMsg {
  who: 'me' | 'bot'
  text: string
}

export interface LandingCopy {
  nav_features: string
  nav_how: string
  nav_demo: string
  cta_demo: string
  cta_tg: string
  cta_demo_sub: string

  hero_eyebrow: string
  hero_title: [string, string]
  hero_lead: string

  proof_sub: string

  how_kicker: string
  how_title: string
  how_steps: HowStep[]

  feat_kicker: string
  feat_title: string
  feats: Feat[]

  tg_kicker: string
  tg_title: string
  tg_lead: string
  tg_bullets: string[]
  tg_chat: ChatMsg[]

  screens_kicker: string
  screens_title: string
  screen_labels: string[]

  os_kicker: string
  os_title: string
  os_lead: string
  os_bullets: string[]
  os_code_label: string
  os_code_note: string
  os_cta: string

  final_title: string
  final_lead: string

  foot_tag: string
  foot_demo: string
  foot_tg: string
  foot_rights: string
}

export const COPY: Record<Lang, LandingCopy> = {
  ru: {
    nav_features: 'Возможности',
    nav_how: 'Как это работает',
    nav_demo: 'Demo',
    cta_demo: 'Попробовать demo',
    cta_tg: 'Открыть в Telegram',
    cta_demo_sub: 'Демо на готовых данных',

    hero_eyebrow: 'Умная аналитика выносливости',
    hero_title: ['Читает тело,', 'а не данные'],
    hero_lead: 'Твоё тело уже всё сказало — recovery, HRV и план на день. Каждое утро, в Telegram.',

    proof_sub: 'Подключается к источникам, которыми ты уже пользуешься',

    how_kicker: 'Как это работает',
    how_title: 'От данных — к решению за одно утро',
    how_steps: [
      { n: '01', t: 'Подключи источник', d: 'Intervals.icu через OAuth или экспорт из Garmin. Минута — и история тренировок у Endurai.' },
      { n: '02', t: 'Endurai считает форму', d: 'Recovery, HRV, RHR, сон, CTL / ATL / TSB — каждую ночь, пока ты спишь.' },
      { n: '03', t: 'Утром — отчёт в Telegram', d: 'Одна цифра готовности, светофор по HRV и план на день со смыслом.' },
      { n: '04', t: 'Спрашивай бота', d: 'Бот знает твою историю и цели. Ответы — персональные, а не из методички.' },
    ],

    feat_kicker: 'Возможности',
    feat_title: 'Всё, что нужно утром — на одном экране',
    feats: [
      { t: 'Recovery score', d: 'Одна цифра 0–100. За 3 секунды понятно: грузиться, держать или отдыхать.', tag: 'Готовность' },
      { t: 'Анализ HRV', d: 'Светофор, тренд к базе и человеческое объяснение, что это значит сегодня.', tag: 'HRV · RHR' },
      { t: 'AI-план дня', d: 'Тренировка с зонами и rationale — зачем именно эта работа именно сегодня.', tag: 'План' },
      { t: 'Разбор тренировки', d: 'Зоны, decoupling, EF, соответствие плану — без ручной работы в таблицах.', tag: 'Анализ' },
      { t: 'Прогресс формы', d: 'CTL / ATL / TSB, тренд EF и проекция готовности к гонке.', tag: 'Тренды' },
      { t: 'Telegram-бот', d: 'Отчёты и диалог в Telegram. Бот владеет твоими данными — спроси что угодно.', tag: 'Telegram' },
    ],

    tg_kicker: 'Telegram-нативно',
    tg_title: 'Твой тренер живёт в Telegram',
    tg_lead: 'Никаких новых приложений. Отчёт приходит сообщением каждое утро, а бот держит в памяти твою историю, цели и форму — поэтому отвечает лично тебе.',
    tg_bullets: [
      'Утренний отчёт готовности — сообщением',
      'Диалог о тренировке: «можно ли сегодня интервалы?»',
      'Бот знает CTL, HRV и цель на сезон',
      'Mini App с полным дашбордом — внутри Telegram',
    ],
    tg_chat: [
      { who: 'me', text: 'Самочувствие так себе. Делать сегодня интервалы?' },
      { who: 'bot', text: 'HRV в зелёной зоне, но TSB −22 при ramp +7 — ты глубоко в усталости. Перенеси интервалы на завтра, сегодня Z2 60 мин.' },
      { who: 'me', text: 'Ок. А до гонки успеваю набрать форму?' },
      { who: 'bot', text: 'До Ironman 70.3 пять недель. По текущему темпу CTL выйдет на 84 из 85 — в коридоре. Держим план.' },
    ],

    screens_kicker: 'Внутри приложения',
    screens_title: 'Дашборд поверх твоих данных',
    screen_labels: ['Утренняя готовность', 'План недели', 'Разбор тренировки'],

    os_kicker: 'Открытый код · MCP',
    os_title: 'Открытый код и свой MCP-сервер',
    os_lead: 'Endurai — открытый проект. Подключи его к Claude или другому ассистенту как MCP-сервер: AI получит доступ к твоим тренировкам, форме и целям — и будет отвечать по делу.',
    os_bullets: [
      'Весь код открыт — форкай, проверяй, дорабатывай',
      'MCP-сервер: данные видны Claude и любому MCP-клиенту',
      'Открытый API — строй свою аналитику поверх',
    ],
    os_code_label: 'claude_desktop_config.json',
    os_code_note: 'Токен — в настройках Endurai',
    os_cta: 'Смотреть на GitHub',

    final_title: 'Начни утро с одной цифры',
    final_lead: 'Демо открывается на готовых данных — посмотри, как Endurai видит твою форму.',

    foot_tag: 'Умная аналитика выносливости',
    foot_demo: 'Demo',
    foot_tg: 'Telegram',
    foot_rights: '© 2026 Endurai',
  },
  en: {
    nav_features: 'Features',
    nav_how: 'How it works',
    nav_demo: 'Demo',
    cta_demo: 'Try the demo',
    cta_tg: 'Open in Telegram',
    cta_demo_sub: 'Runs on ready-made demo data',

    hero_eyebrow: 'Intelligent endurance analytics',
    hero_title: ['Reads your body,', 'not data'],
    hero_lead: 'Your body already spoke — recovery, HRV and a daily plan. Every morning, in Telegram.',

    proof_sub: 'Plugs into the sources you already use',

    how_kicker: 'How it works',
    how_title: 'From data to decision in one morning',
    how_steps: [
      { n: '01', t: 'Connect a source', d: 'Intervals.icu via OAuth, or a Garmin export. One minute and your history is in Endurai.' },
      { n: '02', t: 'Endurai reads your form', d: 'Recovery, HRV, RHR, sleep, CTL / ATL / TSB — every night while you sleep.' },
      { n: '03', t: 'Morning report in Telegram', d: 'One readiness number, an HRV traffic light and a plan that explains itself.' },
      { n: '04', t: 'Ask the bot', d: 'It knows your history and goals. Answers are personal, not from a textbook.' },
    ],

    feat_kicker: 'Features',
    feat_title: 'Everything you need at dawn — on one screen',
    feats: [
      { t: 'Recovery score', d: 'One number, 0–100. In 3 seconds: push, hold or rest today.', tag: 'Readiness' },
      { t: 'HRV analysis', d: 'Traffic light, trend to baseline and a plain-language read on today.', tag: 'HRV · RHR' },
      { t: 'AI plan of the day', d: 'A workout with zones and rationale — why this work, why today.', tag: 'Plan' },
      { t: 'Workout breakdown', d: 'Zones, decoupling, EF and plan adherence — no spreadsheet work.', tag: 'Analysis' },
      { t: 'Fitness progress', d: 'CTL / ATL / TSB, EF trend and a projection toward race day.', tag: 'Trends' },
      { t: 'Telegram bot', d: 'Reports and chat in Telegram. The bot owns your data — ask anything.', tag: 'Telegram' },
    ],

    tg_kicker: 'Telegram-native',
    tg_title: 'Your coach lives in Telegram',
    tg_lead: 'No new app to learn. The report arrives as a message each morning, and the bot keeps your history, goals and form in memory — so the answers are yours.',
    tg_bullets: [
      'Morning readiness report — as a message',
      'Chat about the session: “intervals today?”',
      'The bot knows your CTL, HRV and season goal',
      'Full-dashboard Mini App — inside Telegram',
    ],
    tg_chat: [
      { who: 'me', text: 'Feeling flat. Should I do intervals today?' },
      { who: 'bot', text: 'HRV is green, but TSB is −22 with ramp +7 — you’re deep in fatigue. Move intervals to tomorrow; today Z2 for 60 min.' },
      { who: 'me', text: 'Ok. Will I still build form before the race?' },
      { who: 'bot', text: 'Five weeks to your Ironman 70.3. At this rate CTL lands at 84 of 85 — right in the corridor. Hold the plan.' },
    ],

    screens_kicker: 'Inside the app',
    screens_title: 'A dashboard over your data',
    screen_labels: ['Morning readiness', 'Week plan', 'Workout breakdown'],

    os_kicker: 'Open source · MCP',
    os_title: 'Open source, with its own MCP server',
    os_lead: 'Endurai is open source. Connect it to Claude or any assistant as an MCP server: your AI gets your training, form and goals — and answers to the point.',
    os_bullets: [
      'All code is open — fork it, audit it, extend it',
      'MCP server: your data is visible to Claude and any MCP client',
      'Open API — build your own analytics on top',
    ],
    os_code_label: 'claude_desktop_config.json',
    os_code_note: 'Get your token in Endurai settings',
    os_cta: 'View on GitHub',

    final_title: 'Start the morning with one number',
    final_lead: 'The demo opens on ready-made data — see how Endurai reads your form.',

    foot_tag: 'Intelligent endurance analytics',
    foot_demo: 'Demo',
    foot_tg: 'Telegram',
    foot_rights: '© 2026 Endurai',
  },
}
