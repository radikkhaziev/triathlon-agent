import Gauge from '../components/Gauge'
import StatusBadge from '../components/StatusBadge'
import {
  DEMO_RECOVERY,
  DEMO_HRV,
  DEMO_RHR,
  DEMO_TRAINING_LOAD,
  DEMO_AI_TEXT_RU,
  DEMO_AI_TEXT_EN,
} from '../data/demo'
import { t, lang } from '../i18n'

const BOT_LOGIN_URL = 'https://bot.endurai.me/login'
const TELEGRAM_URL = 'https://t.me/endurai_bot'

export default function LandingPreview() {
  const aiText = lang === 'ru' ? DEMO_AI_TEXT_RU : DEMO_AI_TEXT_EN
  const fmtDelta = (v: number) => (v > 0 ? `+${v.toFixed(1)}%` : `${v.toFixed(1)}%`)
  const fmtTsb = (v: number) => (v > 0 ? `+${v.toFixed(1)}` : v.toFixed(1))

  return (
    <section id="preview" className="max-w-4xl mx-auto px-6 py-12">
      <h2 className="text-xl font-bold text-center mb-2">{t('preview_title')}</h2>
      <p className="text-xs text-text-dim text-center mb-6">{t('preview_subtitle')}</p>

      <div className="bg-surface border border-border rounded-2xl p-6 shadow-[0_8px_24px_-12px_rgba(0,0,0,0.15)]">
        {/* Recovery */}
        <div className="flex items-center gap-5 pb-5 border-b border-border">
          <Gauge value={DEMO_RECOVERY.score} size={104} />
          <div>
            <div className="text-xs uppercase tracking-wide text-text-dim">
              {t('preview_recovery')}
            </div>
            <div className="text-lg font-bold">{t('preview_good')}</div>
            <div className="text-sm text-text-dim">{t('preview_zone2')}</div>
          </div>
        </div>

        {/* Metrics */}
        <div className="grid grid-cols-2 gap-3 py-5 border-b border-border text-sm">
          <MetricRow
            label={t('preview_hrv')}
            primary={`${DEMO_HRV.today} ms`}
            secondary={`7d ${DEMO_HRV.mean_7d} · ${fmtDelta(DEMO_HRV.delta_pct)}`}
            badge={<StatusBadge status={DEMO_HRV.status} label="🟢" />}
          />
          <MetricRow
            label={t('preview_rhr')}
            primary={`${DEMO_RHR.today} bpm`}
            secondary={`30d ${DEMO_RHR.mean_30d}`}
            badge={<StatusBadge status={DEMO_RHR.status} label="🟢" />}
          />
          <MetricRow
            label={t('preview_tsb')}
            primary={fmtTsb(DEMO_TRAINING_LOAD.tsb)}
            secondary={t('preview_tsb_status')}
          />
          <MetricRow
            label="CTL"
            primary={String(DEMO_TRAINING_LOAD.ctl)}
            secondary={`ATL ${DEMO_TRAINING_LOAD.atl}`}
          />
        </div>

        {/* Per-sport CTL */}
        <div className="py-4 border-b border-border">
          <div className="text-xs uppercase tracking-wide text-text-dim mb-2">
            {t('preview_ctl')}
          </div>
          <div className="flex gap-4 text-sm">
            <span>🏊 {DEMO_TRAINING_LOAD.sport_ctl.swim}</span>
            <span>🚴 {DEMO_TRAINING_LOAD.sport_ctl.bike}</span>
            <span>🏃 {DEMO_TRAINING_LOAD.sport_ctl.run}</span>
          </div>
        </div>

        {/* AI recommendation */}
        <div className="pt-5">
          <div className="text-xs uppercase tracking-wide text-text-dim mb-2">
            {t('preview_ai_title')}
          </div>
          <div className="text-sm leading-relaxed bg-bg border border-border rounded-xl px-4 py-3">
            {aiText}
          </div>
        </div>
      </div>

      <div className="mt-6 flex flex-wrap items-center justify-center gap-3">
        <a
          href={BOT_LOGIN_URL}
          className="px-5 py-2.5 rounded-xl bg-accent text-white text-sm font-semibold hover:opacity-90 transition"
        >
          {t('preview_cta_login')}
        </a>
        <a
          href={TELEGRAM_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="px-5 py-2.5 rounded-xl bg-surface border border-border text-text text-sm font-semibold hover:bg-surface-2 transition"
        >
          {t('hero_cta_telegram')}
        </a>
      </div>
    </section>
  )
}

interface MetricRowProps {
  label: string
  primary: string
  secondary: string
  badge?: React.ReactNode
}

function MetricRow({ label, primary, secondary, badge }: MetricRowProps) {
  return (
    <div>
      <div className="text-xs text-text-dim flex items-center gap-2">
        {label}
        {badge}
      </div>
      <div className="font-semibold">{primary}</div>
      <div className="text-xs text-text-dim">{secondary}</div>
    </div>
  )
}
