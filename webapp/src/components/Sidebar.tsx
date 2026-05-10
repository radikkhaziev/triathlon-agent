import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router-dom'
import { apiFetch } from '../api/client'
import type { ChangelogLatest } from '../api/types'
import { useAuth } from '../auth/useAuth'
import { ALL_NAV_ITEMS } from '../lib/navItems'
import EnduraiLogo from './EnduraiLogo'

// Spec §10 deviation — постоянная эмодзи-ссылка для атлета, который
// changelogs не читает = visual debt. Рендерим ТОЛЬКО когда `url` отличается
// от того что мы записали при последнем клике. Привязка к URL устойчива
// к смене ссылки Discussion'а и не зависит от часов клиента.
const LAST_SEEN_KEY = 'changelog.last_seen_url'

export default function Sidebar() {
  const location = useLocation()
  const { t } = useTranslation()
  const { isAuthenticated } = useAuth()
  const [changelog, setChangelog] = useState<ChangelogLatest | null>(null)
  const [unread, setUnread] = useState(false)

  useEffect(() => {
    // H1 — без auth-gate этот fetch на /login возвращает 401 → центральный
    // apiFetch handler делает force-redirect на /login, ломая login flow.
    if (!isAuthenticated) return
    apiFetch<ChangelogLatest>('/api/changelog/latest')
      .then(cl => {
        setChangelog(cl)
        setUnread(cl.url !== localStorage.getItem(LAST_SEEN_KEY))
      })
      .catch(() => setChangelog(null))  // 404/503 → no link
  }, [isAuthenticated])

  const onChangelogClick = () => {
    // H2 — Safari private mode и переполненный quota бросают QuotaExceededError
    // на setItem; глотаем — unread-state можно потерять, но page не должна
    // падать из-за storage failure.
    try {
      if (changelog) localStorage.setItem(LAST_SEEN_KEY, changelog.url)
    } catch {
      // ignore
    }
    setUnread(false)
  }

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
        {ALL_NAV_ITEMS.map(item => {
          const active = isActive(item.path)
          return (
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
        })}
        {changelog && unread && (
          <a
            href={changelog.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={onChangelogClick}
            aria-label={`${t('sidebar.whats_new')} (${t('sidebar.unread')})`}
            className="flex items-center gap-3 px-3 py-2.5 rounded-lg text-sm no-underline text-text hover:bg-surface-2 transition-colors"
          >
            <span className="text-accent text-lg leading-none" aria-hidden="true">●</span>
            <span>{t('sidebar.whats_new')}</span>
          </a>
        )}
      </nav>
    </aside>
  )
}
