import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import Layout from '../components/Layout'
import { Card, ChartScrubLine, fmtScrubDate, PeriodFilter, useChartScrubber, type ScrubItem } from '../components/halo'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import { useApi } from '../hooks/useApi'
import { fmtDateYmd, num } from '../lib/formatters'
import type { BodyTrendSeries, WellnessResponse } from '../api/types'

/**
 * Body trend detail (prototype `BBodyDetail` / `BMiniLineChart` /
 * `BMiniBarChart`, direction-b-halo.jsx:2680-2960). Reached by tapping the
 * Body card on /wellness. Period filter (1m/3m/6m) → one card per metric
 * (Weight / Body fat / VO₂max as line charts, Steps as a bar chart) — scales
 * differ too much to share an axis. Series come from `/api/body-trend`;
 * `updated_at` for the header from `/api/wellness-day`.
 */

type Range = '1m' | '3m' | '6m' | '1y'
const RANGE_DAYS: Record<Range, number> = { '1m': 30, '3m': 90, '6m': 180, '1y': 365 }

const GOOD_GREEN = '#16a34a'

type MetricKey = 'weight' | 'body_fat' | 'vo2max' | 'steps'

interface MetricCfg {
  key: MetricKey
  label: string
  color: string
  unit: string
  kind: 'line' | 'bar'
  // Which direction is "good" for the window delta — `null` = steps (the
  // window average, shown neutral).
  good: 'up' | 'down' | null
  deltaUnit: string
  fmtY: (v: number) => string
  fmtValue: (v: number) => string
}

const METRICS: MetricCfg[] = [
  {
    key: 'weight',
    label: 'Weight',
    color: 'var(--color-brand)',
    unit: 'kg',
    kind: 'line',
    good: 'down',
    deltaUnit: ' kg',
    fmtY: v => v.toFixed(1),
    fmtValue: v => num(v, 1),
  },
  {
    key: 'body_fat',
    label: 'Body fat',
    color: 'var(--color-coral)',
    unit: '%',
    kind: 'line',
    good: 'down',
    deltaUnit: ' pp',
    fmtY: v => v.toFixed(1),
    fmtValue: v => num(v, 1),
  },
  {
    key: 'vo2max',
    label: 'VO₂max',
    color: GOOD_GREEN,
    unit: '',
    kind: 'line',
    good: 'up',
    deltaUnit: '',
    fmtY: v => v.toFixed(1),
    fmtValue: v => num(v, 1),
  },
  {
    key: 'steps',
    label: 'Steps',
    color: 'var(--color-amber)',
    unit: '/day',
    kind: 'bar',
    good: null,
    deltaUnit: '/day',
    fmtY: v => (v >= 1000 ? `${(v / 1000).toFixed(0)}k` : String(Math.round(v))),
    fmtValue: v => Math.round(v).toLocaleString(),
  },
]

function firstValid(arr: (number | null)[]): number | null {
  for (const v of arr) if (v != null) return v
  return null
}
function lastValid(arr: (number | null)[]): number | null {
  for (let i = arr.length - 1; i >= 0; i--) if (arr[i] != null) return arr[i]
  return null
}
function avgValid(arr: (number | null)[]): number | null {
  let sum = 0
  let n = 0
  for (const v of arr) {
    if (v != null) {
      sum += v
      n++
    }
  }
  return n ? sum / n : null
}

// `YYYY-MM-DD` → `MM/DD` for chart x-axis ticks.
const fmtMd = (ymd: string) => {
  const p = ymd.split('-')
  return `${p[1]}/${p[2]}`
}

export default function BodyTrend() {
  const { t } = useTranslation()
  const [range, setRange] = useState<Range>('3m')

  const pastDays = RANGE_DAYS[range]
  const { data: series, loading, error } = useApi<BodyTrendSeries>(`/api/body-trend?days=${pastDays}`)
  const today = fmtDateYmd(new Date())
  const { data: wellness } = useApi<WellnessResponse>(`/api/wellness-day?date=${today}`)
  const w = wellness?.has_data ? wellness : null
  const updatedTime = w?.updated_at
    ? new Date(w.updated_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    : null

  return (
    <Layout maxWidth="480px">
      <div className="-mx-4 -mt-4 md:-mb-8 min-h-screen bg-halo-bg px-4 md:px-9 font-sans text-halo-ink">
        <header className="flex items-center justify-between px-1 pt-[18px] pb-2.5">
          <Link
            to="/wellness"
            className="inline-flex items-center gap-1.5 py-1.5 pl-1 pr-2.5 text-sm font-medium text-halo-ink-dim no-underline"
          >
            <span className="text-lg leading-none">‹</span> {t('nav.today')}
          </Link>
          {updatedTime && (
            <span className="pr-1 text-xs text-halo-ink-dim">{t('body_trend.updated', { time: updatedTime })}</span>
          )}
        </header>

        {loading && !series && <LoadingSpinner />}
        {error && !series && <ErrorMessage message={t('wellness.load_error')} />}

        {series && (
          <div className="flex flex-col gap-3.5 pb-6">
            <div>
              <div className="text-[22px] font-semibold tracking-[-0.4px]">{t('body_trend.title')}</div>
              <div className="mt-0.5 text-[13px] text-halo-ink-dim">
                {t('body_trend.subtitle', { days: pastDays })}
              </div>
            </div>

            <PeriodFilter value={range} onChange={setRange} />

            {series.dates.length === 0 ? (
              <Card>
                <div className="py-10 text-center text-[13px] text-halo-ink-dim">{t('body_trend.no_data')}</div>
              </Card>
            ) : (
              METRICS.map(m => (
                <MetricCard key={m.key} cfg={m} dates={series.dates} values={series[m.key]} pastDays={pastDays} />
              ))
            )}
          </div>
        )}
      </div>
    </Layout>
  )
}

// One metric card — header (label · today value · window delta) + chart.
// Rendered only when the metric has at least one measured point in the window.
function MetricCard({
  cfg,
  dates,
  values,
  pastDays,
}: {
  cfg: MetricCfg
  dates: string[]
  values: (number | null)[]
  pastDays: number
}) {
  const today = lastValid(values)
  if (today == null) return null

  const first = firstValid(values)
  // `change` metrics (weight/body-fat/VO₂max) show oldest→newest delta; steps
  // shows the window average (a single delta number isn't meaningful for it).
  const isAvg = cfg.good == null
  const delta = isAvg ? avgValid(values) : first != null ? today - first : null

  let deltaStr = '—'
  let deltaColor = 'var(--color-ink-dim)'
  if (delta != null) {
    if (isAvg) {
      deltaStr = `${Math.round(delta).toLocaleString()}${cfg.deltaUnit}`
    } else {
      const rounded = Number(delta.toFixed(1))
      deltaStr = `${rounded > 0 ? '+' : ''}${rounded}${cfg.deltaUnit}`
      if (rounded !== 0) {
        const good = cfg.good === 'up' ? rounded > 0 : rounded < 0
        deltaColor = good ? GOOD_GREEN : 'var(--color-coral)'
      }
    }
  }

  return (
    <Card>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm" style={{ background: cfg.color }} />
            <span className="text-[11px] font-semibold uppercase tracking-[0.4px] text-halo-ink-dim">{cfg.label}</span>
          </div>
          <div className="mt-1 flex items-baseline gap-1">
            <span className="text-[28px] font-semibold tracking-[-0.5px] text-halo-ink">{cfg.fmtValue(today)}</span>
            {cfg.unit && <span className="text-xs text-halo-ink-dim">{cfg.unit}</span>}
          </div>
        </div>
        <div className="mt-1.5 text-right">
          <div className="text-[12px] font-semibold" style={{ color: deltaColor }}>
            {deltaStr}
          </div>
          {/* "over Nd" not "vs Nd ago" — body metrics are sparsely measured,
              so the delta spans the window's data, not necessarily a point
              exactly N days back. */}
          <div className="mt-px text-[9px] font-semibold uppercase tracking-[0.4px] text-halo-ink-dimmer">
            {isAvg ? 'window' : `over ${pastDays}d`}
          </div>
        </div>
      </div>
      <div className="mt-2.5">
        {cfg.kind === 'bar' ? (
          <MiniBarChart dates={dates} values={values} color={cfg.color} fmtY={cfg.fmtY} />
        ) : (
          <MiniLineChart dates={dates} values={values} color={cfg.color} fmtY={cfg.fmtY} />
        )}
      </div>
    </Card>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Mini line chart (prototype `BMiniLineChart`). Auto-fit y range, area fill,
// sparse x labels, today endpoint dot. Null points are skipped; the line spans
// the gap. `preserveAspectRatio="none"` stretches to the card width.
// ─────────────────────────────────────────────────────────────────────────────
function MiniLineChart({
  dates,
  values,
  color,
  fmtY,
}: {
  dates: string[]
  values: (number | null)[]
  color: string
  fmtY: (v: number) => string
}) {
  const W = 320
  const H = 110
  const pad = { l: 32, r: 8, t: 10, b: 20 }
  const innerW = W - pad.l - pad.r
  const innerH = H - pad.t - pad.b
  const N = dates.length

  // Scrubber hook — called before the early return below (Rules of Hooks).
  const { svgRef, idx: scrubIdx, handlers } = useChartScrubber(N, pad.l, innerW)

  const pts = values
    .map((v, i) => ({ i, v }))
    .filter((p): p is { i: number; v: number } => p.v != null)
  if (pts.length === 0) return null

  let vMin = Math.min(...pts.map(p => p.v))
  let vMax = Math.max(...pts.map(p => p.v))
  const range = vMax - vMin || 1
  vMin -= range * 0.1
  vMax += range * 0.1

  const xOf = (i: number) => pad.l + (N <= 1 ? innerW / 2 : (i / (N - 1)) * innerW)
  const yOf = (v: number) => pad.t + innerH - ((v - vMin) / (vMax - vMin)) * innerH

  const line = pts.map((p, k) => (k === 0 ? 'M ' : ' L ') + xOf(p.i).toFixed(1) + ' ' + yOf(p.v).toFixed(1)).join('')
  const area =
    `${line} L ${xOf(pts[pts.length - 1].i).toFixed(1)} ${yOf(vMin).toFixed(1)}` +
    ` L ${xOf(pts[0].i).toFixed(1)} ${yOf(vMin).toFixed(1)} Z`

  const span = vMax - vMin
  const ticks = [vMin + span * 0.05, vMin + span * 0.5, vMin + span * 0.95]
  const xCount = Math.min(4, N)
  const xLabels: number[] = []
  for (let i = 0; i < xCount; i++) xLabels.push(xCount === 1 ? 0 : Math.round((i * (N - 1)) / (xCount - 1)))

  // Scrub snaps to the nearest *plotted* point. `values` has gaps — weight /
  // body fat / VO₂max aren't logged daily and the line just spans them — so a
  // raw calendar index can land on a null day with no value to show. `pts` is
  // non-empty here (the early return above bails when there are no points).
  const scrubPt =
    scrubIdx == null
      ? null
      : pts.reduce((best, p) => (Math.abs(p.i - scrubIdx) < Math.abs(best.i - scrubIdx) ? p : best))
  const scrubItems: ScrubItem[] = scrubPt ? [{ label: '', value: fmtY(scrubPt.v), color }] : []

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      height={H}
      preserveAspectRatio="none"
      className="block overflow-visible"
      {...handlers}
    >
      {ticks.map((tick, i) => (
        <line
          key={`g${i}`}
          x1={pad.l}
          y1={yOf(tick)}
          x2={pad.l + innerW}
          y2={yOf(tick)}
          stroke="var(--color-border)"
          strokeWidth="1"
          strokeDasharray="2 3"
          opacity="0.5"
        />
      ))}
      {ticks.map((tick, i) => (
        <text key={`y${i}`} x={pad.l - 6} y={yOf(tick) + 3} fontSize="9" fill="var(--color-ink-dim)" textAnchor="end">
          {fmtY(tick)}
        </text>
      ))}
      <path d={area} fill={color} fillOpacity="0.1" />
      <path d={line} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
      <circle
        cx={xOf(pts[pts.length - 1].i)}
        cy={yOf(pts[pts.length - 1].v)}
        r="3.5"
        fill="#fff"
        stroke={color}
        strokeWidth="1.8"
      />
      {xLabels.map((idx, i) => (
        <text
          key={`x${i}`}
          x={xOf(idx)}
          y={H - pad.b + 12}
          fontSize="9"
          fill="var(--color-ink-dim)"
          textAnchor={i === 0 ? 'start' : i === xLabels.length - 1 ? 'end' : 'middle'}
        >
          {fmtMd(dates[idx])}
        </text>
      ))}

      {/* Scrubber — invisible hit target + crosshair callout. */}
      <rect x={pad.l} y={pad.t} width={innerW} height={innerH} fill="transparent" style={{ cursor: 'crosshair' }} />
      <ChartScrubLine
        idx={scrubPt ? scrubPt.i : null}
        dateLabel={scrubPt ? fmtScrubDate(dates[scrubPt.i]) : ''}
        items={scrubItems}
        x={xOf}
        padT={pad.t}
        innerH={innerH}
        W={W}
        padR={pad.r}
      />
    </svg>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Mini bar chart (prototype `BMiniBarChart`). Past ~45 days the window
// auto-aggregates to weekly bars (averaging non-null days) so bars stay
// readable. Today's bar is full opacity, history dimmed.
// ─────────────────────────────────────────────────────────────────────────────
function MiniBarChart({
  dates,
  values,
  color,
  fmtY,
}: {
  dates: string[]
  values: (number | null)[]
  color: string
  fmtY: (v: number) => string
}) {
  const W = 320
  const H = 110
  const pad = { l: 34, r: 8, t: 10, b: 20 }
  const innerW = W - pad.l - pad.r
  const innerH = H - pad.t - pad.b
  const N = dates.length

  type Bar = { date: string; v: number | null }
  const bars: Bar[] = []
  if (N > 45) {
    for (let i = 0; i < N; i += 7) {
      const end = Math.min(i + 6, N - 1)
      let sum = 0
      let n = 0
      for (let k = i; k <= end; k++) {
        const v = values[k]
        if (v != null) {
          sum += v
          n++
        }
      }
      bars.push({ date: dates[i], v: n ? sum / n : null })
    }
  } else {
    for (let i = 0; i < N; i++) bars.push({ date: dates[i], v: values[i] })
  }
  const M = bars.length

  const maxV = Math.max(0, ...bars.map(b => b.v ?? 0))
  const niceStep =
    maxV > 12000 ? 4000 : maxV > 6000 ? 2000 : maxV > 1500 ? 500 : maxV > 600 ? 200 : maxV > 200 ? 50 : 10
  const yMax = Math.ceil(maxV / niceStep) * niceStep || niceStep

  const slotW = innerW / M
  const barW = Math.max(2, slotW * (N > 45 ? 0.78 : 0.72))
  const xOf = (i: number) => pad.l + i * slotW + (slotW - barW) / 2
  const yOf = (v: number) => pad.t + innerH - (v / yMax) * innerH

  const yTicks = [0, yMax / 2, yMax]
  const xCount = Math.min(4, M)
  const xLabels: number[] = []
  for (let i = 0; i < xCount; i++) xLabels.push(xCount === 1 ? 0 : Math.round((i * (M - 1)) / (xCount - 1)))

  const { svgRef, idx: scrubIdx, handlers } = useChartScrubber(M, pad.l, innerW)
  const scrubBar = scrubIdx == null ? null : bars[scrubIdx]
  const scrubItems: ScrubItem[] =
    scrubBar == null || scrubBar.v == null ? [] : [{ label: '', value: fmtY(scrubBar.v), color }]

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      height={H}
      preserveAspectRatio="none"
      className="block overflow-visible"
      {...handlers}
    >
      {yTicks.map((tick, i) => (
        <line
          key={`g${i}`}
          x1={pad.l}
          y1={yOf(tick)}
          x2={pad.l + innerW}
          y2={yOf(tick)}
          stroke="var(--color-border)"
          strokeWidth="1"
          strokeDasharray={tick === 0 ? undefined : '2 3'}
          opacity={tick === 0 ? 0.6 : 0.45}
        />
      ))}
      {yTicks.map((tick, i) => (
        <text key={`yt${i}`} x={pad.l - 6} y={yOf(tick) + 3} fontSize="9" fill="var(--color-ink-dim)" textAnchor="end">
          {fmtY(tick)}
        </text>
      ))}
      {bars.map((b, i) => {
        if (b.v == null) return null
        const h = Math.max(1, yOf(0) - yOf(b.v))
        return (
          <rect
            key={i}
            x={xOf(i)}
            y={yOf(b.v)}
            width={barW}
            height={h}
            rx="1.5"
            fill={color}
            opacity={i === M - 1 ? 1 : 0.7}
          />
        )
      })}
      {xLabels.map((idx, i) => (
        <text
          key={`x${i}`}
          x={xOf(idx) + barW / 2}
          y={H - pad.b + 12}
          fontSize="9"
          fill="var(--color-ink-dim)"
          textAnchor={i === 0 ? 'start' : i === xLabels.length - 1 ? 'end' : 'middle'}
        >
          {fmtMd(bars[idx].date)}
        </text>
      ))}

      {/* Scrubber — invisible hit target + crosshair callout. */}
      <rect x={pad.l} y={pad.t} width={innerW} height={innerH} fill="transparent" style={{ cursor: 'crosshair' }} />
      <ChartScrubLine
        idx={scrubIdx}
        dateLabel={fmtScrubDate(scrubBar?.date) + (N > 45 ? ' (wk)' : '')}
        items={scrubItems}
        x={i => xOf(i) + barW / 2}
        padT={pad.t}
        innerH={innerH}
        W={W}
        padR={pad.r}
      />
    </svg>
  )
}
