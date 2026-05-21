import type { ReactNode } from 'react'
import { donutSegments } from './geometry'

/**
 * Stacked-arc donut (prototype HR-zone / polarization donuts). Cumulative
 * segments via `donutSegments`. Inline SVG one-off gauge (brief §2 / README
 * §7 — gauges/donuts stay SVG; only line/scatter route through Chart.js).
 */
export default function Donut({
  values,
  colors,
  size = 100,
  r = 38,
  strokeWidth = 14,
  trackColor = 'var(--color-surface-2)',
  center,
}: {
  values: number[]
  colors: string[]
  size?: number
  r?: number
  strokeWidth?: number
  trackColor?: string
  center?: ReactNode
}) {
  const C = 2 * Math.PI * r
  const segs = donutSegments(values, C)
  return (
    <div className="relative inline-flex items-center justify-center">
      <svg width={size} height={size} viewBox={`${-size / 2} ${-size / 2} ${size} ${size}`}>
        <circle r={r} fill="none" stroke={trackColor} strokeWidth={strokeWidth} />
        {segs.map((s, i) => (
          <circle
            key={i}
            r={r}
            fill="none"
            stroke={colors[i % colors.length]}
            strokeWidth={strokeWidth}
            strokeDasharray={`${s.dash} ${s.gap}`}
            strokeDashoffset={s.offset}
            transform="rotate(-90)"
          />
        ))}
      </svg>
      {center && (
        <div className="pointer-events-none absolute inset-0 flex flex-col items-center justify-center text-center">
          {center}
        </div>
      )}
    </div>
  )
}
