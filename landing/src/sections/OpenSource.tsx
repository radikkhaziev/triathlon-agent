import { t } from '../i18n'

const GITHUB_URL = 'https://github.com/radikkhaziev/triathlon-agent'

// Approximate, hardcoded — exact counts rot with every PR.
const METRICS = [
  { value: '50+', labelKey: 'opensource_metric_tools' as const },
  { value: '25+', labelKey: 'opensource_metric_tables' as const },
  { value: '2', labelKey: 'opensource_metric_lang' as const },
]

export default function OpenSource() {
  return (
    <section className="max-w-4xl mx-auto px-6 py-12">
      <div className="bg-surface border border-border rounded-2xl p-8 text-center">
        <h2 className="text-xl font-bold mb-3">{t('opensource_title')}</h2>
        <p className="text-sm text-text-dim max-w-xl mx-auto mb-6 leading-relaxed">
          {t('opensource_body')}
        </p>

        <div className="flex flex-wrap justify-center gap-6 mb-6">
          {METRICS.map((m) => (
            <div key={m.labelKey}>
              <div className="text-2xl font-bold text-accent">{m.value}</div>
              <div className="text-xs text-text-dim">{t(m.labelKey)}</div>
            </div>
          ))}
        </div>

        <a
          href={GITHUB_URL}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-block px-5 py-2.5 rounded-xl bg-accent text-white text-sm font-semibold hover:opacity-90 transition"
        >
          {t('opensource_cta')}
        </a>
      </div>
    </section>
  )
}
