import { formatWeekLabel } from '../lib/formatters'

interface WeekNavProps {
  weekStart: string
  weekEnd: string
  hasPrev: boolean
  hasNext: boolean
  onPrev: () => void
  onNext: () => void
}

export default function WeekNav({ weekStart, weekEnd, hasPrev, hasNext, onPrev, onNext }: WeekNavProps) {
  return (
    <div className="flex items-center justify-center gap-3 py-2">
      <button
        onClick={onPrev}
        disabled={!hasPrev}
        className="bg-surface-2 border border-border text-text rounded-lg px-3.5 py-2 text-[13px] font-semibold cursor-pointer transition-all hover:bg-border active:scale-[0.97] disabled:opacity-30 disabled:cursor-default font-sans"
      >
        &larr; Пред
      </button>
      <span className="text-sm font-semibold min-w-[180px] text-center">
        {formatWeekLabel(weekStart, weekEnd)}
      </span>
      <button
        onClick={onNext}
        disabled={!hasNext}
        className="bg-surface-2 border border-border text-text rounded-lg px-3.5 py-2 text-[13px] font-semibold cursor-pointer transition-all hover:bg-border active:scale-[0.97] disabled:opacity-30 disabled:cursor-default font-sans"
      >
        След &rarr;
      </button>
    </div>
  )
}
