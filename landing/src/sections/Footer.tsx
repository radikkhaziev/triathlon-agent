import EnduraiLogo from '../components/EnduraiLogo'
import { t } from '../i18n'

const GITHUB_URL = 'https://github.com/radikkhaziev/triathlon-agent'
const TELEGRAM_URL = 'https://t.me/radikrunbot'
const AUTHOR_URL = 'https://github.com/radikkhaziev'

export default function Footer() {
  return (
    <footer className="border-t border-border mt-8">
      <div className="max-w-4xl mx-auto px-6 py-8 flex flex-col sm:flex-row items-center justify-between gap-4">
        <div className="flex items-center gap-2">
          <EnduraiLogo height={28} />
        </div>
        <nav className="flex items-center gap-5 text-sm text-text-dim">
          <a
            href={GITHUB_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-text transition"
          >
            {t('footer_github')}
          </a>
          <a
            href={TELEGRAM_URL}
            target="_blank"
            rel="noopener noreferrer"
            className="hover:text-text transition"
          >
            {t('footer_telegram')}
          </a>
          <span>
            {t('footer_built_by')}{' '}
            <a
              href={AUTHOR_URL}
              target="_blank"
              rel="noopener noreferrer"
              className="hover:text-text transition"
            >
              Radik Khaziev
            </a>
          </span>
        </nav>
      </div>
    </footer>
  )
}
