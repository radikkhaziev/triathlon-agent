import { useTranslation } from 'react-i18next'
import { Link, useLocation } from 'react-router-dom'
import { useChangelog } from '../../hooks/useChangelog'
import { useAuth } from '../../auth/useAuth'
import { ALL_NAV_ITEMS } from '../../lib/navItems'

// Line icons — prototype `BdIcon` (direction-b-desktop.jsx), keyed by route.
// Halo-v3: 4-tab IA (wellness/calendar/trends/settings). Icons для
// `/weekly` удалены вместе с этим пунктом из primary nav — route жив как
// deep-link. `/activities` list-route retired совсем (Week tab закрывает
// кейс); `/activity/:id` живёт как deep-link. Старый `/progress` retired
// (контент в Load-табе).
// Route rename 2026-05-23: `/plan` → `/calendar`, `/dashboard` → `/trends`.
const ICON: Record<string, string> = {
  '/wellness': 'M20.8 8.6c0-2.5-2-4.5-4.5-4.5-1.8 0-3.4 1.1-4.3 2.7-.9-1.6-2.5-2.7-4.3-2.7C5.2 4.1 3.2 6.1 3.2 8.6c0 6 8.8 11.3 8.8 11.3s8.8-5.3 8.8-11.3z',
  '/calendar': 'M8 4h8a2 2 0 0 1 2 2v14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2zm1-2h6v4H9V2zm-1 9h8m-8 4h5',
  '/trends': 'M4 19V9m6 10V5m6 14v-7m6 7v-3',
  '/settings':
    'M12 15a3 3 0 1 0 0-6 3 3 0 0 0 0 6zm7.4-3a7.4 7.4 0 0 0-.1-1.4l2-1.6-2-3.4-2.4.9a7.5 7.5 0 0 0-2.4-1.4L14 2h-4l-.5 2.6a7.5 7.5 0 0 0-2.4 1.4l-2.4-.9-2 3.4 2 1.6a7.4 7.4 0 0 0 0 2.8l-2 1.6 2 3.4 2.4-.9a7.5 7.5 0 0 0 2.4 1.4L10 22h4l.5-2.6a7.5 7.5 0 0 0 2.4-1.4l2.4.9 2-3.4-2-1.6c0-.5.1-.9.1-1.4z',
}

function NavIcon({ path }: { path: string }) {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" className="block shrink-0">
      <path d={ICON[path] ?? ''} fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

/**
 * Halo desktop sidebar (prototype `BdSidebar`, direction-b-desktop.jsx).
 * 240px, fixed, md+ only — the mobile bar stays `HaloBottomTabs`. Replaces
 * the legacy `Sidebar` (which used legacy `--accent`/`EnduraiLogo`). Keeps
 * the what's-new-after-/calendar insertion (load-bearing, was in legacy
 * Sidebar — была после `/plan` до route rename 2026-05-23).
 * User pill = brand monogram + logout (no name on this surface — data-honest,
 * avoids an always-on /api/auth/me fetch in the persistent shell).
 */
export default function HaloSidebar() {
  const { t } = useTranslation()
  const location = useLocation()
  const { changelog, unread, markRead } = useChangelog()
  const { logout, isDemo } = useAuth()

  const isActive = (path: string) =>
    path === '/' ? location.pathname === '/' : location.pathname.startsWith(path)

  return (
    <aside className="fixed left-0 top-0 bottom-0 z-40 hidden w-60 flex-col border-r border-halo-border bg-halo-surface md:flex">
      <Link to="/" className="flex items-center gap-2.5 px-[22px] pb-[18px] pt-[22px] no-underline">
        <img src="/endurai-icon.png" alt="" className="h-7 w-7 rounded-[7px]" />
        <span className="text-[17px] font-semibold tracking-[-0.3px] text-halo-ink">
          Endur<span className="text-halo-brand">AI</span>
        </span>
      </Link>

      <nav className="flex flex-1 flex-col gap-0.5 overflow-y-auto px-3 py-1.5">
        {ALL_NAV_ITEMS.flatMap(item => {
          const on = isActive(item.path)
          const link = (
            <Link
              key={item.path}
              to={item.path}
              className={`flex items-center gap-3 rounded-[10px] px-3 py-2.5 text-sm no-underline transition-colors ${
                on
                  ? 'bg-halo-brand-light font-semibold text-halo-brand-dark'
                  : 'text-halo-ink hover:bg-halo-surface-2'
              }`}
            >
              <span className={on ? 'text-halo-brand-dark' : 'text-halo-ink-dim'}>
                <NavIcon path={item.path} />
              </span>
              <span className="tracking-[-0.1px]">{t(item.labelKey)}</span>
            </Link>
          )
          if (item.path === '/calendar' && changelog && unread && !isDemo) {
            return [
              link,
              <a
                key="changelog"
                href={changelog.url}
                target="_blank"
                rel="noopener noreferrer"
                onClick={markRead}
                aria-label={`${t('sidebar.whats_new')} (${t('sidebar.unread')})`}
                className="flex items-center gap-3 rounded-[10px] px-3 py-2.5 text-sm text-halo-ink no-underline transition-colors hover:bg-halo-surface-2"
              >
                <span aria-hidden="true">✨</span>
                <span className="tracking-[-0.1px]">{t('sidebar.whats_new')}</span>
              </a>,
            ]
          }
          return [link]
        })}
      </nav>

      <div className="border-t border-halo-border px-3 pb-[18px] pt-3">
        <button
          type="button"
          onClick={logout}
          className="flex w-full items-center gap-2.5 rounded-xl px-2.5 py-2 text-left cursor-pointer hover:bg-halo-surface-2 font-sans"
        >
          <span className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-halo-brand to-halo-brand-dark text-[12px] font-semibold text-white">
            EN
          </span>
          <span className="flex-1 text-[13px] font-semibold tracking-[-0.1px] text-halo-ink">
            {t('settings.logout')}
          </span>
          <svg width="18" height="18" viewBox="0 0 24 24" className="block shrink-0 text-halo-ink-dimmer">
            <path
              d="M14 4h4a2 2 0 0 1 2 2v12a2 2 0 0 1-2 2h-4M10 8l-4 4 4 4m-4-4h12"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            />
          </svg>
        </button>
      </div>
    </aside>
  )
}
