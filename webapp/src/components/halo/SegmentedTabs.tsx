interface Tab<K extends string> {
  key: K
  label: string
}

/**
 * Surface-2 segmented pill toggle (prototype Dashboard Goal/Load/Recap and
 * the Plan Week/Plan switch). Active segment lifts to a white pill with a
 * barely-there shadow.
 */
export default function SegmentedTabs<K extends string>({
  tabs,
  active,
  onChange,
  className = '',
}: {
  tabs: Tab<K>[]
  active: K
  onChange: (k: K) => void
  className?: string
}) {
  return (
    <div className={`flex rounded-chip bg-halo-surface-2 p-[3px] ${className}`}>
      {tabs.map(t => {
        const on = t.key === active
        return (
          <button
            key={t.key}
            type="button"
            onClick={() => onChange(t.key)}
            aria-pressed={on}
            className={`flex-1 cursor-pointer rounded-[9px] border-none py-2 text-center text-[13px] font-semibold transition-colors ${
              on
                ? 'bg-halo-surface text-halo-ink shadow-[0_1px_2px_rgba(0,0,0,0.06)]'
                : 'bg-transparent text-halo-ink-dim'
            }`}
          >
            {t.label}
          </button>
        )
      })}
    </div>
  )
}
