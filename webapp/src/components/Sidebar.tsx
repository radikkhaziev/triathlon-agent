import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router-dom'
import { useChangelog } from '../hooks/useChangelog'
import { ALL_NAV_ITEMS } from '../lib/navItems'
import EnduraiLogo from './EnduraiLogo'

// Spec §10 deviation — постоянная эмодзи-ссылка для атлета, который
// changelogs не читает = visual debt. Рендерим ТОЛЬКО когда unread.

export default function Sidebar() {
  const location = useLocation()
  const { t } = useTranslation()
  const { changelog, unread, markRead } = useChangelog()

  const isActive = (path: string) => {
    if (path === '/') return location.pathname === '/'
    return location.pathname.startsWith(path)
  }

  return (
    <aside className="hidden md:flex fixed left-0 top-0 bottom-0 w-56 border-r border-border bg-surface flex-col z-40">
      <div className="px-5 py-6 border-b border-border">
        <Link to="/" className="block no-underline">
          <EnduraiLogo height={36} />
        </Link>
      </div>
      <nav className="flex-1 py-4 px-3 space-y-1 overflow-y-auto">
        {ALL_NAV_ITEMS.flatMap(item => {
          const active = isActive(item.path)
          const link = (
            <Link
              key={item.path}
              to={item.path}
              className={`flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm no-underline transition-colors ${
                active
                  ? 'bg-surface-2 text-accent font-semibold'
                  : 'text-text hover:bg-surface-2'
              }`}
            >
              <span className="text-lg leading-none" aria-hidden="true">
                {item.icon}
              </span>
              <span>{t(item.labelKey)}</span>
            </Link>
          )
          // Insert "What's new" right after /plan in both desktop sidebar
          // and mobile More menu, so the link sits next to План rather
          // than at the bottom of the list.
          if (item.path === '/plan' && changelog && unread) {
            return [
              link,
              <a
                key="changelog"
                href={changelog.url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={markRead}
                aria-label={`${t('sidebar.whats_new')} (${t('sidebar.unread')})`}
                className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm no-underline text-text hover:bg-surface-2 transition-colors"
              >
                <span className="text-accent text-lg leading-none" aria-hidden="true">●</span>
                <span>{t('sidebar.whats_new')}</span>
              </a>,
            ]
          }
          return [link]
        })}
      </nav>
    </aside>
  )
}
