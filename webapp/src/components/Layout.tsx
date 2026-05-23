import { type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import HaloBottomTabs from './halo/BottomTabs'
import HaloSidebar from './halo/HaloSidebar'
import { HALO_BOTTOM_TABS } from '../lib/navItems'

interface LayoutProps {
  children: ReactNode
  title?: string
  backTo?: string
  backLabel?: string
  maxWidth?: string
  hideBottomTabs?: boolean
}

export default function Layout({
  children,
  title,
  backTo,
  backLabel,
  maxWidth = '540px',
  hideBottomTabs = false,
}: LayoutProps) {
  const { t } = useTranslation()
  const label = backLabel || t('common.home')
  const showNav = !hideBottomTabs
  return (
    <>
      {showNav && <HaloSidebar />}
      <div className={`${showNav ? 'md:pl-60' : ''}`}>
        {/* Mobile: page-supplied maxWidth (inline style). Desktop: the
            prototype `BdShell` canvas — 1180px (≈1100 content + 36px gutters),
            left-aligned beside the sidebar (no mx-auto). The `md:!max-w`
            !important beats the non-important inline mobile cap. */}
        <div
          className={`px-4 pt-4 mx-auto md:mx-0 md:!max-w-[1180px] ${hideBottomTabs ? 'pb-8' : 'pb-20 md:pb-8'}`}
          style={{ maxWidth }}
        >
          {backTo && (
            <Link to={backTo} className="inline-flex items-center gap-1 text-[13px] text-halo-ink-dim no-underline pt-3">
              &larr; {label}
            </Link>
          )}
          {title && (
            <div className="text-center py-4">
              <h1 className="text-xl font-bold">{title}</h1>
            </div>
          )}
          {children}
        </div>
      </div>
      {showNav && (
        <HaloBottomTabs
          items={HALO_BOTTOM_TABS}
          className="fixed bottom-0 left-0 right-0 z-50 md:hidden"
        />
      )}
    </>
  )
}
