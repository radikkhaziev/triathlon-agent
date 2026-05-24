import { clamp } from './geometry'

/**
 * ESS / external-stress scale (prototype Activity·race). Relative 0–200
 * scale with a tick at 100 ("1h at LTHR"). Real ESS often exceeds 100 — the
 * fill clamps but the value is shown raw (spec F5: no silent clip).
 */
export default function ESSScale({ value }: { value: number }) {
  const fill = clamp((value / 200) * 100, 0, 100)
  const color =
    value > 150 ? 'var(--color-coral)' : value > 80 ? 'var(--color-amber)' : 'var(--color-brand)'
  return (
    <div className="max-w-[160px] flex-1">
      <div className="relative h-2 rounded bg-halo-surface-2">
        <div
          className="absolute left-0 top-0 bottom-0 rounded"
          style={{ width: `${fill}%`, background: color }}
        />
        <div
          className="absolute left-1/2 bg-halo-ink-dimmer opacity-50"
          style={{ top: -3, bottom: -3, width: 2 }}
        />
      </div>
      <div className="mt-1 flex justify-between text-[9px] font-semibold tracking-[0.3px] text-halo-ink-dimmer">
        <span>0</span>
        <span>100</span>
        <span>200+</span>
      </div>
    </div>
  )
}
