type Status = 'green' | 'yellow' | 'red' | 'insufficient_data'

const STYLES: Record<Status, string> = {
  green: 'bg-[#22c55e20] text-green',
  yellow: 'bg-[#f59e0b20] text-yellow',
  red: 'bg-[#ef444420] text-red',
  insufficient_data: 'bg-[#88888820] text-text-dim',
}

interface StatusBadgeProps {
  status: Status
  label: string
}

export default function StatusBadge({ status, label }: StatusBadgeProps) {
  return (
    <span className={`inline-block px-2.5 py-0.5 rounded-md text-xs font-semibold ${STYLES[status]}`}>
      {label}
    </span>
  )
}
