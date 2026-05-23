import { type ReactNode } from 'react'

export interface DatePill {
  key: string
  /** Final label, e.g. `Thu 14` or `Today · Sat 16` (caller builds it). */
  label: string
  /** Selected day → cobalt fill + white. */
  today?: boolean
  /** Future days render dimmer and (caller's choice) non-interactive. */
  future?: boolean
}

/**
 * Horizontal scroll of soft day pills (prototype `BWellness` date strip).
 * Selected = cobalt fill + white; others = hairline-bordered transparent;
 * future = dimmer. The caller builds the window + labels and handles
 * navigation (`useDayNav`).
 *
 * `leading` renders before the day pills in the same row — the Wellness strip
 * uses it for the "All history" pill that opens the calendar heatmap.
 */
export default function DateStrip({
  pills,
  onPick,
  leading,
}: {
  pills: DatePill[]
  onPick: (key: string) => void
  leading?: ReactNode
}) {
  return (
    <div className="flex items-center gap-1.5 overflow-x-auto pb-3">
      {leading}
      {pills.map(p => (
        <button
          key={p.key}
          type="button"
          disabled={p.future}
          onClick={() => onPick(p.key)}
          className={`whitespace-nowrap rounded-pill px-3 py-2 text-xs font-semibold ${
            p.today
              ? 'border-none bg-halo-brand text-white'
              : `border border-halo-border bg-transparent ${
                  p.future ? 'text-halo-ink-dimmer' : 'text-halo-ink-dim'
                }`
          }`}
        >
          {p.label}
        </button>
      ))}
    </div>
  )
}
