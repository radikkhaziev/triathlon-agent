import type { ReactNode } from 'react'

interface CardProps {
  children: ReactNode
  /**
   * `hero`   = brand cobalt fill, white text (workout hero, CTA blocks).
   * `heroInk`= near-black ink fill, white text (race recap hero).
   */
  variant?: 'surface' | 'hero' | 'heroInk'
  className?: string
}

/**
 * Halo card primitive (README §4).
 *   surface — white surface, 20px radius, hairline border, barely-there shadow
 *   hero    — brand cobalt fill, white text, no border
 *   heroInk — ink fill, white text, no border (race detail hero)
 * Padding intentionally lives here (18px) so screens stay rhythm-consistent;
 * override via className when a card needs a custom inner layout.
 */
export default function Card({
  children,
  variant = 'surface',
  className = '',
}: CardProps) {
  const base = 'rounded-card p-[18px]'
  const tone =
    variant === 'hero'
      ? 'bg-halo-brand text-white'
      : variant === 'heroInk'
        ? 'bg-halo-ink text-white'
        : 'bg-halo-surface text-halo-ink border border-halo-border shadow-card'
  return <div className={`${base} ${tone} ${className}`}>{children}</div>
}
