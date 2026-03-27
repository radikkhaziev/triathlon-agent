import { num } from '../lib/formatters'

interface SportCtlBarsProps {
  swim: number | null
  bike: number | null
  run: number | null
}

const sports = [
  { key: 'swim' as const, emoji: '🏊', name: 'Swim' },
  { key: 'bike' as const, emoji: '🚴', name: 'Bike' },
  { key: 'run' as const, emoji: '🏃', name: 'Run' },
]

export default function SportCtlBars({ swim, bike, run }: SportCtlBarsProps) {
  const values = { swim, bike, run }
  const maxCtl = Math.max(swim || 0, bike || 0, run || 0, 1)

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
