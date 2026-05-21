import type { ReactNode } from 'react'
import { arcPath, pointAtPct } from './geometry'

interface GaugeProps {
  /** SVG box (prototype: recovery 240×220, goal 180×150). */
  width: number
  height: number
  cx: number
  cy: number
  r: number
  strokeWidth: number
  /** 0..100. `null` → ghost (dashed track, no progress) for empty-state. */
  value: number | null
  /** Progress stroke colour (category-driven by the caller). */
  color: string
  /** Track stroke colour (light category wash, or surface-2). */
  trackColor: string
  /** Tick-mark percent positions drawn over the bar in `--color-bg`. */
  ticks?: number[]
  /** Bottom-corner labels under the sweep ends (e.g. `['0','100']`). */
  endLabels?: [string, string]
  /**
   * Centre SVG content, given the gauge centre. Call sites emit the exact
   * `<text>`/`<tspan>` the prototype draws — keeps placement pixel-faithful
   * (prototype centres are asymmetric vs the box, so an overlay div drifts).
   */
  center?: (cx: number, cy: number) => ReactNode
}

/**
 * Halo 240° arc gauge (handoff README §7). Bottom-opening symmetric arc
 * (−120°→+120°; see the `start`/`end` note below). Used by the Wellness
 * recovery hero, the Dashboard goal arc, and the Wellness-empty ghost arc.
 * Inline SVG is sanctioned for one-off gauges (brief §2).
 */
export default function Gauge({
  width,
  height,
  cx,
  cy,
  r,
  strokeWidth,
  value,
  color,
  trackColor,
  ticks,
  endLabels,
  center,
}: GaugeProps) {
  // Bottom-opening symmetric 240° arc: 0% at the lower-left tip, 100% at the
  // lower-right tip, 50% at the top, gap centered at 6 o'clock. The design
  // package's `-210/+30` is a mock quirk — it renders a *right*-opening arc
  // while the `0`/`100` labels are hard-placed at the bottom corners
  // (README §7: "labels under the gauge ends"), so arc + scale didn't line
  // up. `-120/+120` is the symmetric bottom-opening gauge the design intends.
  const start = -120
  const end = 120
  const sweep = end - start
  const ghost = value == null
  const valEnd = start + ((value ?? 0) / 100) * sweep

  return (
    <svg
      width={width}
      height={height}
      viewBox={`0 0 ${width} ${height}`}
      className="overflow-visible"
    >
      <path
        d={arcPath(cx, cy, r, start, end)}
        fill="none"
        stroke={trackColor}
        strokeWidth={strokeWidth}
        strokeLinecap="round"
        strokeDasharray={ghost ? '2 8' : undefined}
      />
      {!ghost && (
        <path
          d={arcPath(cx, cy, r, start, valEnd)}
          fill="none"
          stroke={color}
          strokeWidth={strokeWidth}
          strokeLinecap="round"
        />
      )}
      {ticks?.map(p => {
        const [x1, y1] = pointAtPct(cx, cy, r - 12, start, end, p / 100)
        const [x2, y2] = pointAtPct(cx, cy, r + 12, start, end, p / 100)
        return (
          <line key={p} x1={x1} y1={y1} x2={x2} y2={y2} stroke="var(--color-bg)" strokeWidth="3" />
        )
      })}
      {endLabels && (
        <>
          <text x={cx - r - 6} y={cy + r - 6} textAnchor="middle" fontSize="10" fill="var(--color-ink-dimmer)">
            {endLabels[0]}
          </text>
          <text x={cx + r + 6} y={cy + r - 6} textAnchor="middle" fontSize="10" fill="var(--color-ink-dimmer)">
            {endLabels[1]}
          </text>
        </>
      )}
      {center?.(cx, cy)}
    </svg>
  )
}
