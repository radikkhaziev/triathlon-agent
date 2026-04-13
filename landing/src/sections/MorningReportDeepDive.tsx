import Gauge from '../components/Gauge'
import StatusBadge from '../components/StatusBadge'
import TypingAnimation from '../components/TypingAnimation'
import { lang, t } from '../i18n'
import { DEMO_AI_TEXT_RU, DEMO_AI_TEXT_EN } from '../data/demo'

export default function MorningReportDeepDive() {
  const aiText = lang === 'ru' ? DEMO_AI_TEXT_RU : DEMO_AI_TEXT_EN

  return (
    <section className="max-w-4xl mx-auto px-6 py-12">
      <h2 className="text-xl font-bold text-center mb-8">{t('deepdive_title')}</h2>
      <div className="grid gap-4 md:grid-cols-3">
        {/* Block 1 — Recovery */}
        <article className="bg-surface border border-border rounded-2xl p-5 flex flex-col gap-4">
          <div className="flex justify-center">
            <Gauge value={78} size={88} lineWidth={8} />
          </div>
          <div>
            <h3 className="text-base font-bold mb-2">{t('deepdive_recovery_title')}</h3>
            <p className="text-sm text-text-dim leading-relaxed">{t('deepdive_recovery_body')}</p>
          </div>
        </article>

        {/* Block 2 — Dual HRV */}
        <article className="bg-surface border border-border rounded-2xl p-5 flex flex-col gap-4">
          <div className="flex flex-col gap-2 items-start">
            <StatusBadge status="green" label={`🟢 ${t('deepdive_hrv_fast')}`} />
            <StatusBadge status="yellow" label={`🟡 ${t('deepdive_hrv_chronic')}`} />
          </div>
          <div>
            <h3 className="text-base font-bold mb-2">{t('deepdive_hrv_title')}</h3>
            <p className="text-sm text-text-dim leading-relaxed">{t('deepdive_hrv_body')}</p>
          </div>
        </article>

        {/* Block 3 — AI */}
        <article className="bg-surface border border-border rounded-2xl p-5 flex flex-col gap-4">
          <div className="bg-bg border border-border rounded-xl px-3 py-2.5 text-xs leading-relaxed">
            <div className="flex items-center gap-2 text-text-dim mb-1.5">
              <TypingAnimation />
              <span>{t('deepdive_ai_thinking')}…</span>
            </div>
            <p className="text-text">{aiText}</p>
          </div>
          <div>
            <h3 className="text-base font-bold mb-2">{t('deepdive_ai_title')}</h3>
            <p className="text-sm text-text-dim leading-relaxed">{t('deepdive_ai_body')}</p>
          </div>
        </article>
      </div>
    </section>
  )
}
