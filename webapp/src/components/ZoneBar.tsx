import { ZONE_COLORS, ZONE_LABELS } from '../lib/constants'

interface ZoneBarProps {
  zones: number[]
  label: string
}

export default function ZoneBar({ zones, label }: ZoneBarProps) {
  const total = zones.reduce((a, b) => a + (b || 0), 0)
  if (total <= 0) return null

  return (
    <div className="my-1.5">
      <div className="text-[11px] text-text-dim mb-0.5">{label}</div>
      <div className="flex h-4 rounded overflow-hidden bg-surface-2">
        {zones.slice(0, 5).map((v, i) => {
          const pct = ((v || 0) / total) * 100
          if (pct < 1) return null
          const lbl = pct >= 8 ? `${ZONE_LABELS[i]} ${Math.round(pct)}%` : ''
          return (
            <div
              key={i}
              className="flex items-center justify-center text-[9px] font-semibold text-white overflow-hidden whitespace-nowrap"
              style={{ width: `${pct}%`, background: ZONE_COLORS[i] }}
            >
              {lbl}
            </div>
          )
        })}
      </div>
      <div className="flex gap-2 flex-wrap mt-0.5">
        {zones.slice(0, 5).map((v, i) => {
          if ((v || 0) <= 0) return null
          const mins = Math.round((v || 0) / 60)
          return (
            <span key={i} className="text-[10px] text-text-dim">
              <span
                className="inline-block w-2 h-2 rounded-sm mr-0.5 align-middle"
                style={{ background: ZONE_COLORS[i] }}
              />
              {ZONE_LABELS[i]} {mins}m
            </span>
          )
        })}
      </div>
    </div>
  )
}
