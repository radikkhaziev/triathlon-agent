import { ZONE_COLORS, ZONE_LABELS } from '../lib/constants'

interface ZoneBarProps {
  zones: number[]
  label: string
  /**
   * ``"list"`` — compact 16px bar with inline labels, for activity rows.
   * ``"detail"`` — taller 24px bar with bigger labels and per-zone summary
   * grid, for the Activity detail page. Defaults to ``"list"`` for back-compat.
   */
  size?: 'list' | 'detail'
}

export default function ZoneBar({ zones, label, size = 'list' }: ZoneBarProps) {
  const total = zones.reduce((a, b) => a + (b || 0), 0)
  if (total <= 0) return null

  const isDetail = size === 'detail'
  // Walk every zone (up to 7) — power_zone_times for Ride goes Z1..Z7. Older
  // ZoneBar capped at 5 which dropped Z6/Z7 silently.
  const visibleZones = zones.slice(0, 7)

  const barHeight = isDetail ? 'h-6' : 'h-4'
  const barRadius = isDetail ? 'rounded-md' : 'rounded'
  const inlineLabelMin = isDetail ? 5 : 8
  const labelFontSize = isDetail ? 'text-[11px]' : 'text-[9px]'
  const summaryFontSize = isDetail ? 'text-[12px]' : 'text-[10px]'

  return (
    <div className={isDetail ? 'mb-3 w-full' : 'my-1.5 w-full'}>
      <div className={`flex items-baseline justify-between mb-1 ${isDetail ? 'text-[12px]' : 'text-[11px]'} text-text-dim`}>
        <span>{label}</span>
        {isDetail && <span>{Math.round(total / 60)}m total</span>}
      </div>
      <div className={`flex ${barHeight} ${barRadius} overflow-hidden bg-surface-2 w-full`}>
        {visibleZones.map((v, i) => {
          const pct = ((v || 0) / total) * 100
          if (pct < 0.5) return null
          const zoneLabel = ZONE_LABELS[i] ?? `Z${i + 1}`
          const inline = pct >= inlineLabelMin ? `${zoneLabel} ${Math.round(pct)}%` : ''
          return (
            <div
              key={i}
              className={`flex items-center justify-center font-semibold text-white overflow-hidden whitespace-nowrap ${labelFontSize}`}
              style={{ width: `${pct}%`, background: ZONE_COLORS[i % ZONE_COLORS.length] }}
            >
              {inline}
            </div>
          )
        })}
      </div>
      {isDetail ? (
        // Detail page: per-zone grid with mins + percent — fits 3-4 zones per row on mobile.
        <div className="grid grid-cols-3 sm:grid-cols-5 gap-x-3 gap-y-1 mt-1.5">
          {visibleZones.map((v, i) => {
            if ((v || 0) <= 0) return null
            const mins = Math.round((v || 0) / 60)
            const pct = Math.round(((v || 0) / total) * 100)
            const zoneLabel = ZONE_LABELS[i] ?? `Z${i + 1}`
            return (
              <span key={i} className={`${summaryFontSize} flex items-center gap-1.5`}>
                <span
                  className="inline-block w-2.5 h-2.5 rounded-sm shrink-0"
                  style={{ background: ZONE_COLORS[i % ZONE_COLORS.length] }}
                />
                <span className="text-text-dim">{zoneLabel}</span>
                <span className="font-semibold">{mins}m</span>
                <span className="text-text-dim">({pct}%)</span>
              </span>
            )
          })}
        </div>
      ) : (
        <div className="flex gap-2 flex-wrap mt-0.5">
          {visibleZones.map((v, i) => {
            if ((v || 0) <= 0) return null
            const mins = Math.round((v || 0) / 60)
            const zoneLabel = ZONE_LABELS[i] ?? `Z${i + 1}`
            return (
              <span key={i} className={`${summaryFontSize} text-text-dim`}>
                <span
                  className="inline-block w-2 h-2 rounded-sm mr-0.5 align-middle"
                  style={{ background: ZONE_COLORS[i % ZONE_COLORS.length] }}
                />
                {zoneLabel} {mins}m
              </span>
            )
          })}
        </div>
      )}
    </div>
  )
}
