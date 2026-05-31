// Endurance Score — full-screen detail. Tap from the Wellness home card.
//
// Two layouts:
//   · Mobile — single column, vertical stack (port of `BEnduranceScoreDetail`,
//     direction-b-halo.jsx:3641-3845).
//   · Desktop — 2-column grid `1fr 1.7fr` for row 1 (gauge + per-sport on the
//     left, zone-banded trend chart on the right) then a full-width zone
//     legend with descriptions below (port of `BdEnduranceDetail`,
//     direction-b-desktop.jsx:450-708).
//
// Backend trend is daily (not weekly as in the Halo mock). 1m/3m/6m/1y maps
// to 30/90/180/365 daily points. Dots render only on the 1m view (≤40 pts);
// longer windows mark only the latest point to keep the line readable.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import {
  Card,
  PeriodFilter,
  type PeriodRange,
  EnduranceGauge,
  EnduranceBadgePlate,
  ENDURANCE_ZONES,
  ENDURANCE_MAX,
  enduranceZoneFor,
  useChartScrubber,
  ChartScrubLine,
  fmtScrubDate,
  StackedBar,
  type ScrubItem,
} from '../components/halo'
import {
  ENDURANCE_SPORT_LABEL,
  sortPerSport,
  sortPerSportByShareDesc,
} from '../components/halo/EnduranceScore'
import { useApi } from '../hooks/useApi'
import { useMeasuredWidth } from '../hooks/useMeasuredWidth'
import { CHART_COLORS } from '../lib/constants'
import type { EnduranceScoreResponse } from '../api/types'

// Chart height is fixed; width is measured per-render via ResizeObserver so
// the SVG fills its card on mobile (~320px) and desktop (~680px) equally.
const H = 280
const PAD_L = 44
const PAD_R = 20
const PAD_T = 18
const PAD_B = 32
const INNER_H = H - PAD_T - PAD_B

const SPORT_COLOR: Record<string, string> = {
  Bike: CHART_COLORS.ride,
  Run: CHART_COLORS.run,
  Swim: CHART_COLORS.swim,
  Other: 'var(--color-ink-dimmer)',
}

// Parse `YYYY-MM-DD` into a local-TZ Date. `new Date(iso)` interprets a
// bare date as UTC-midnight, then Intl formats in the user's TZ — in negative
// offsets that shifts the rendered day to the previous date. Same trick
// `fmtScrubDate` (ChartScrubber.tsx) uses.
function fmtMD(iso: string, lang: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  if (!y || !m || !d) return iso
  const date = new Date(y, m - 1, d)
  return new Intl.DateTimeFormat(lang === 'ru' ? 'ru-RU' : 'en-US', { day: '2-digit', month: '2-digit' }).format(date)
}

// k-format ticks (3500 → "3.5k"). Sub-1000 values keep raw.
function fmtTick(v: number): string {
  return v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)
}

// Build runs of same-zone-colored line segments so the polyline can recolour
// at zone boundaries. Overlap each run by one point so neighbouring segments
// meet cleanly (TSB-chart pattern from LoadDetail.tsx).
function buildRuns(scores: number[]) {
  const runs: { zoneId: string; color: string; from: number; to: number }[] = []
  let cur: (typeof runs)[number] | null = null
  for (let i = 0; i < scores.length; i++) {
    const z = enduranceZoneFor(scores[i])
    if (!cur) {
      cur = { zoneId: z.id, color: z.color, from: i, to: i }
    } else if (cur.zoneId === z.id) {
      cur.to = i
    } else {
      runs.push(cur)
      cur = { zoneId: z.id, color: z.color, from: i - 1, to: i }
    }
  }
  if (cur) runs.push(cur)
  return runs
}

export default function EnduranceDetail() {
  const { t } = useTranslation()
  const [range, setRange] = useState<PeriodRange>('3m')
  const { data, loading, error } = useApi<EnduranceScoreResponse>(`/api/endurance-score?period=${range}`)

  return (
    <Layout>
      <div className="-mx-4 -mt-4 md:-mb-8 min-h-screen bg-halo-bg px-4 md:px-9 font-sans text-halo-ink">
        <header className="flex items-center justify-between gap-3 px-1 pt-[18px] pb-3.5">
          <Link
            to="/trends"
            className="inline-flex items-center gap-1.5 py-1.5 pl-1 pr-2.5 text-sm font-medium text-halo-ink-dim no-underline"
          >
            <span className="text-lg leading-none">‹</span> {t('nav.trends')}
          </Link>
          <div className="min-w-0 flex-1 text-right md:text-left">
            <div className="truncate text-[15px] font-semibold tracking-[-0.2px] md:text-[20px]">{t('load.endurance.title')}</div>
            <div className="hidden text-[13px] text-halo-ink-dim md:block">{t('load.endurance.subtitle')}</div>
          </div>
        </header>

        {loading && !data && <LoadingSpinner />}
        {error && !data && <ErrorMessage message={error} />}

        {data && <DetailBody data={data} loading={loading} range={range} onRangeChange={setRange} />}
      </div>
    </Layout>
  )
}

function DetailBody({
  data,
  loading,
  range,
  onRangeChange,
}: {
  data: EnduranceScoreResponse
  loading: boolean
  range: PeriodRange
  onRangeChange: (p: PeriodRange) => void
}) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language.startsWith('ru') ? 'ru' : 'en'
  const score = data.current.score
  const zone = enduranceZoneFor(score)
  const delta = data.current.delta_vs_week_ago
  const deltaSign = delta >= 0 ? '+' : ''

  // Cold-start / <14d HRV data — backend flags it so we don't render a
  // misleading "score 0 / Detrained" gauge. Mirrors EnduranceScoreCard's
  // card branch; reachable here via deep-link to `/trends/endurance`.
  if (data.current.insufficient_data) {
    return (
      <div className="flex flex-col gap-3.5 pb-6">
        <Card>
          <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
            {t('load.endurance.title')}
          </div>
          <div className="mt-4 px-2 py-8 text-center text-[13px] text-halo-ink-dim">
            {t('load.endurance.insufficient')}
          </div>
        </Card>
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-3.5 pb-6 md:gap-[18px]">
      {/* Row 1 — desktop: 1fr | 1.7fr (left summary · right chart). Mobile:
          stacks summary → by-sport card → chart. The mobile-only BySportCard
          is `md:hidden` so on desktop it drops out of the grid flow entirely
          (display:none kids don't claim a column) and the per-sport
          breakdown stays inside SummaryCard as horizontal bars. */}
      <div className="flex flex-col gap-3.5 md:grid md:grid-cols-[1fr_1.7fr] md:items-start md:gap-[18px]">
        <SummaryCard data={data} zone={zone} score={score} delta={delta} deltaSign={deltaSign} />
        <BySportCard data={data} className="md:hidden" />
        <TrendCard
          data={data}
          loading={loading}
          range={range}
          onRangeChange={onRangeChange}
          currentZoneId={zone.id}
          lang={lang}
        />
      </div>

      {/* Row 2 — full-width zone legend. Mobile: compact (dot + name + range).
          Desktop: 4-col grid (dot + name + range + description). */}
      <Card>
        <div className="flex items-baseline justify-between">
          <div className="text-[15px] font-semibold tracking-[-0.2px]">{t('load.endurance.zones_title')}</div>
          <span className="text-[12px] font-medium text-halo-ink-dim">
            {ENDURANCE_ZONES[0].min} – {(ENDURANCE_MAX / 1000).toFixed(1)}k
          </span>
        </div>
        <div className="mt-3 flex flex-col gap-1.5">
          {/* Render best → worst (Peaking at top). Pre-compute the range
              string before reversing, since `range = next.min - 1` needs the
              ascending neighbour. */}
          {ENDURANCE_ZONES.map((zn, i) => {
            const next = ENDURANCE_ZONES[i + 1]
            return {
              ...zn,
              rangeLabel: next
                ? `${zn.min.toLocaleString('en-US').replace(/,/g, ' ')} – ${(next.min - 1)
                    .toLocaleString('en-US')
                    .replace(/,/g, ' ')}`
                : `${zn.min.toLocaleString('en-US').replace(/,/g, ' ')}+`,
            }
          }).slice().reverse().map(zn => {
            const isCurrent = zn.id === zone.id
            const rangeLabel = zn.rangeLabel
            return (
              <div
                key={zn.id}
                className="grid grid-cols-[14px_1fr_auto] items-center gap-3 rounded-chip px-3 py-2.5 md:grid-cols-[14px_200px_140px_1fr] md:gap-5"
                style={{
                  background: isCurrent ? `${zn.color}14` : 'transparent',
                  border: `1px solid ${isCurrent ? `${zn.color}40` : 'transparent'}`,
                }}
              >
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: zn.color }} />
                <span
                  className="text-[13px] md:text-[14px]"
                  style={{
                    fontWeight: isCurrent ? 700 : 600,
                    color: isCurrent ? 'var(--color-ink)' : 'var(--color-ink-dim)',
                  }}
                >
                  {t(`load.endurance.zone.${zn.id}`)}
                </span>
                <span className="font-mono text-[12px] font-medium text-halo-ink-dim md:text-[12px]">{rangeLabel}</span>
                <span className="hidden text-[13px] text-halo-ink-dim md:block">
                  {t(`load.endurance.zone_desc.${zn.id}`)}
                </span>
              </div>
            )
          })}
        </div>
      </Card>
    </div>
  )
}

// Left card on desktop / top card on mobile. Gauge + zone pill + (optional
// badge) + per-sport breakdown with horizontal progress bars.
function SummaryCard({
  data,
  zone,
  score,
  delta,
  deltaSign,
}: {
  data: EnduranceScoreResponse
  zone: { id: string; color: string }
  score: number
  delta: number
  deltaSign: string
}) {
  const { t } = useTranslation()
  return (
    <Card>
      <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
        {t('load.endurance.current_score')}
      </div>
      <div className="mt-2 flex justify-center">
        <EnduranceGauge score={score} size={260} />
      </div>
      {data.current.badge && (
        <div className="mt-1 flex justify-center">
          <EnduranceBadgePlate icon={data.current.badge.icon} label={data.current.badge.label} zoneColor={zone.color} />
        </div>
      )}
      {/* Zone pill — colored chip with current zone name + Δ/week. Mirrors the
          desktop design (direction-b-desktop.jsx:532-537). */}
      <div className="mt-2 flex justify-center">
        <div
          className="inline-flex items-center gap-2 rounded-pill px-3.5 py-1.5"
          style={{ background: `${zone.color}1f` }}
        >
          <span className="inline-block h-2.5 w-2.5 rounded-full" style={{ background: zone.color }} />
          <span
            className="text-[12px] font-bold uppercase tracking-[0.4px]"
            style={{ color: 'var(--color-ink)' }}
          >
            {t(`load.endurance.zone.${zone.id}`)}
          </span>
          <span className="text-[12px] font-semibold text-halo-ink-dim">
            · {t('load.endurance.delta_per_week', { sign: deltaSign, delta })}
          </span>
        </div>
      </div>

      {/* Per-sport breakdown — desktop only. Mobile has its own BySportCard
          rendered as a sibling card so the design carries the same %-grid
          format from Today. Ordering + labels mirror Training load: Swim →
          Ride → Run → Other in English. */}
      <div className="mt-5 hidden border-t border-halo-border pt-4 md:block">
        <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
          {t('load.endurance.per_sport')}
        </div>
        <div className="mt-3 flex flex-col gap-2.5">
          {sortPerSport(data.current.per_sport).map(s => (
            <div
              key={s.name}
              className="grid grid-cols-[12px_70px_1fr_50px] items-center gap-3"
            >
              <span className="h-2.5 w-2.5 rounded-full" style={{ background: SPORT_COLOR[s.name] }} />
              <span className="text-[13px] font-medium">{ENDURANCE_SPORT_LABEL[s.name] ?? s.name}</span>
              <div className="h-1.5 overflow-hidden rounded-full bg-halo-surface-2">
                <div
                  className="h-full"
                  style={{
                    width: `${Math.max(0, Math.min(100, s.pct))}%`,
                    background: SPORT_COLOR[s.name],
                  }}
                />
              </div>
              <span className="text-right font-mono text-[13px] font-semibold tabular-nums tracking-[-0.2px]">
                {s.pct.toFixed(2)}%
              </span>
            </div>
          ))}
        </div>
      </div>
    </Card>
  )
}

// Mobile-only per-sport card — same stacked bar the home card uses (visual
// continuity), plus the full %-grid below for the exact splits. Direction-
// b-halo.jsx:3757-3787. On desktop the per-sport bars inside SummaryCard
// already carry both reads, so this card is `md:hidden` and never makes it
// onto the grid.
function BySportCard({ data, className = '' }: { data: EnduranceScoreResponse; className?: string }) {
  const { t } = useTranslation()
  // Bar + legend: canonical Training-load order (Swim → Ride → Run → Other)
  // so this surface visually echoes the Training load card below it.
  const byTrainingLoadOrder = sortPerSport(data.current.per_sport)
  // %-grid below: descending by share so the dominant disciplines read first
  // (helper handles the stable tie-breaker).
  const byShareDesc = sortPerSportByShareDesc(data.current.per_sport)
  return (
    <div className={className}>
      <Card>
        <div className="text-[15px] font-semibold tracking-[-0.2px]">{t('load.endurance.by_sport_title')}</div>
        {/* Stacked bar + name-only legend (percentages live in the grid
            below — no point duplicating). Order matches Training load. */}
        <div className="mt-3">
          <StackedBar segments={byTrainingLoadOrder.map(s => ({ flex: s.pct, color: SPORT_COLOR[s.name] }))} />
          <div className="mt-1.5 flex flex-wrap items-center justify-between gap-x-2 gap-y-1 text-[10px] font-medium text-halo-ink-dim">
            {byTrainingLoadOrder.map(s => (
              <span key={s.name} className="inline-flex items-center gap-1">
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: SPORT_COLOR[s.name] }} />
                {ENDURANCE_SPORT_LABEL[s.name] ?? s.name}
              </span>
            ))}
          </div>
        </div>
        <div className="mt-4 grid grid-cols-2 gap-x-4 gap-y-3.5 border-t border-halo-border pt-[14px]">
          {byShareDesc.map(s => (
            <div key={s.name}>
              <div className="text-[22px] font-semibold tracking-[-0.6px]">{s.pct.toFixed(2)}%</div>
              <div className="mt-0.5 flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full" style={{ background: SPORT_COLOR[s.name] }} />
                <span className="text-[12px] font-medium text-halo-ink-dim">{ENDURANCE_SPORT_LABEL[s.name] ?? s.name}</span>
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  )
}

// Right card on desktop / lower card on mobile. Title + 1M/3M/6M/1Y picker +
// zone-banded trend chart with per-zone-coloured line segments.
function TrendCard({
  data,
  loading,
  range,
  onRangeChange,
  currentZoneId,
  lang,
}: {
  data: EnduranceScoreResponse
  loading: boolean
  range: PeriodRange
  onRangeChange: (p: PeriodRange) => void
  currentZoneId: string
  lang: string
}) {
  const { t } = useTranslation()
  // Backend guarantees the trend ends at today with `current.score` — when
  // today's snapshot is missing it appends a synthetic point in the handler
  // (api/routers/dashboard.py:1033). So `data.trend` is consumed verbatim.
  const trend = data.trend
  const N = trend.length
  const vals = trend.map(p => p.score)

  // SVG width follows the card — ResizeObserver re-measures on resize.
  const [wrapRef, W] = useMeasuredWidth<HTMLDivElement>(360)
  const innerW = Math.max(40, W - PAD_L - PAD_R)

  // Y-axis snaps to zone boundaries that bracket the data — chart reads as
  // "here's how I moved through the zones".
  const boundaries = [...ENDURANCE_ZONES.map(z => z.min), ENDURANCE_MAX]
  const rawMin = vals.length ? Math.min(...vals) : 0
  const rawMax = vals.length ? Math.max(...vals) : ENDURANCE_MAX
  let yMin = 0
  let yMax = ENDURANCE_MAX
  for (let i = boundaries.length - 1; i >= 0; i--) {
    if (boundaries[i] <= rawMin - 80) {
      yMin = boundaries[i]
      break
    }
  }
  for (let i = 0; i < boundaries.length; i++) {
    if (boundaries[i] >= rawMax + 80) {
      yMax = boundaries[i]
      break
    }
  }

  const x = (i: number) => PAD_L + (N === 1 ? innerW / 2 : (i / (N - 1)) * innerW)
  const y = (v: number) => PAD_T + INNER_H - ((v - yMin) / (yMax - yMin)) * INNER_H

  const runs = buildRuns(vals)
  const yTicks = boundaries.filter(b => b >= yMin && b <= yMax)
  // When N (point count) drops below the desired tick count (e.g. fresh user
  // with only 2 snapshots vs 5-7 desired labels), `Math.round` collapses
  // several positions to the same index — duplicate React keys + overlapping
  // labels. Cap to N and dedupe via a Set (indices are monotonically
  // non-decreasing, so insertion-order Set preserves the sort).
  const desiredXLabels = range === '1m' ? 5 : range === '3m' ? 5 : range === '6m' ? 6 : 7
  const xLabelCount = Math.max(2, Math.min(N, desiredXLabels))
  const labelIdx = N > 1
    ? Array.from(
        new Set(
          Array.from({ length: xLabelCount }, (_, i) => Math.round((i * (N - 1)) / (xLabelCount - 1))),
        ),
      )
    : [0]
  // Daily snapshots: dots only on 1m (~30 pts); 3m+ just marks the latest.
  const showDots = N <= 40

  // Pointer scrubber — vertical crosshair + value callout, same component
  // every other Wellness trend uses (RecoveryTrend / SleepTrend / LoadDetail).
  const { svgRef, idx: scrubIdx, handlers } = useChartScrubber(N, PAD_L, innerW)
  const scrubItems: ScrubItem[] =
    scrubIdx == null || vals[scrubIdx] == null
      ? []
      : [
          {
            label: t(`load.endurance.zone.${enduranceZoneFor(vals[scrubIdx]).id}`),
            value: vals[scrubIdx].toLocaleString('en-US').replace(/,/g, ' '),
            color: enduranceZoneFor(vals[scrubIdx]).color,
          },
        ]

  return (
    <Card>
      <div className="text-[15px] font-semibold tracking-[-0.2px]">{t('load.endurance.trend_chart_title')}</div>
      <div className="mt-2.5">
        <PeriodFilter value={range} onChange={onRangeChange} />
      </div>
      {/* While a period switch is in flight `useApi` retains the old trend
          so the page doesn't unmount — but rendering stale data under a new
          period chip is misleading (scrubber dates wrong, point count off).
          Replace the chart with a spinner placeholder until the new payload
          arrives; the period filter itself stays interactive. */}
      {loading ? (
        <div className="mt-3 flex items-center justify-center" style={{ height: H }}>
          <LoadingSpinner />
        </div>
      ) : N === 0 ? (
        // Defensive — `insufficient_data` is the documented empty-state
        // branch and short-circuits earlier (DetailBody guard). Backend's
        // `today_row` fallback also guarantees at least one point. This stays
        // as a crash guard in case the contract changes.
        <div className="py-12 text-center text-[13px] text-halo-ink-dim">
          {t('load.endurance.no_data')}
        </div>
      ) : (
        <div ref={wrapRef} className="mt-3 w-full">
          <svg
            ref={svgRef}
            viewBox={`0 0 ${W} ${H}`}
            width="100%"
            height={H}
            className="block"
            preserveAspectRatio="none"
            {...handlers}
          >
            {/* Zone bands — tinted fill + colored top-border + in-band
                label so each band is clearly identifiable. Current zone
                gets the strongest fill so the user reads "I'm in THIS one
                right now". Borders/labels skip a band if `zoneHi >= yMax`
                (the band extends off the visible window). */}
            {ENDURANCE_ZONES.map((zn, i) => {
              const next = ENDURANCE_ZONES[i + 1]
              const zoneLo = Math.max(zn.min, yMin)
              const zoneHi = Math.min(next ? next.min : ENDURANCE_MAX, yMax)
              if (zoneHi <= zoneLo) return null
              const isCurrent = zn.id === currentZoneId
              const hasTopBorder = zoneHi < yMax
              const bandHeight = y(zoneLo) - y(zoneHi)
              const labelInside = bandHeight >= 18
              return (
                <g key={zn.id}>
                  <rect
                    x={PAD_L}
                    y={y(zoneHi)}
                    width={innerW}
                    height={bandHeight}
                    fill={zn.color}
                    opacity={isCurrent ? 0.14 : 0.07}
                  />
                  {hasTopBorder && (
                    <line
                      x1={PAD_L}
                      y1={y(zoneHi)}
                      x2={PAD_L + innerW}
                      y2={y(zoneHi)}
                      stroke={zn.color}
                      strokeWidth="1"
                      opacity={isCurrent ? 0.5 : 0.3}
                    />
                  )}
                  {labelInside && (
                    <text
                      x={PAD_L + 8}
                      y={y(zoneHi) + 13}
                      fontSize="10"
                      fontWeight={isCurrent ? 700 : 600}
                      fill={zn.color}
                      textAnchor="start"
                      opacity={isCurrent ? 0.95 : 0.65}
                    >
                      {t(`load.endurance.zone.${zn.id}`)}
                    </text>
                  )}
                </g>
              )
            })}
            {/* Y-axis tick labels — zone boundaries (3.0k / 4.5k / 5.5k …).
                The grid lines themselves come from the colored zone-border
                strokes above, so we don't double up with grey lines. */}
            {yTicks.map(v => (
              <text
                key={v}
                x={PAD_L - 8}
                y={y(v) + 3}
                fontSize="10"
                fill="var(--color-ink-dim)"
                textAnchor="end"
              >
                {fmtTick(v)}
              </text>
            ))}
            {/* Zone-coloured line runs. */}
            {runs.map((run, ri) => {
              let d = ''
              for (let i = run.from; i <= run.to; i++) {
                d += (i === run.from ? 'M ' : ' L ') + x(i).toFixed(1) + ' ' + y(vals[i]).toFixed(1)
              }
              return (
                <path
                  key={ri}
                  d={d}
                  fill="none"
                  stroke={run.color}
                  strokeWidth="2.4"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              )
            })}
            {showDots &&
              vals.map((v, i) => (
                <circle
                  key={i}
                  cx={x(i)}
                  cy={y(v)}
                  r="3.6"
                  fill={enduranceZoneFor(v).color}
                  stroke="#fff"
                  strokeWidth="1.5"
                />
              ))}
            {!showDots && N > 0 && (
              <circle
                cx={x(N - 1)}
                cy={y(vals[N - 1])}
                r="4.5"
                fill={enduranceZoneFor(vals[N - 1]).color}
                stroke="#fff"
                strokeWidth="1.8"
              />
            )}
            {/* Latest-point value label. Y-axis resolution (3.5k spread)
                makes 5491 visually indistinguishable from 5500; this annotation
                removes that ambiguity. White paint-order halo keeps the text
                legible even when it overlaps a zone band. */}
            {N > 0 && (
              <text
                x={x(N - 1) - 8}
                y={y(vals[N - 1]) - 10}
                fontSize="12"
                fontWeight="700"
                fill={enduranceZoneFor(vals[N - 1]).color}
                textAnchor="end"
                paintOrder="stroke"
                stroke="#fff"
                strokeWidth="3"
              >
                {vals[N - 1].toLocaleString('en-US').replace(/,/g, ' ')}
              </text>
            )}
            {labelIdx.map(i => (
              <text
                key={i}
                x={x(i)}
                y={H - 10}
                fontSize="10"
                fill="var(--color-ink-dim)"
                textAnchor={i === 0 ? 'start' : i === N - 1 ? 'end' : 'middle'}
                fontWeight="500"
              >
                {fmtMD(trend[i].date, lang)}
              </text>
            ))}
            {/* Invisible hit-target — keeps the cursor as crosshair across
                the entire plot area, even where there's no zone band fill. */}
            <rect
              x={PAD_L}
              y={PAD_T}
              width={innerW}
              height={INNER_H}
              fill="transparent"
              style={{ cursor: 'crosshair' }}
            />
            {/* Scrubber crosshair + value callout. */}
            <ChartScrubLine
              idx={scrubIdx}
              dateLabel={fmtScrubDate(trend[scrubIdx ?? 0]?.date)}
              items={scrubItems}
              x={x}
              padT={PAD_T}
              innerH={INNER_H}
              W={W}
              padR={PAD_R}
            />
          </svg>
        </div>
      )}
    </Card>
  )
}
