import { useEffect, useId, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router-dom'
import { useChangelog } from '../hooks/useChangelog'
import { MORE_NAV_ITEMS, PRIMARY_NAV_ITEMS } from '../lib/navItems'

export default function BottomTabs() {
  const location = useLocation()
  const [moreOpen, setMoreOpen] = useState(false)
  const { t } = useTranslation()
  const moreMenuId = useId()
  const moreButtonRef = useRef<HTMLButtonElement>(null)
  const moreMenuRef = useRef<HTMLDivElement>(null)
  const { changelog, unread, markRead } = useChangelog()

  const isActive = (path: string) => {
    if (path === '/') return location.pathname === '/'
    return location.pathname.startsWith(path)
  }

  const moreActive = MORE_NAV_ITEMS.some(item => isActive(item.path))
  // Unread changelog inflates the More-button into "active" state so the
  // dot is visible without opening the menu.
  const moreHasUnread = !!(changelog && unread)

  // Focus management + Escape to close for the More menu.
  useEffect(() => {
    if (!moreOpen) return

    // Move focus into the menu when it opens.
    const firstLink = moreMenuRef.current?.querySelector<HTMLAnchorElement>('a')
    firstLink?.focus()

    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        setMoreOpen(false)
        moreButtonRef.current?.focus()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [moreOpen])

  return (
    <div className="md:hidden">
      {/* More menu overlay */}
      {moreOpen && (
        <div
          className="fixed inset-0 z-40"
          onClick={() => setMoreOpen(false)}
          aria-hidden="true"
        >
          <div
            id={moreMenuId}
            ref={moreMenuRef}
            role="dialog"
            aria-modal="true"
            aria-label={t('nav.more')}
            className="absolute bottom-[calc(64px+env(safe-area-inset-bottom))] right-2 bg-bg border border-border rounded-xl shadow-lg py-2 min-w-[180px]"
            onClick={e => e.stopPropagation()}
          >
            {MORE_NAV_ITEMS.flatMap(item => {
              const link = (
                <Link
                  key={item.path}
                  to={item.path}
                  onClick={() => setMoreOpen(false)}
                  className={`flex items-center gap-3 px-4 py-3 text-sm no-underline transition-colors hover:bg-surface ${
                    isActive(item.path) ? 'text-accent font-semibold' : 'text-text'
                  }`}
                >
                  <span className="text-lg" aria-hidden="true">
                    {item.icon}
                  </span>
                  {t(item.labelKey)}
                </Link>
              )
              // Inject "What's new" right after /plan — same placement as
              // the desktop Sidebar so users find it in the same spot
              // regardless of viewport.
              if (item.path === '/plan' && changelog && unread) {
                return [
                  link,
                  <a
                    key="changelog"
                    href={changelog.url}
                    target="_blank"
                    rel="noopener noreferrer"
                    onClick={() => {
                      markRead()
                      setMoreOpen(false)
                    }}
                    aria-label={`${t('sidebar.whats_new')} (${t('sidebar.unread')})`}
                    className="flex items-center gap-3 px-4 py-3 text-sm no-underline transition-colors hover:bg-surface text-text"
                  >
                    <span className="text-accent text-lg leading-none" aria-hidden="true">●</span>
                    {t('sidebar.whats_new')}
                  </a>,
                ]
              }
              return [link]
            })}
          </div>
        </div>
      )}

      {/* Bottom tab bar */}
      <nav
        className="fixed bottom-0 left-0 right-0 h-16 bg-surface border-t border-border flex justify-around items-center z-50"
        style={{ paddingBottom: 'env(safe-area-inset-bottom)' }}
      >
        {PRIMARY_NAV_ITEMS.map(tab => (
          <Link
            key={tab.path}
            to={tab.path}
            className={`flex flex-col items-center justify-center gap-0.5 flex-1 h-full no-underline transition-colors ${
              isActive(tab.path) ? 'text-accent' : 'text-text-dim'
            }`}
          >
            <span className="text-xl leading-none" aria-hidden="true">
              {tab.icon}
            </span>
            <span className="text-[10px] font-medium">{t(tab.labelKey)}</span>
          </Link>
        ))}
        <button
          ref={moreButtonRef}
          type="button"
          onClick={() => setMoreOpen(!moreOpen)}
          aria-expanded={moreOpen}
          aria-haspopup="dialog"
          aria-controls={moreMenuId}
          aria-label={
            moreHasUnread && !moreOpen
              ? `${t('nav.more')} (${t('sidebar.whats_new')}: ${t('sidebar.unread')})`
              : undefined
          }
          className={`relative flex flex-col items-center justify-center gap-0.5 flex-1 h-full border-none bg-transparent cursor-pointer transition-colors font-sans ${
            moreActive || moreOpen ? 'text-accent' : 'text-text-dim'
          }`}
        >
          <span className="text-xl leading-none" aria-hidden="true">
            ⚙️
          </span>
          <span className="text-[10px] font-medium">{t('nav.more')}</span>
          {moreHasUnread && !moreOpen && (
            // a11y — `aria-label` на decorative `<span>` непредсказуемо
            // объявляется screen-reader'ами; signal живёт на самом button'е
            // через aria-label выше. Точка остаётся чисто визуальной.
            <span
              aria-hidden="true"
              className="absolute top-2 right-[calc(50%-14px)] w-2 h-2 rounded-full bg-accent"
            />
          )}
        </button>
      </nav>
    </div>
  )
}
