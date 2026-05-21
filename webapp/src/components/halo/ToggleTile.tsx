/**
 * Sport toggle tile (prototype `BSportsPicker`): 46px tinted sport-initial
 * avatar + label + an iOS-style sliding switch. When on, the tile takes a
 * faint sport-colour wash + border. Presentational; caller owns selection.
 */
export default function ToggleTile({
  label,
  color,
  on,
  onToggle,
  initial,
  disabled = false,
}: {
  label: string
  color: string
  on: boolean
  onToggle: () => void
  initial: string
  disabled?: boolean
}) {
  return (
    <button
      type="button"
      onClick={onToggle}
      disabled={disabled}
      aria-pressed={on}
      className="flex w-full items-center gap-3.5 rounded-card border p-3.5 text-left transition-colors disabled:opacity-60"
      style={{
        borderColor: on ? color : 'var(--color-border)',
        background: on ? `color-mix(in srgb, ${color} 8%, transparent)` : 'var(--color-surface)',
      }}
    >
      <span
        className="inline-flex h-[46px] w-[46px] shrink-0 items-center justify-center rounded-full text-base font-bold"
        style={{ color, background: `color-mix(in srgb, ${color} 14%, transparent)` }}
      >
        {initial}
      </span>
      <span className="flex-1 text-[15px] font-semibold text-halo-ink">{label}</span>
      <span
        className="relative inline-block shrink-0 rounded-pill transition-colors"
        style={{
          width: 48,
          height: 28,
          background: on ? color : 'var(--color-surface-2)',
        }}
      >
        <span
          className="absolute top-0.5 rounded-full bg-white shadow transition-all"
          style={{ width: 24, height: 24, left: on ? 22 : 2 }}
        />
      </span>
    </button>
  )
}
