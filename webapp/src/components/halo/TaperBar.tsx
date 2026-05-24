import { taperFill } from './geometry'

/**
 * Per-sport CTL progress with taper overshoot (prototype Dashboard·Goal
 * "By sport"). When current > target the bar fills 100% and shows a faded
 * dashed overshoot tail; a `taper` chip is rendered by the caller in the
 * row caption. Geometry via `taperFill` (capped 40%).
 */
export default function TaperBar({
  current,
  target,
  color,
  height = 8,
}: {
  current: number
  target: number
  color: string
  height?: number
}) {
  const { over, pct, overPct } = taperFill(current, target)
  return (
    <div
      className="relative mt-1.5 flex overflow-hidden rounded-pill"
      style={{ height, background: 'var(--color-surface-2)' }}
    >
      <div style={{ width: `${pct}%`, background: color }} />
      {over && (
        <div
          style={{
            width: `${overPct}%`,
            background: color,
            opacity: 0.35,
            borderLeft: '2px dashed var(--color-surface)',
          }}
        />
      )}
    </div>
  )
}
