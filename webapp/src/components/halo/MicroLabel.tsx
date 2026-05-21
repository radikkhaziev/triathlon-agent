import type { ReactNode } from 'react'

/**
 * Halo micro label — 11px, 600, uppercase, +0.6 tracking, dim.
 * Section eyebrow used across every Halo card (README §4).
 */
export default function MicroLabel({
  children,
  className = '',
}: {
  children: ReactNode
  className?: string
}) {
  return (
    <span
      className={`text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim ${className}`}
    >
      {children}
    </span>
  )
}
