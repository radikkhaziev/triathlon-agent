import { num } from '../lib/formatters'

interface SportCtlBarsProps {
  swim: number | null
  ride: number | null
  run: number | null
}

const sports = [
  { key: 'swim' as const, emoji: '🏊', name: 'Swim' },
  { key: 'ride' as const, emoji: '🚴', name: 'Ride' },
  { key: 'run' as const, emoji: '🏃', name: 'Run' },
]

export default function SportCtlBars({ swim, ride, run }: SportCtlBarsProps) {
  const values = { swim, ride, run }
  const maxCtl = Math.max(swim || 0, ride || 0, run || 0, 1)

  return (
    <div>
      <div className="text-[12px] text-text-dim uppercase tracking-wide my-2">Per-sport CTL</div>
      {sports.map(s => {
        const v = values[s.key]
        if (v == null) return null
        const pct = Math.min(100, (v / maxCtl) * 100)
        return (
          <div key={s.key} className="flex items-center gap-2 py-1.5">
            <span className="text-base w-6 text-center">{s.emoji}</span>
            <span className="text-[13px] w-10">{s.name}</span>
            <div className="flex-1 h-1.5 bg-border rounded-full overflow-hidden">
              <div
                className="h-full rounded-full bg-[var(--button)] transition-[width] duration-400"
                style={{ width: `${pct}%` }}
              />
            </div>
            <span className="text-[13px] font-semibold w-10 text-right">{num(v)}</span>
          </div>
        )
      })}
    </div>
  )
}
