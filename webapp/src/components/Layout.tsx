import { type ReactNode } from 'react'
import { Link } from 'react-router-dom'

interface LayoutProps {
  children: ReactNode
  title?: string
  backTo?: string
  backLabel?: string
  maxWidth?: string
}

export default function Layout({ children, title, backTo, backLabel = 'Главная', maxWidth = '540px' }: LayoutProps) {
  return (
    <div className="px-4 pb-8 mx-auto" style={{ maxWidth }}>
      {backTo && (
        <Link to={backTo} className="inline-flex items-center gap-1 text-[13px] text-accent no-underline pt-3">
          &larr; {backLabel}
        </Link>
      )}
      {title && (
        <div className="text-center py-4">
          <h1 className="text-xl font-bold">{title}</h1>
        </div>
      )}
      {children}
    </div>
  )
}
