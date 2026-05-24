/**
 * Single source of truth for app navigation.
 *
 * Halo-v3 IA (2026-05-20, prototype `BBottomTabs` direction-b-halo.jsx:82-88):
 * **4 tabs everywhere** — Today / Week / Trends / Profile. «План» и
 * «Активности» merged в один Week tab (PlanScreen хостит `MergedWeek`
 * напрямую — мысль дизайнера: будущее = план, прошлое = факт; toggle
 * Week/Plan убран при design-reconcile 2026-05-23, Plan view retired).
 * `/weekly` остаётся как deep-link route (weekly-report Telegram-кнопка),
 * но не в primary nav. `/activities` list-route убран совсем (Week tab
 * закрывает кейс); detail-роут `/activity/:id` живёт как deep-link.
 * Старый `/progress` тоже retired — его контент в Load-табе.
 *
 * Routes (2026-05-23): tab paths переименованы под Halo IA — `/plan` →
 * `/calendar` (точнее описывает merge план+факт; уход от конфликта с
 * `/weekly`), `/dashboard` → `/trends` (URL = label). Legacy paths остаются
 * как `<Navigate>` редиректы в `App.tsx` — не ломают Telegram WebApp
 * кнопки, букмарки, ссылки в morning report. `/wellness` и `/settings`
 * оставлены: short, читаемые, конвенциональные.
 *
 * Применяется к обоим viewport'ам — мобильному `HaloBottomTabs` и
 * desktop `HaloSidebar` — чтобы IA была единой. Прототип `BdSidebar`
 * в desktop.jsx ещё показывает старые 7 пунктов, но user явно подтвердил
 * unified 4-tab IA по концепту merge'a.
 */

export interface NavItem {
  path: string
  labelKey: string
  icon: string
}

/**
 * The 4 primary nav items — единый список для desktop sidebar и mobile
 * bottom-tabs. Иконка только для legacy mobile More-menu rendering (Halo
 * `HaloSidebar` использует inline-SVG по `path`; `HaloBottomTabs` рендерит
 * халовский dot-indicator). Эмодзи здесь — backstop для surfaces, которые
 * до сих пор могли читать `icon` (в Halo — нигде).
 */
export const ALL_NAV_ITEMS: NavItem[] = [
  { path: '/wellness', labelKey: 'nav.today', icon: '💚' },
  { path: '/calendar', labelKey: 'nav.week', icon: '📋' },
  { path: '/trends', labelKey: 'nav.trends', icon: '📊' },
  { path: '/settings', labelKey: 'nav.profile', icon: '⚙️' },
]

/**
 * Halo mobile bottom-tab IA — те же 4 пункта, что и desktop sidebar
 * (Halo-v3 unified IA). Consumed by `<HaloBottomTabs>` mounted in Layout.
 */
export const HALO_BOTTOM_TABS: { path: string; labelKey: string }[] = [
  { path: '/wellness', labelKey: 'nav.today' },
  { path: '/calendar', labelKey: 'nav.week' },
  { path: '/trends', labelKey: 'nav.trends' },
  { path: '/settings', labelKey: 'nav.profile' },
]
