import EnduraiLogo from '../components/EnduraiLogo'
import { t } from '../i18n'

const TELEGRAM_URL = 'https://t.me/radikrunbot'

function scrollToPreview() {
  document.getElementById('preview')?.scrollIntoView({ behavior: 'smooth', block: 'start' })
}

export default function Hero() {
  return (
    <section className="relative overflow-hidden">
      <div
        aria-hidden
        className="absolute inset-0 -z-10 bg-gradient-to-b from-[var(--accent-glow)] to-transparent pointer-events-none"
      />
      <div className="max-w-4xl mx-auto px-6 pt-16 pb-20 text-center">
        <div className="flex justify-center mb-8">
          <EnduraiLogo height={64} />
        </div>
        <h1 className="text-2xl sm:text-3xl font-bold leading-tight mb-4 max-w-2xl mx-auto">
          {t('hero_tagline')}
        </h1>
        <p className="text-sm text-text-dim mb-8">{t('hero_subline')}</p>
        <div className="flex flex-wrap items-center justify-center gap-3">
          <button
            type="button"
            onClick={scrollToPreview}
            className="px-5 py-2.5 rounded-xl bg-accent text-white text-sm font-semibold hover:opacity-90 transition"
          >
            {t('hero_cta_preview')}
          </button>
          <a
            href={TELEGRAM_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="px-5 py-2.5 rounded-xl bg-surface border border-border text-text text-sm font-semibold hover:bg-surface-2 transition"
          >
            {t('hero_cta_telegram')}
          </a>
        </div>
      </div>
    </section>
  )
}
