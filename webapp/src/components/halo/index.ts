// Halo redesign primitives — namespaced, opt-in. Screens import these during
// their full-fidelity structural port (see docs/WEBAPP_HALO_REDESIGN_SPEC.md).
// See ./README.md for the cleanup contract.
export { default as Card } from './Card'
export { default as MicroLabel } from './MicroLabel'
export { default as TopBar } from './TopBar'
export { default as HaloBottomTabs } from './BottomTabs'
export { default as Gauge } from './Gauge'
export { default as MiniRangeGauge } from './MiniRangeGauge'
export { default as Donut } from './Donut'
export { default as StackedBar } from './StackedBar'
export { default as TaperBar } from './TaperBar'
export { default as ESSScale } from './ESSScale'
export { default as SegmentedTabs } from './SegmentedTabs'
export { default as PeriodFilter } from './PeriodFilter'
export type { PeriodRange } from './PeriodFilter'
export { default as DateStrip } from './DateStrip'
export { default as ToggleTile } from './ToggleTile'
export { default as SegmentedCodeInput } from './SegmentedCodeInput'
export { default as PhotoStrip } from './PhotoStrip'
export * from './geometry'
export { useChartScrubber, ChartScrubLine, fmtScrubDate } from './ChartScrubber'
export { InfoIcon, InfoPanel } from './InfoTip'
export {
  EnduranceScoreCard,
  EnduranceGauge,
  BadgePlate as EnduranceBadgePlate,
  ENDURANCE_ZONES,
  ENDURANCE_MAX,
  zoneFor as enduranceZoneFor,
} from './EnduranceScore'
export type { EnduranceZoneDef } from './EnduranceScore'
export { default as BottomSheet } from './BottomSheet'
export type { DatePill } from './DateStrip'
export type { ScrubItem } from './ChartScrubber'
