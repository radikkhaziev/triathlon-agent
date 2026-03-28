interface Tab {
  key: string
  label: string
  dot?: boolean
}

interface TabSwitcherProps {
  tabs: Tab[]
  active: string
  onChange: (key: string) => void
}

export default function TabSwitcher({ tabs, active, onChange }: TabSwitcherProps) {
  return (
    <div className="flex gap-1 mb-2.5">
      {tabs.map(tab => (
        <button
          key={tab.key}
          onClick={() => onChange(tab.key)}
          className={`px-3 py-1 rounded-md border text-xs cursor-pointer font-sans transition-all ${
            active === tab.key
              ? 'bg-[var(--button)] text-[var(--button-text)] border-[var(--button)]'
              : 'bg-transparent text-text border-border'
          }`}
        >
          {tab.label}
          {tab.dot && (
            <span className="inline-block w-[5px] h-[5px] rounded-full bg-green ml-1 align-middle" />
          )}
        </button>
      ))}
    </div>
  )
}
