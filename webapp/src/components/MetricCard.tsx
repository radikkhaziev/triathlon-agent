interface MetricCardProps {
  label: string
  value: string
  sub?: string
  subClass?: string
}

export default function MetricCard({ label, value, sub, subClass }: MetricCardProps) {
  return (
    <div className="bg-[var(--tg-theme-bg-color,var(--bg))] border border-border rounded-[10px] px-3 py-2.5">
      <div className="text-[11px] text-text-dim uppercase tracking-wide">{label}</div>
      <div className="text-lg font-bold mt-0.5">{value}</div>
      {sub && <div className={`text-[11px] text-text-dim mt-px ${subClass || ''}`}>{sub}</div>}
    </div>
  )
}
