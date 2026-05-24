import { rangePct } from './geometry'

/**
 * HRV/RHR mini range gauge (prototype `BWellness`): a thin track with the
 * current value as a tick, lo/hi labels at the ends. Status colour drives
 * the tick. Inline SVG (one-off gauge, brief §2).
 */
export default function MiniRangeGauge({
  lo,
  hi,
  cur,
  color,
  loLabel,
  hiLabel,
}: {
  lo: number
  hi: number
  cur: number
  color: string
  loLabel?: string
  hiLabel?: string
}) {
  const pct = rangePct(cur, lo, hi)
  return (
    <div className="relative mt-2.5 h-7">
      <svg width="100%" height="28" viewBox="0 0 140 28" preserveAspectRatio="none">
        <rect x="0" y="11" width="140" height="6" rx="3" fill="var(--color-surface-2)" />
        <rect x={pct * 140 - 1} y="6" width="2" height="16" fill={color} />
      </svg>
      <div className="absolute bottom-[-4px] left-0 text-[9px] text-halo-ink-dimmer">
        {loLabel ?? lo}
      </div>
      <div className="absolute bottom-[-4px] right-0 text-[9px] text-halo-ink-dimmer">
        {hiLabel ?? hi}
      </div>
    </div>
  )
}
