import { type ReactNode } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import BottomTabs from './BottomTabs'
import Sidebar from './Sidebar'

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
      {showNav && <Sidebar />}
      <div className={`${showNav ? 'md:pl-56' : ''}`}>
        <div className={`px-4 mx-auto ${hideBottomTabs ? 'pb-8' : 'pb-20 md:pb-8'}`} style={{ maxWidth }}>
          {backTo && (
            <Link to={backTo} className="inline-flex items-center gap-1 text-[13px] text-accent no-underline pt-3">
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
      {showNav && <BottomTabs />}
    </>
  )
}
