import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router-dom'

interface Tab {
  path: string
  labelKey: string
  icon: string
}

const TABS: Tab[] = [
  { path: '/', labelKey: 'nav.today', icon: '🏠' },
  { path: '/plan', labelKey: 'nav.plan', icon: '📋' },
  { path: '/activities', labelKey: 'nav.activities', icon: '🏃' },
  { path: '/wellness', labelKey: 'nav.wellness', icon: '💚' },
]

const MORE_ITEMS: Tab[] = [
  { path: '/progress', labelKey: 'nav.progress', icon: '📈' },
  { path: '/dashboard', labelKey: 'nav.dashboard', icon: '📊' },
  { path: '/settings', labelKey: 'nav.settings', icon: '⚙️' },
]

export default function BottomTabs() {
  const location = useLocation()
  const [moreOpen, setMoreOpen] = useState(false)
  const { t } = useTranslation()

  const isActive = (path: string) => {
    if (path === '/') return location.pathname === '/'
    return location.pathname.startsWith(path)
  }

  const moreActive = MORE_ITEMS.some(item => isActive(item.path))

  return (
    <>
      {/* More menu overlay */}
      {moreOpen && (
        <div className="fixed inset-0 z-40" onClick={() => setMoreOpen(false)}>
          <div className="absolute bottom-[calc(64px+env(safe-area-inset-bottom))] right-2 bg-bg border border-border rounded-xl shadow-lg py-2 min-w-[180px]" onClick={e => e.stopPropagation()}>
            {MORE_ITEMS.map(item => (
              <Link
                key={item.path}
                to={item.path}
                onClick={() => setMoreOpen(false)}
                className={`flex items-center gap-3 px-4 py-3 text-sm no-underline transition-colors hover:bg-surface ${
                  isActive(item.path) ? 'text-accent font-semibold' : 'text-text'
                }`}
              >
                <span className="text-lg">{item.icon}</span>
                {t(item.labelKey)}
              </Link>
            ))}
          </div>
        </div>
      )}

      {/* Bottom tab bar */}
      <nav className="fixed bottom-0 left-0 right-0 h-16 bg-surface border-t border-border flex justify-around items-center z-50" style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}>
        {TABS.map(tab => (
          <Link
            key={tab.path}
            to={tab.path}
            className={`flex flex-col items-center justify-center gap-0.5 flex-1 h-full no-underline transition-colors ${
              isActive(tab.path) ? 'text-accent' : 'text-text-dim'
            }`}
          >
            <span className="text-xl leading-none">{tab.icon}</span>
            <span className="text-[10px] font-medium">{t(tab.labelKey)}</span>
          </Link>
        ))}
        <button
          onClick={() => setMoreOpen(!moreOpen)}
          className={`flex flex-col items-center justify-center gap-0.5 flex-1 h-full border-none bg-transparent cursor-pointer transition-colors font-sans ${
            moreActive || moreOpen ? 'text-accent' : 'text-text-dim'
          }`}
        >
          <span className="text-xl leading-none">⚙️</span>
          <span className="text-[10px] font-medium">{t('nav.more')}</span>
        </button>
      </nav>
    </>
  )
}
