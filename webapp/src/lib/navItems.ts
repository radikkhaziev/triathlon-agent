/**
 * Single source of truth for app navigation.
 *
 * Both the mobile `BottomTabs` (primary bar + More menu) and the desktop
 * `Sidebar` derive their items from here — keep routes, labels, icons and
 * ordering in sync by editing this one file.
 */

export interface NavItem {
  path: string
  labelKey: string
  icon: string
}

/** Primary mobile tabs — shown in the bottom bar on phones. */
export const PRIMARY_NAV_ITEMS: NavItem[] = [
  { path: '/', labelKey: 'nav.today', icon: '🏠' },
  { path: '/plan', labelKey: 'nav.plan', icon: '📋' },
  { path: '/activities', labelKey: 'nav.activities', icon: '🏃' },
  { path: '/wellness', labelKey: 'nav.wellness', icon: '💚' },
]

/** Secondary items — hidden behind the "More" button on mobile. */
export const MORE_NAV_ITEMS: NavItem[] = [
  { path: '/progress', labelKey: 'nav.progress', icon: '📈' },
  { path: '/dashboard', labelKey: 'nav.dashboard', icon: '📊' },
  { path: '/settings', labelKey: 'nav.settings', icon: '⚙️' },
]

/** Every navigable item in a single flat list — used by the desktop sidebar. */
export const ALL_NAV_ITEMS: NavItem[] = [...PRIMARY_NAV_ITEMS, ...MORE_NAV_ITEMS]
