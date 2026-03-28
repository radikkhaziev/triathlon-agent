import { STATUS_BADGE_MAP } from '../lib/constants'

export default function StatusBadge({ status }: { status: string }) {
  const info = STATUS_BADGE_MAP[status] || STATUS_BADGE_MAP.insufficient_data
  return (
    <span className={`inline-block px-2.5 py-0.5 rounded-md text-xs font-semibold ${info.cls}`}>
      {info.label}
    </span>
  )
}
