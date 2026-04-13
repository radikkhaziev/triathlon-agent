import ArchitectureDiagram from '../components/ArchitectureDiagram'
import { t, type TranslationKey } from '../i18n'

const STEPS: TranslationKey[] = [
  'howitworks_step1',
  'howitworks_step2',
  'howitworks_step3',
  'howitworks_step4',
]

export default function HowItWorks() {
  return (
    <section className="max-w-4xl mx-auto px-6 py-12">
      <h2 className="text-xl font-bold text-center mb-8">{t('howitworks_title')}</h2>
      <div className="bg-surface border border-border rounded-2xl p-6">
        <ArchitectureDiagram />
      </div>
      <ol className="mt-6 grid gap-3 sm:grid-cols-2 text-sm">
        {STEPS.map((key, i) => (
          <li key={key} className="flex gap-3">
            <span className="flex-shrink-0 w-6 h-6 rounded-full bg-accent text-white text-xs font-bold flex items-center justify-center">
              {i + 1}
            </span>
            <span className="text-text-dim leading-relaxed">{t(key)}</span>
          </li>
        ))}
      </ol>
    </section>
  )
}
