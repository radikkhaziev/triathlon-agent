import { useRef, useState, type PointerEvent } from 'react'

/**
 * Chart scrubber — a vertical crosshair + floating value callout shared by
 * every time-series chart on the Wellness detail screens (Recovery / Sleep /
 * Body / Load). The user drags or taps over the plot area; the rule snaps to
 * the nearest data point and the callout shows the date + per-series values.
 *
 * Port of the design's `useChartScrubber` / `ChartScrubLine` / `fmtScrubDate`
 * (design-package/endurai/direction-b-halo.jsx).
 */

export interface ScrubItem {
  /** Series name, e.g. `"CTL"`. Empty string for a single-series chart. */
  label: string
  value: string | number
  color: string
}

/**
 * Pointer → nearest data index. `clientX` is normalized against the live
 * `svg.viewBox.baseVal.width`, so the hit test works regardless of whether the
 * viewBox is fixed (legacy MiniRangeGauge) or measured per-frame (all line/bar
 * charts). `n` is the number of data points (or bars).
 */
export function useChartScrubber(n: number, padL: number, innerW: number) {
  const svgRef = useRef<SVGSVGElement | null>(null)
  const [idx, setIdx] = useState<number | null>(null)

  const move = (e: PointerEvent<SVGSVGElement>) => {
    const svg = svgRef.current
    if (!svg || n <= 0) return
    const rect = svg.getBoundingClientRect()
    if (rect.width === 0) return
    const vbW = svg.viewBox.baseVal.width || padL + innerW
    const px = ((e.clientX - rect.left) / rect.width) * vbW
    const t = innerW > 0 ? (px - padL) / innerW : 0
    setIdx(Math.max(0, Math.min(n - 1, Math.round(t * (n - 1)))))
  }
  const leave = () => setIdx(null)

  const handlers = {
    onPointerDown: (e: PointerEvent<SVGSVGElement>) => {
      // Capture so a drag that leaves the SVG keeps scrubbing; harmless if it
      // throws (pointer already released).
      try {
        e.currentTarget.setPointerCapture(e.pointerId)
      } catch {
        /* noop */
      }
      move(e)
    },
    onPointerMove: move,
    onPointerUp: leave,
    onPointerLeave: leave,
    onPointerCancel: leave,
    // pan-y keeps vertical page scroll working while a horizontal drag scrubs.
    style: { touchAction: 'pan-y' as const, cursor: 'crosshair' as const },
  }

  return { svgRef, idx, handlers }
}

/** `YYYY-MM-DD` → `"Sat 04/11"` for the scrub callout. */
const WEEKDAYS = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
export function fmtScrubDate(ymd: string | undefined | null): string {
  if (!ymd) return ''
  const [y, m, d] = ymd.split('-').map(Number)
  if (!y || !m || !d) return ''
  return `${WEEKDAYS[new Date(y, m - 1, d).getDay()]} ${String(m).padStart(2, '0')}/${String(d).padStart(2, '0')}`
}

/**
 * Vertical rule + floating callout box. Renders nothing when `idx` is null.
 * `x` maps a data index to its x coordinate (line charts → the point; bar
 * charts → the bar centre). The callout flips to the left of the rule when it
 * would overflow the right edge.
 */
export function ChartScrubLine({
  idx,
  dateLabel,
  items,
  x,
  padT,
  innerH,
  W,
  padR = 6,
  color = '#0a0d18',
}: {
  idx: number | null
  dateLabel: string
  items: ScrubItem[]
  x: (i: number) => number
  padT: number
  innerH: number
  W: number
  padR?: number
  color?: string
}) {
  if (idx == null) return null
  const px = x(idx)
  const charW = 5.0
  const lineH = 11
  const dateRowH = 12
  const padX = 7
  const padY = 5
  const longest = Math.max(
    dateLabel.length * charW + 2,
    ...items.map(it => (String(it.label).length + String(it.value).length + 1) * charW + 14),
  )
  const boxW = Math.max(54, Math.ceil(longest) + padX * 2)
  const boxH = padY * 2 + dateRowH + items.length * lineH
  let bx = px + 8
  if (bx + boxW > W - padR) bx = px - boxW - 8
  if (bx < 2) bx = 2
  const by = padT + 2
  return (
    <g pointerEvents="none">
      <line x1={px} y1={padT} x2={px} y2={padT + innerH} stroke={color} strokeWidth="1" opacity="0.6" />
      <rect
        x={bx}
        y={by}
        width={boxW}
        height={boxH}
        rx="4"
        fill="#fff"
        stroke="rgba(10,13,24,0.18)"
        strokeWidth="1"
        style={{ filter: 'drop-shadow(0 1px 2px rgba(10,13,24,0.12))' }}
      />
      <text x={bx + padX} y={by + padY + 8} fontSize="9" fontWeight="700" fill={color}>
        {dateLabel}
      </text>
      {items.map((it, i) => (
        <g key={i}>
          <circle cx={bx + padX + 3} cy={by + padY + dateRowH + lineH * i + 4} r="2.6" fill={it.color} />
          <text x={bx + padX + 11} y={by + padY + dateRowH + lineH * i + 8} fontSize="9" fill={color}>
            <tspan opacity="0.6">{it.label}</tspan>
            <tspan dx="3" fontWeight="700">
              {it.value}
            </tspan>
          </text>
        </g>
      ))}
    </g>
  )
}
