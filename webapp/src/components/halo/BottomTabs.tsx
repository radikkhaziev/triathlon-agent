import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router-dom'

export interface HaloTab {
  path: string
  labelKey: string
}

interface HaloBottomTabsProps {
  /**
   * Tab set. Required prop, route-agnostic by design. F1/F16 resolved
   * 2026-05-17 (option A) — the canonical set lives in
   * `lib/navItems.HALO_BOTTOM_TABS` (Today/Plan/History/Trends/Profile).
   */
  items: HaloTab[]
  className?: string
}

/**
 * Halo bottom tab bar (README §4). Active tab = filled brand dot inside a
 * circle; inactive = hollow circle. Routes via <Link>. Mounted in
 * `Layout` (mobile, fixed bottom) — replaced the legacy emoji BottomTabs
 * 2026-05-17 (F1/F16 = option A). Positioning (fixed/md:hidden) is the
 * consumer's job via `className`; this stays a pure presentational shell.
 */
export default function HaloBottomTabs({ items, className = '' }: HaloBottomTabsProps) {
  const { t } = useTranslation()
  const location = useLocation()

  const isActive = (path: string) =>
    path === '/' ? location.pathname === '/' : location.pathname.startsWith(path)

  return (
    <nav
      className={`grid items-center border-t border-halo-border bg-halo-surface px-3 pt-2.5 pb-[calc(0.875rem+env(safe-area-inset-bottom))] ${className}`}
      style={{ gridTemplateColumns: `repeat(${items.length}, 1fr)` }}
    >
      {items.map(tab => {
        const active = isActive(tab.path)
        return (
          <Link
            key={tab.path}
            to={tab.path}
            className="flex flex-col items-center gap-1 no-underline"
          >
            <span
              className={`flex h-6 w-6 items-center justify-center rounded-full ${
                active ? 'bg-halo-brand' : 'border-[1.5px] border-halo-ink-dimmer'
              }`}
            >
              {active && <span className="h-2 w-2 rounded-full bg-white" />}
            </span>
            <span
              className={`text-[11px] ${
                active
                  ? 'font-semibold text-halo-ink'
                  : 'font-medium text-halo-ink-dim'
              }`}
            >
              {t(tab.labelKey)}
            </span>
          </Link>
        )
      })}
    </nav>
  )
}
