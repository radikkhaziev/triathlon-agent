import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router-dom'
import EnduraiLogo from './EnduraiLogo'

interface NavItem {
  path: string
  labelKey: string
  icon: string
}

const NAV: NavItem[] = [
  { path: '/', labelKey: 'nav.today', icon: '🏠' },
  { path: '/plan', labelKey: 'nav.plan', icon: '📋' },
  { path: '/activities', labelKey: 'nav.activities', icon: '🏃' },
  { path: '/wellness', labelKey: 'nav.wellness', icon: '💚' },
  { path: '/progress', labelKey: 'nav.progress', icon: '📈' },
  { path: '/dashboard', labelKey: 'nav.dashboard', icon: '📊' },
  { path: '/settings', labelKey: 'nav.settings', icon: '⚙️' },
]

export default function Sidebar() {
  const location = useLocation()
  const { t } = useTranslation()

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
        {NAV.map(item => {
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
              <span className="text-lg leading-none">{item.icon}</span>
              <span>{t(item.labelKey)}</span>
            </Link>
          )
        })}
      </nav>
    </aside>
  )
}
