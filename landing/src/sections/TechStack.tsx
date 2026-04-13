import { t } from '../i18n'

interface TechGroup {
  titleKey: 'tech_backend' | 'tech_frontend' | 'tech_integrations' | 'tech_infra'
  items: string[]
}

const GROUPS: TechGroup[] = [
  { titleKey: 'tech_backend', items: ['Python 3.12', 'FastAPI', 'PostgreSQL', 'Redis', 'Dramatiq', 'SQLAlchemy'] },
  { titleKey: 'tech_frontend', items: ['React 18', 'TypeScript', 'Tailwind CSS', 'Chart.js', 'Vite'] },
  { titleKey: 'tech_integrations', items: ['Claude API', 'MCP Protocol', 'Intervals.icu', 'Telegram Bot API', 'Garmin GDPR'] },
  { titleKey: 'tech_infra', items: ['Docker', 'Nginx', 'Sentry', 'APScheduler', 'Alembic'] },
]

export default function TechStack() {
  return (
    <section className="max-w-4xl mx-auto px-6 py-12">
      <h2 className="text-xl font-bold text-center mb-2">{t('tech_title')}</h2>
      <p className="text-xs text-text-dim text-center mb-8">{t('tech_note')}</p>
      <div className="space-y-5">
        {GROUPS.map((g) => (
          <div key={g.titleKey}>
            <div className="text-xs uppercase tracking-wide text-text-dim mb-2">{t(g.titleKey)}</div>
            <div className="flex flex-wrap gap-2">
              {g.items.map((item) => (
                <span
                  key={item}
                  className="px-3 py-1 rounded-lg bg-surface border border-border text-xs font-medium"
                >
                  {item}
                </span>
              ))}
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}
