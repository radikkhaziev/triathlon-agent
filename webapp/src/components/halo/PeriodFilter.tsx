import SegmentedTabs from './SegmentedTabs'

// Canonical period window shared by every trend screen (Wellness Recovery /
// Sleep / Body / Load detail + the Dashboard Load tab).
export type PeriodRange = '1m' | '3m' | '6m' | '1y'

const PERIOD_TABS: { key: PeriodRange; label: string }[] = [
  { key: '1m', label: '1M' },
  { key: '3m', label: '3M' },
  { key: '6m', label: '6M' },
  { key: '1y', label: '1Y' },
]

/**
 * Period filter — the surface-2 segmented control (`SegmentedTabs`), one
 * source of truth so every trend screen's 1M/3M/6M/1Y switch stays identical.
 */
export default function PeriodFilter({
  value,
  onChange,
}: {
  value: PeriodRange
  onChange: (v: PeriodRange) => void
}) {
  return <SegmentedTabs tabs={PERIOD_TABS} active={value} onChange={onChange} />
}
