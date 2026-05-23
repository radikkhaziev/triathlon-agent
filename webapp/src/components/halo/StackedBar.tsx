import type { CSSProperties } from 'react'

interface Segment {
  /** Flex weight (prototype uses raw values, e.g. swim 8.5 / ride 38.2). */
  flex: number
  color: string
}

/**
 * Segmented horizontal bar. Two prototype uses:
 *  - Wellness training-load: swim/ride/run flex segments.
 *  - Dashboard·Load TSB band: 5 fixed zones + a current-value marker
 *    (risk / optimal / gray / fresh / transition — see LoadDetail.tsx::TSB_ZONES).
 */
export default function StackedBar({
  segments,
  height = 10,
  rounded = true,
  track = 'var(--color-surface-2)',
  /** Marker position 0..100 (% across the bar) — TSB current value. */
  marker,
  markerColor,
}: {
  segments: Segment[]
  height?: number
  rounded?: boolean
  track?: string
  marker?: number
  markerColor?: string
}) {
  return (
    <div
      className={`relative flex overflow-hidden ${rounded ? 'rounded-pill' : ''}`}
      style={{ height, background: track }}
    >
      {segments.map((s, i) => (
        <div key={i} style={{ flex: s.flex, background: s.color } as CSSProperties} />
      ))}
      {marker != null && (
        <div
          className="absolute"
          style={{
            left: `${Math.max(0, Math.min(100, marker))}%`,
            top: -3,
            bottom: -3,
            width: 2,
            background: markerColor ?? 'var(--color-ink)',
            boxShadow: '0 0 0 2px var(--color-surface)',
          }}
        />
      )}
    </div>
  )
}
