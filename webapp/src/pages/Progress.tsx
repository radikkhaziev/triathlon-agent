import { useState, useEffect, useRef, useMemo } from 'react'
import { Link } from 'react-router-dom'
import { Chart, registerables } from 'chart.js'
import annotationPlugin from 'chartjs-plugin-annotation'
import Layout from '../components/Layout'
import TabSwitcher from '../components/TabSwitcher'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import { ChartCard, chartOptions } from '../components/ChartCard'
import { useApi } from '../hooks/useApi'
import { apiFetch } from '../api/client'
import { CHART_COLORS } from '../lib/constants'
import { num } from '../lib/formatters'
import type { ProgressResponse, DecouplingTrend, MarathonShapeResponse } from '../api/types'

interface FitnessProjectionData {
  count: number
  dates: string[]
  ctl: (number | null)[]
  atl: (number | null)[]
  ramp_rate: (number | null)[]
}

Chart.register(...registerables, annotationPlugin)

type Sport = 'bike' | 'run' | 'swim'

const SPORT_TABS = [
  { key: 'bike', label: 'Bike' },
  { key: 'run', label: 'Run' },
  { key: 'swim', label: 'Swim' },
]

const DAYS_TABS = [
  { key: '90', label: '90d' },
  { key: '180', label: '180d' },
  { key: '365', label: '1y' },
]

const STATUS_COLORS = {
  green: '#22c55e',
  yellow: '#eab308',
  red: '#ef4444',
} as const

// One sub-section inside the unified days-dependent card. `first:` strips the
// top divider/padding so the first rendered child sits flush against the card
// top — works because Tailwind's `first:` operates on the actual DOM, so React
// conditional rendering (e.g. TrendBadge returning null) is handled correctly.
function SubBlock({ children, chart = false }: { children: React.ReactNode; chart?: boolean }) {
  return (
    <div className="border-t border-border first:border-t-0 mt-3 pt-3 first:mt-0 first:pt-0">
      {chart ? <div style={{ height: 280 }}>{children}</div> : children}
    </div>
  )
}

export default function Progress() {
  const [sport, setSport] = useState<Sport>('bike')
  const [days, setDays] = useState('180')

  const endpoint = `/api/progress?sport=${sport}&days=${days}`
  const { data, loading, error } = useApi<ProgressResponse>(endpoint)

  return (
    <Layout title="Progress">
      <FitnessProjectionChart />

      <div className="flex gap-1 py-3 sticky top-0 bg-bg z-10">
        {SPORT_TABS.map(tab => (
          <button
            key={tab.key}
            onClick={() => setSport(tab.key as Sport)}
            className={`flex-1 py-2 px-1 border-none rounded-lg text-[13px] font-semibold cursor-pointer transition-all font-sans ${
              sport === tab.key
                ? 'bg-[var(--button)] text-[var(--button-text)]'
                : 'bg-[var(--surface)] text-text-dim'
            }`}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {data?.decoupling_trend && <DecouplingBadge trend={data.decoupling_trend} />}
      {sport !== 'swim' && <PolarizationWidget sport={sport} />}
      {sport === 'run' && <MarathonShapeWidget />}
      {sport === 'bike' && <ProgressionWidget />}

      <TabSwitcher tabs={DAYS_TABS} active={days} onChange={setDays} />

      {loading && <LoadingSpinner />}
      {error && <ErrorMessage message="Failed to load progress data" />}
      {!loading && !error && data && data.data_points > 0 && (
        <ProgressContent data={data} sport={sport} />
      )}
      {!loading && !error && (!data || data.data_points === 0) && (
        <EmptyState sport={sport} />
      )}
    </Layout>
  )
}

function ProgressContent({ data, sport }: { data: ProgressResponse; sport: Sport }) {
  return (
    <>
      <div className="bg-surface border border-border rounded-[14px] p-4 mb-3">
        <TrendBadge data={data} sport={sport} />
        {sport === 'swim' ? <SwimCharts data={data} /> : (
          <>
            <EFChart data={data} sport={sport} />
            <DecouplingChart data={data} sport={sport} />
          </>
        )}
      </div>
      <ActivityList data={data} sport={sport} />
    </>
  )
}

function TrendBadge({ data, sport }: { data: ProgressResponse; sport: Sport }) {
  const trend = sport === 'swim'
    ? data.metrics?.pace_100m?.trend
    : data.trend

  if (!trend || trend.direction === 'insufficient_data') return null

  const isPositive = sport === 'swim'
    ? trend.direction === 'falling'  // lower pace = better
    : trend.direction === 'rising'   // higher EF = better

  const color = isPositive ? 'var(--green)' : trend.direction === 'stable' ? 'var(--text-dim)' : 'var(--red)'
  const arrow = trend.direction === 'rising' ? '\u2191' : trend.direction === 'falling' ? '\u2193' : '\u2192'
  const label = sport === 'swim' ? 'Pace trend' : 'EF trend'

  return (
    <SubBlock>
      <div className="flex items-center justify-between">
        <span className="text-[13px] text-text-dim">{label}</span>
        <span className="text-sm font-bold" style={{ color }}>
          {arrow} {Math.abs(trend.pct).toFixed(1)}%
        </span>
      </div>
    </SubBlock>
  )
}

function DecouplingBadge({ trend }: { trend: DecouplingTrend }) {
  const color = STATUS_COLORS[trend.status]
  const stale = trend.latest.days_since > 14

  return (
    <div className="bg-surface border border-border rounded-[14px] p-4 mb-3">
      <div className="flex items-center justify-between mb-1">
        <span className="text-[13px] text-text-dim">Decoupling (last {trend.last_n})</span>
        <span className="text-sm font-bold" style={{ color }}>
          {trend.median.toFixed(1)}%
        </span>
      </div>
      <div className="flex items-center justify-between">
        <span className="text-[12px] text-text-dim">
          Latest: {trend.latest.value.toFixed(1)}% ({trend.latest.date})
        </span>
        {stale && <span className="text-[11px] text-text-dim">stale ({trend.latest.days_since}d ago)</span>}
      </div>
    </div>
  )
}

function EFChart({ data, sport }: { data: ProgressResponse; sport: Sport }) {
  const chartRef = useRef<HTMLCanvasElement>(null)
  const chartInstRef = useRef<Chart | null>(null)

  useEffect(() => {
    if (!chartRef.current || !data.weekly?.length) return

    chartInstRef.current?.destroy()

    const labels = data.weekly.map(w => w.week.replace(/^\d{4}-/, ''))
    const values = data.weekly.map(w => w.ef_mean ?? null)
    const color = sport === 'bike' ? CHART_COLORS.ride : CHART_COLORS.run

    chartInstRef.current = new Chart(chartRef.current, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: `EF (${data.unit || ''})`,
          data: values,
          borderColor: color,
          backgroundColor: color + '20',
          fill: true,
          tension: 0.3,
          pointRadius: 3,
          pointBackgroundColor: color,
          borderWidth: 2,
          spanGaps: true,
        }],
      },
      options: chartOptions(`Efficiency Factor — ${sport === 'bike' ? 'Bike' : 'Run'}`),
    })

    return () => { chartInstRef.current?.destroy() }
  }, [data, sport])

  if (!data.weekly?.length) return null

  return (
    <SubBlock chart>
      <canvas ref={chartRef} />
    </SubBlock>
  )
}

function DecouplingChart({ data, sport }: { data: ProgressResponse; sport: Sport }) {
  const chartRef = useRef<HTMLCanvasElement>(null)
  const chartInstRef = useRef<Chart | null>(null)
  const activities = data.activities.filter(a => a.decoupling != null)

  useEffect(() => {
    if (!chartRef.current || activities.length < 2) return

    chartInstRef.current?.destroy()

    const labels = activities.map(a => a.date.slice(5))  // MM-DD
    const values = activities.map(a => a.decoupling!)
    const colors = activities.map(a => {
      const abs = Math.abs(a.decoupling!)
      if (abs < 5) return STATUS_COLORS.green
      if (abs <= 10) return STATUS_COLORS.yellow
      return STATUS_COLORS.red
    })

    chartInstRef.current = new Chart(chartRef.current, {
      type: 'scatter',
      data: {
        labels,
        datasets: [{
          label: 'Decoupling %',
          data: values.map((v, i) => ({ x: i, y: v })),
          pointBackgroundColor: colors,
          pointBorderColor: colors,
          pointRadius: 5,
          pointHoverRadius: 7,
        }],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          title: { display: true, text: `Cardiac Drift — ${sport === 'bike' ? 'Bike' : 'Run'}`, font: { size: 14, weight: 'bold' } },
          annotation: {
            annotations: {
              greenZone: {
                type: 'box',
                yMin: -5, yMax: 5,
                backgroundColor: STATUS_COLORS.green + '15',
                borderWidth: 0,
              },
              yellowZoneHi: {
                type: 'box',
                yMin: 5, yMax: 10,
                backgroundColor: STATUS_COLORS.yellow + '15',
                borderWidth: 0,
              },
              yellowZoneLo: {
                type: 'box',
                yMin: -10, yMax: -5,
                backgroundColor: STATUS_COLORS.yellow + '15',
                borderWidth: 0,
              },
              redZoneHi: {
                type: 'box',
                yMin: 10, yMax: 30,
                backgroundColor: STATUS_COLORS.red + '10',
                borderWidth: 0,
              },
              redZoneLo: {
                type: 'box',
                yMin: -30, yMax: -10,
                backgroundColor: STATUS_COLORS.red + '10',
                borderWidth: 0,
              },
            },
          },
          tooltip: {
            callbacks: {
              title: (items) => {
                const idx = items[0]?.dataIndex
                return idx != null ? activities[idx].date : ''
              },
              label: (item) => `${(item.raw as { y: number }).y.toFixed(1)}%`,
            },
          },
        },
        scales: {
          x: {
            type: 'linear',
            display: true,
            ticks: {
              font: { size: 12 },
              callback: (val) => labels[val as number] || '',
              maxRotation: 45,
              autoSkip: true,
              maxTicksLimit: 10,
            },
            grid: { color: 'rgba(128,128,128,0.2)' },
          },
          y: {
            grid: { color: 'rgba(128,128,128,0.2)' },
            ticks: {
              font: { size: 12 },
              callback: (val) => `${val}%`,
            },
          },
        },
      },
    })

    return () => { chartInstRef.current?.destroy() }
  }, [data, sport])

  if (activities.length < 2) return null

  return (
    <SubBlock chart>
      <canvas ref={chartRef} />
    </SubBlock>
  )
}

function SwimCharts({ data }: { data: ProgressResponse }) {
  const paceRef = useRef<HTMLCanvasElement>(null)
  const swolfRef = useRef<HTMLCanvasElement>(null)
  const chartsRef = useRef<Chart[]>([])

  useEffect(() => {
    if (!data.weekly?.length) return

    chartsRef.current.forEach(c => c.destroy())
    chartsRef.current = []

    const labels = data.weekly.map(w => w.week.replace(/^\d{4}-/, ''))

    if (paceRef.current) {
      const values = data.weekly.map(w => w.pace_mean ?? null)
      chartsRef.current.push(new Chart(paceRef.current, {
        type: 'line',
        data: {
          labels,
          datasets: [{
            label: 'Pace (sec/100m)',
            data: values,
            borderColor: CHART_COLORS.swim,
            backgroundColor: CHART_COLORS.swim + '20',
            fill: true,
            tension: 0.3,
            pointRadius: 3,
            pointBackgroundColor: CHART_COLORS.swim,
            borderWidth: 2,
            spanGaps: true,
          }],
        },
        options: chartOptions('Pace per 100m'),
      }))
    }

    if (swolfRef.current) {
      const values = data.weekly.map(w => w.swolf_mean ?? null)
      const hasSwolf = values.some(v => v !== null)
      if (hasSwolf) {
        chartsRef.current.push(new Chart(swolfRef.current, {
          type: 'line',
          data: {
            labels,
            datasets: [{
              label: 'SWOLF',
              data: values,
              borderColor: '#8b5cf6',
              backgroundColor: '#8b5cf620',
              fill: true,
              tension: 0.3,
              pointRadius: 3,
              pointBackgroundColor: '#8b5cf6',
              borderWidth: 2,
              spanGaps: true,
            }],
          },
          options: chartOptions('SWOLF'),
        }))
      }
    }

    return () => { chartsRef.current.forEach(c => c.destroy()) }
  }, [data])

  const hasSwolf = data.weekly?.some(w => w.swolf_mean != null) ?? false

  if (!data.weekly?.length) return null

  return (
    <>
      <SubBlock chart><canvas ref={paceRef} /></SubBlock>
      {hasSwolf && <SubBlock chart><canvas ref={swolfRef} /></SubBlock>}
    </>
  )
}

function ActivityList({ data, sport }: { data: ProgressResponse; sport: Sport }) {
  const activities = [...data.activities].reverse().slice(0, 10)

  return (
    <div className="bg-surface border border-border rounded-[14px] p-4 mb-3">
      <div className="text-[15px] font-bold mb-3">Recent Sessions</div>
      {activities.map(act => (
        <Link
          key={act.id}
          to={`/activity/${act.id}`}
          className="flex justify-between items-center py-1.5 border-b border-border last:border-b-0 no-underline text-text"
        >
          <div>
            <span className="text-[13px] text-text-dim">{act.date}</span>
            <span className="text-[12px] text-text-dim ml-2">{act.duration_min}min</span>
          </div>
          <div className="text-sm font-semibold">
            {sport === 'swim' ? (
              <>
                {act.pace_100m != null && <span>{num(act.pace_100m, 0)}s/100m</span>}
                {act.swolf != null && <span className="text-text-dim text-[12px] ml-2">SWOLF {num(act.swolf, 0)}</span>}
              </>
            ) : (
              <>
                {act.ef != null && (
                  <span><span className="text-text-dim font-normal">EF</span> {num(act.ef, 2)}</span>
                )}
                {act.decoupling != null && (
                  <span
                    className="text-[12px] ml-2 font-medium"
                    style={{ color: STATUS_COLORS[act.decoupling_status || 'green'] }}
                  >
                    <span className="text-text-dim font-normal">Drift</span> {num(act.decoupling, 1)}%
                  </span>
                )}
              </>
            )}
          </div>
        </Link>
      ))}
    </div>
  )
}

const PATTERN_COLORS: Record<string, string> = {
  polarized: '#22c55e',
  pyramidal: '#eab308',
  threshold: '#f97316',
  too_easy: '#ef4444',
  too_hard: '#ef4444',
  insufficient_data: '#9ca3af',
}

const PATTERN_LABELS: Record<string, string> = {
  polarized: 'Polarized (optimal)',
  pyramidal: 'Pyramidal (acceptable)',
  threshold: 'Threshold (gray zone)',
  too_easy: 'Too easy',
  too_hard: 'Too hard',
  insufficient_data: 'Not enough data',
}

interface PolarizationData {
  low_pct: number
  mid_pct: number
  high_pct: number
  pattern: string
  total_hours: number
  n_activities: number
  signals: string[]
  windows?: Record<string, { low_pct: number; mid_pct: number; high_pct: number; pattern: string }>
}

function PolarizationWidget({ sport }: { sport: Sport }) {
  const [data, setData] = useState<PolarizationData | null>(null)

  useEffect(() => {
    let cancelled = false
    const apiSport = sport === 'bike' ? 'ride' : sport
    apiFetch<PolarizationData>(`/api/polarization?sport=${apiSport}&days=28`)
      .then(d => { if (!cancelled) setData(d) })
      .catch(e => console.warn('polarization fetch failed:', e))
    return () => { cancelled = true }
  }, [sport])

  if (!data || data.pattern === 'insufficient_data') return null

  const color = PATTERN_COLORS[data.pattern] || '#9ca3af'

  return (
    <div className="bg-surface border border-border rounded-[14px] p-4 mb-3">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[13px] text-text-dim">Zone Distribution (28d)</span>
        <span className="text-xs font-semibold px-2 py-0.5 rounded-full" style={{ color, backgroundColor: color + '15' }}>
          {PATTERN_LABELS[data.pattern] || data.pattern}
        </span>
      </div>

      {/* Stacked bar */}
      <div className="flex rounded-lg overflow-hidden h-7 text-[11px] font-mono font-semibold text-white">
        {data.low_pct > 0 && (
          <div className="flex items-center justify-center" style={{ width: `${data.low_pct}%`, backgroundColor: '#22c55e' }}>
            {data.low_pct >= 10 && `${data.low_pct}%`}
          </div>
        )}
        {data.mid_pct > 0 && (
          <div className="flex items-center justify-center" style={{ width: `${data.mid_pct}%`, backgroundColor: '#f59e0b' }}>
            {data.mid_pct >= 8 && `${data.mid_pct}%`}
          </div>
        )}
        {data.high_pct > 0 && (
          <div className="flex items-center justify-center" style={{ width: `${data.high_pct}%`, backgroundColor: '#ef4444' }}>
            {data.high_pct >= 8 && `${data.high_pct}%`}
          </div>
        )}
      </div>

      {/* Legend */}
      <div className="flex gap-4 mt-2 text-[11px] text-text-dim">
        <span><span className="inline-block w-2 h-2 rounded-full bg-[#22c55e] mr-1" />Low {data.low_pct}%</span>
        <span><span className="inline-block w-2 h-2 rounded-full bg-[#f59e0b] mr-1" />Mid {data.mid_pct}%</span>
        <span><span className="inline-block w-2 h-2 rounded-full bg-[#ef4444] mr-1" />High {data.high_pct}%</span>
        <span className="ml-auto">{data.total_hours}h · {data.n_activities} sessions</span>
      </div>

      {/* Signals */}
      {data.signals.length > 0 && (
        <div className="mt-2 pt-2 border-t border-border">
          {data.signals.map((s, i) => (
            <div key={i} className="text-[12px] text-text-dim mt-1">
              ⚠ {s}
            </div>
          ))}
        </div>
      )}
    </div>
  )
}


// Marathon Shape — Runalyze-style basic-endurance metric. Distance-picker
// renormalises the "ready for X" badge and shifts the chart annotation line;
// the underlying MS curve doesn't move (it's an objective measurement).
type MSDistance = '10K' | 'HM' | 'Marathon'

const MS_DISTANCE_KM: Record<MSDistance, number> = {
  '10K': 10.0,
  HM: 21.0975,
  Marathon: 42.195,
}

const MS_DISTANCE_TABS = [
  { key: '10K', label: '10K' },
  { key: 'HM', label: 'HM' },
  { key: 'Marathon', label: 'Marathon' },
]

// Spec §3 — distance-adjusted Components factors. Calibrated on Runalyze
// screenshot V≈37 (2026-05-14). Multiply marathon-baseline target by these to
// derive per-distance Required Weekly/Long Run. Drift unknown for V > 50;
// refine via §14.D3.B (PHP-port) if user surveys reveal material divergence.
const MS_RUNALYZE_DISTANCE_FACTORS: Record<MSDistance, { weekly: number; longjog: number | null }> = {
  '10K': { weekly: 0.26, longjog: null },   // 15 / 58, longjog n/a for 10K (Runalyze shows «—»)
  HM: { weekly: 0.57, longjog: 0.69 },      // 33/58, 18/26
  Marathon: { weekly: 1.00, longjog: 1.00 },
}

// Format helpers for the Predicted block (Phase 1.5).
// `H:MM:SS` for ≥1h, `M:SS` otherwise. 6135 → "1:42:15". 3340 → "55:40".
// Negative / zero treated as "—" — envelope filtering upstream prevents these,
// but the guard is free defence against future degenerate model output.
function formatHMS(sec: number): string {
  if (!(sec > 0)) return '—'
  const total = Math.round(sec)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const ss = String(s).padStart(2, '0')
  if (h > 0) {
    const mm = String(m).padStart(2, '0')
    return `${h}:${mm}:${ss}`
  }
  return `${m}:${ss}`
}

// `M:SS/km`. 290.7 → "4:51/km" (rounded to nearest second).
function formatPace(secPerKm: number): string {
  if (!(secPerKm > 0)) return '—'
  const total = Math.round(secPerKm)
  const m = Math.floor(total / 60)
  const s = total % 60
  return `${m}:${String(s).padStart(2, '0')}/km`
}

// Spec §13 — surface a footnote when CI is wide enough that the point estimate
// stops being actionable. Threshold 0.20 = ±10% of central value.
const MS_WIDE_CI_THRESHOLD = 0.20

// XGBoost cannot extrapolate beyond training distance range — tree-based models
// clamp predictions to the nearest seen leaf. If the picked race distance is
// further than this factor × the user's longest race, the prediction is
// extrapolated and unreliable (CI bands don't catch this — they're computed
// from in-distribution bootstrap residuals). Threshold 1.3 ≈ within 30% of
// training-range max we assume interpolation is safe.
const MS_EXTRAPOLATION_FACTOR = 1.3

function MarathonShapeWidget() {
  const [data, setData] = useState<MarathonShapeResponse | null>(null)
  const [distance, setDistance] = useState<MSDistance>('HM')
  const chartRef = useRef<HTMLCanvasElement>(null)
  const chartInstRef = useRef<Chart | null>(null)

  useEffect(() => {
    let cancelled = false
    apiFetch<MarathonShapeResponse>('/api/marathon-shape?weeks=12')
      .then(d => { if (!cancelled) setData(d) })
      .catch(e => console.warn('marathon-shape fetch failed:', e))
    return () => { cancelled = true }
  }, [])

  // Build a chronological view (oldest → newest) for the chart. API returns
  // newest-first so the badge can read `weeks[0]` directly; reverse for plot.
  // useMemo here is load-bearing: this array is a useEffect dep, so a stable
  // reference prevents the chart from being torn down + rebuilt on every
  // parent re-render (sport/days tab switches at the Progress page level).
  const chronological = useMemo(() => data ? [...data.weeks].reverse() : [], [data])
  const required = Math.pow(MS_DISTANCE_KM[distance], 1.23)
  const newest = data?.weeks[0] ?? null
  const shape = newest?.shape_pct ?? null
  const progressPct = shape !== null ? Math.round((shape / required) * 100) : null

  // badgeLabel is only used in the `progressPct !== null` JSX branch; the
  // null branch renders distinct copy ("No data" / "VO2max unavailable…") that
  // tells the user *why* it's missing. badgeColor is still resolved in both
  // branches because it feeds the chart annotation line.
  let badgeLabel = ''
  let badgeColor: string
  if (progressPct === null) {
    badgeColor = 'var(--text-dim)'
  } else if (progressPct >= 100) {
    badgeLabel = `Ready for ${distance}`
    badgeColor = STATUS_COLORS.green
  } else if (progressPct >= 80) {
    badgeLabel = `Almost ready for ${distance}`
    badgeColor = STATUS_COLORS.yellow
  } else {
    badgeLabel = `Building for ${distance}`
    badgeColor = STATUS_COLORS.red
  }

  // Effect 1 — chart instance lifecycle (depends only on the dataset).
  // Distance switches don't touch the underlying series, so we don't tear
  // the chart down for them — Effect 2 patches the annotation in place.
  useEffect(() => {
    if (!chartRef.current || chronological.length === 0) return

    chartInstRef.current?.destroy()

    const labels = chronological.map(w => w.week_end.replace(/^\d{4}-/, ''))
    const values = chronological.map(w => w.shape_pct)
    const color = CHART_COLORS.run
    const baseOpts = chartOptions(`Marathon Shape — 12 weeks`)

    chartInstRef.current = new Chart(chartRef.current, {
      type: 'line',
      data: {
        labels,
        datasets: [{
          label: 'Marathon Shape (%)',
          data: values,
          borderColor: color,
          backgroundColor: color + '20',
          fill: true,
          tension: 0.3,
          pointRadius: 3,
          pointBackgroundColor: color,
          borderWidth: 2,
          spanGaps: true,
        }],
      },
      options: {
        ...baseOpts,
        plugins: {
          // `chartOptions` returns `Record<string, unknown>`, so plugins is
          // `unknown` and not spreadable without a cast.
          ...(baseOpts.plugins as Record<string, unknown>),
          annotation: {
            annotations: {
              required: {
                type: 'line',
                yMin: required,
                yMax: required,
                borderColor: badgeColor,
                borderWidth: 2,
                borderDash: [5, 5],
                label: {
                  display: true,
                  content: `${distance} ${required.toFixed(1)}%`,
                  position: 'end',
                  backgroundColor: badgeColor,
                  color: '#fff',
                  font: { size: 10, weight: 'bold' },
                },
              },
            },
          },
        },
      },
    })

    return () => { chartInstRef.current?.destroy() }
    // Distance/required/badgeColor intentionally excluded — see Effect 2.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [chronological])

  // Effect 2 — annotation patch on distance switch (no chart rebuild).
  useEffect(() => {
    const chart = chartInstRef.current
    // Chart.js options is typed as `any` by the plugin; cast to access the
    // annotation plugin's nested config without dragging in its types.
    const annotation = (chart?.options?.plugins as { annotation?: { annotations?: { required?: Record<string, unknown> } } })
      ?.annotation?.annotations?.required
    if (!chart || !annotation) return

    annotation.yMin = required
    annotation.yMax = required
    annotation.borderColor = badgeColor
    const label = annotation.label as { content?: string; backgroundColor?: string } | undefined
    if (label) {
      label.content = `${distance} ${required.toFixed(1)}%`
      label.backgroundColor = badgeColor
    }
    chart.update('none')  // 'none' = no animation, instant re-render
  }, [distance, required, badgeColor])

  if (!data) return null

  const current = data.current_components
  // Spec §6 + §3 — Components targets scaled per selected distance using the
  // empirical Runalyze factor table. Marathon-baseline targets (current.*)
  // come from V^1.135 / ln(V/4)*12 and are scaled by MS_RUNALYZE_DISTANCE_FACTORS.
  // Long Run uses `displayed_target_long_run_km` (= scoring-internal + 13) per
  // §3 D2.A — matches Runalyze «Required Long Run» column.
  const distanceFactor = MS_RUNALYZE_DISTANCE_FACTORS[distance]
  const effectiveTargetWeeklyKm = current ? current.target_weekly_km * distanceFactor.weekly : null
  const effectiveTargetLongRunKm =
    current && distanceFactor.longjog !== null
      ? current.displayed_target_long_run_km * distanceFactor.longjog
      : null
  const weeklyPct =
    current && effectiveTargetWeeklyKm
      ? Math.round((current.actual_weekly_km / effectiveTargetWeeklyKm) * 100)
      : null
  const longjogPct =
    current && effectiveTargetLongRunKm
      ? Math.round((current.actual_longjog_km / effectiveTargetLongRunKm) * 100)
      : null
  // If every week is null (no vo2max anywhere in window), hide the chart —
  // a lone annotation line floating without data points is more confusing
  // than helpful. The "VO2max unavailable" message above covers the state.
  const hasAnyData = chronological.some(w => w.shape_pct !== null)

  // Phase 1.5 — ML-predicted finish time + pace for the currently-selected
  // distance. null when the run model is cold-start / below-acceptance / failed.
  const predicted = data.predicted_times?.[distance] ?? null
  const ciSpread = predicted
    ? (predicted.total_sec_ci_high - predicted.total_sec_ci_low) / predicted.total_sec
    : 0
  const wideCi = predicted && ciSpread > MS_WIDE_CI_THRESHOLD
  // XGBoost extrapolation check: tree-based models clamp predictions to the
  // nearest seen value when a feature falls outside training range. If the
  // picked race distance is > MS_EXTRAPOLATION_FACTOR × the user's longest
  // Run race, the model is extrapolating and the prediction is unreliable.
  // Surfaced as inline footnote.
  const selectedDistanceM = MS_DISTANCE_KM[distance] * 1000
  const isExtrapolated = !!(
    predicted &&
    data.max_run_race_distance_m !== null &&
    selectedDistanceM > data.max_run_race_distance_m * MS_EXTRAPOLATION_FACTOR
  )

  return (
    <div className="bg-surface border border-border rounded-[14px] p-4 mb-3">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[13px] text-text-dim">Marathon Shape</span>
      </div>

      <TabSwitcher tabs={MS_DISTANCE_TABS} active={distance} onChange={k => setDistance(k as MSDistance)} />

      {progressPct !== null && shape !== null ? (
        <div className="mb-3 mt-2">
          <div className="flex items-baseline gap-3">
            <span className="text-2xl font-bold" style={{ color: badgeColor }}>
              {progressPct}%
            </span>
            <span className="text-[13px]" style={{ color: badgeColor }}>{badgeLabel}</span>
          </div>
          <div className="text-[11px] text-text-dim mt-0.5">
            MS {shape.toFixed(1)} / target {required.toFixed(1)}
          </div>
        </div>
      ) : (
        <div className="mb-3 mt-2 text-[13px] text-text-dim">
          {newest === null ? 'No data' : 'VO2max unavailable for the most recent week'}
        </div>
      )}

      {predicted && (
        <div className="mb-3 pb-3 border-b border-border">
          <div className="text-[12px] text-text-dim mb-1.5">Predicted ({distance})</div>
          <div className="flex gap-6 text-[13px]">
            <div>
              <div className="text-text-dim text-[11px]">Time</div>
              <div className="font-mono font-semibold">{formatHMS(predicted.total_sec)}</div>
              <div className="text-text-dim text-[11px] font-mono">
                {formatHMS(predicted.total_sec_ci_low)} – {formatHMS(predicted.total_sec_ci_high)}
              </div>
            </div>
            <div>
              <div className="text-text-dim text-[11px]">Pace</div>
              <div className="font-mono font-semibold">{formatPace(predicted.pace_sec_per_km)}</div>
              <div className="text-text-dim text-[11px] font-mono">
                {formatPace(predicted.pace_ci_low)} – {formatPace(predicted.pace_ci_high)}
              </div>
            </div>
          </div>
          {wideCi && (
            <div className="text-[10px] text-text-dim mt-1.5 italic">
              Model uncertainty high — limited race history; bands will tighten as more data arrives.
            </div>
          )}
          {isExtrapolated && data.max_run_race_distance_m !== null && (
            <div className="text-[10px] text-text-dim mt-1.5 italic">
              Extrapolated — your longest race is {(data.max_run_race_distance_m / 1000).toFixed(1)} km,
              prediction for {distance} extrapolates beyond your training distribution.
              CI bands don't account for this; treat with caution until you log a race closer to this distance.
            </div>
          )}
        </div>
      )}

      {hasAnyData && (
        <ChartCard>
          <canvas ref={chartRef} />
        </ChartCard>
      )}

      {current && (
        <div className="mt-3 pt-3 border-t border-border text-[12px] text-text-dim space-y-1">
          <div className="flex justify-between">
            <span>Weekly volume ({distance})</span>
            <span className="font-mono">
              {num(current.actual_weekly_km, 1)} / {effectiveTargetWeeklyKm !== null ? num(effectiveTargetWeeklyKm, 1) : '—'} km
              {weeklyPct !== null && <span className="text-text-dim ml-2">({weeklyPct}%)</span>}
            </span>
          </div>
          <div className="flex justify-between">
            <span>Long run ({distance})</span>
            <span className="font-mono">
              {effectiveTargetLongRunKm !== null
                ? `${num(current.actual_longjog_km, 1)} / ${num(effectiveTargetLongRunKm, 1)} km`
                : 'n/a'}
              {longjogPct !== null && <span className="text-text-dim ml-2">({longjogPct}%)</span>}
            </span>
          </div>
          <div className="flex justify-between">
            <span>VO2max</span>
            <span className="font-mono">{num(current.vo2max, 1)}</span>
          </div>
        </div>
      )}
    </div>
  )
}


interface ProgressionData {
  status: string
  shap?: {
    features?: { name: string; importance: number; direction: string }[]
    latest_drivers?: { name: string; shap: number; value: number }[]
  }
  r2?: number
  n_examples?: number
  trained_at?: string
}

const FEATURE_LABELS: Record<string, string> = {
  n_sessions: 'Sessions',
  sessions_per_week: 'Sessions/week',
  decoupling_median: 'Cardiac drift',
  recovery_mean: 'Recovery',
  total_sessions_all: 'All sessions',
  ctl_delta: 'CTL change',
  ctl_mean: 'CTL avg',
  low_pct: 'Low zone %',
  mid_pct: 'Mid zone %',
  high_pct: 'High zone %',
  weekly_tss: 'Weekly TSS',
  weekly_hours: 'Weekly hours',
  total_hours: 'Total hours',
  total_tss: 'Total TSS',
  total_tss_all: 'Total TSS (all)',
  ef_mean: 'EF avg',
  ef_std: 'EF variability',
  recovery_below_40: 'Low recovery days',
  sleep_mean: 'Sleep score',
  hrv_mean: 'HRV avg',
  rhr_mean: 'RHR avg',
  ctl_max: 'CTL max',
  tsb_mean: 'TSB avg',
  tsb_min: 'TSB min',
  longest_min: 'Longest session',
}

function ProgressionWidget() {
  const [data, setData] = useState<ProgressionData | null>(null)

  useEffect(() => {
    let cancelled = false
    apiFetch<ProgressionData>('/api/progression?sport=Ride')
      .then(d => { if (!cancelled) setData(d) })
      .catch(e => { if (!cancelled) console.warn('progression fetch failed:', e) })
    return () => { cancelled = true }
  }, [])

  if (!data || data.status !== 'ok' || !data.shap?.features?.length) return null
  if (data.r2 == null || data.r2 < 0) return null

  const features = data.shap.features.slice(0, 5)
  const isWeak = data.r2 < 0.3

  return (
    <div className="bg-surface border border-border rounded-[14px] p-4 mb-3">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[13px] text-text-dim">EF Drivers (Ride)</span>
        <span className="text-[10px] text-text-dim font-mono">
          {data.n_examples} weeks · R²={data.r2?.toFixed(2)}
        </span>
      </div>
      {isWeak && (
        <div className="text-[10px] text-text-dim bg-surface-2 rounded px-2 py-1 mb-3">
          Low R² — not enough data yet, insights may be unreliable
        </div>
      )}

      <div className="space-y-2">
        {features.map((f) => {
          const label = FEATURE_LABELS[f.name] || f.name
          const isPositive = f.direction === 'positive'
          const color = isPositive ? '#22c55e' : '#ef4444'
          const maxImp = features[0].importance || 1
          const width = Math.max(8, (f.importance / maxImp) * 100)

          return (
            <div key={f.name} className="flex items-center gap-2">
              <span className="text-[11px] text-text-dim w-28 truncate text-right">{label}</span>
              <div className="flex-1 h-4 bg-surface-2 rounded overflow-hidden">
                <div
                  className="h-full rounded flex items-center justify-end pr-1"
                  style={{ width: `${width}%`, backgroundColor: color + '30', borderRight: `3px solid ${color}` }}
                >
                  <span className="text-[9px] font-mono" style={{ color }}>{isPositive ? '↑' : '↓'}</span>
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {data.shap.latest_drivers && data.shap.latest_drivers.length > 0 && (
        <div className="mt-3 pt-2 border-t border-border">
          <span className="text-[10px] text-text-dim">Latest week drivers:</span>
          <div className="flex flex-wrap gap-1.5 mt-1">
            {data.shap.latest_drivers.map((d) => (
              <span
                key={d.name}
                className="text-[10px] font-mono px-1.5 py-0.5 rounded"
                style={{
                  color: d.shap > 0 ? '#22c55e' : '#ef4444',
                  backgroundColor: d.shap > 0 ? '#22c55e15' : '#ef444415',
                }}
              >
                {d.shap > 0 ? '↑' : '↓'} {FEATURE_LABELS[d.name] || d.name}
              </span>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}


function FitnessProjectionChart() {
  const chartRef = useRef<HTMLCanvasElement>(null)
  const chartInstRef = useRef<Chart | null>(null)
  const [projection, setProjection] = useState<FitnessProjectionData | null>(null)

  useEffect(() => {
    apiFetch<FitnessProjectionData>('/api/fitness-projection')
      .then(setProjection)
      .catch(e => console.warn('fitness-projection fetch failed:', e))
  }, [])

  useEffect(() => {
    if (!chartRef.current || !projection || projection.count === 0) return

    chartInstRef.current?.destroy()

    const today = new Date().toISOString().slice(0, 10)
    const todayIdx = projection.dates.findIndex(d => d >= today)

    // Split into historical (past) and projection (future) segments
    const labels = projection.dates.map(d => d.slice(5)) // MM-DD

    // Split into past (solid) and future (dashed) at today's position.
    // If all dates are before today, treat everything as past — no future series.
    const splitIdx = todayIdx >= 0 ? todayIdx : projection.dates.length
    const ctlPast = projection.ctl.map((v, i) => i > splitIdx ? null : v)
    const ctlFuture = todayIdx >= 0 ? projection.ctl.map((v, i) => i < splitIdx ? null : v) : projection.ctl.map(() => null)
    const atlPast = projection.atl.map((v, i) => i > splitIdx ? null : v)
    const atlFuture = todayIdx >= 0 ? projection.atl.map((v, i) => i < splitIdx ? null : v) : projection.atl.map(() => null)

    chartInstRef.current = new Chart(chartRef.current, {
      type: 'line',
      data: {
        labels,
        datasets: [
          {
            label: 'CTL',
            data: ctlPast,
            borderColor: CHART_COLORS.ctl,
            fill: false,
            cubicInterpolationMode: 'monotone',
            pointRadius: 0,
            borderWidth: 2,
            spanGaps: true,
          },
          {
            label: 'CTL (projection)',
            data: ctlFuture,
            borderColor: CHART_COLORS.ctl,
            borderDash: [6, 4],
            fill: false,
            cubicInterpolationMode: 'monotone',
            pointRadius: 0,
            borderWidth: 2,
            spanGaps: true,
          },
          {
            label: 'ATL',
            data: atlPast,
            borderColor: CHART_COLORS.atl,
            cubicInterpolationMode: 'monotone',
            pointRadius: 0,
            borderWidth: 1.5,
            spanGaps: true,
          },
          {
            label: 'ATL (projection)',
            data: atlFuture,
            borderColor: CHART_COLORS.atl,
            borderDash: [6, 4],
            cubicInterpolationMode: 'monotone',
            pointRadius: 0,
            borderWidth: 1.5,
            spanGaps: true,
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        plugins: {
          legend: { position: 'top', labels: { boxWidth: 12, padding: 10, font: { size: 13 }, filter: (item) => !item.text.includes('projection') } },
          title: { display: true, text: 'Fitness Projection (CTL / ATL)', font: { size: 14, weight: 'bold' } },
          annotation: todayIdx >= 0 ? {
            annotations: {
              todayLine: {
                type: 'line',
                xMin: todayIdx,
                xMax: todayIdx,
                borderColor: 'rgba(128,128,128,0.5)',
                borderWidth: 1,
                borderDash: [4, 4],
                label: { display: false },
              },
            },
          } : {},
        },
        scales: {
          x: { grid: { color: 'rgba(128,128,128,0.2)' }, ticks: { font: { size: 12 }, maxRotation: 45, autoSkip: true, maxTicksLimit: 10 } },
          y: { grid: { color: 'rgba(128,128,128,0.2)' }, ticks: { font: { size: 12 } } },
        },
      },
    })

    return () => { chartInstRef.current?.destroy() }
  }, [projection])

  if (!projection || projection.count === 0) return null

  return (
    <ChartCard>
      <canvas ref={chartRef} />
    </ChartCard>
  )
}


function EmptyState({ sport }: { sport: Sport }) {
  const sportLabel = sport === 'bike' ? 'cycling' : sport === 'run' ? 'running' : 'swimming'
  return (
    <div className="text-center py-12 text-text-dim">
      <div className="text-3xl mb-3">{sport === 'bike' ? '\u{1F6B4}' : sport === 'run' ? '\u{1F3C3}' : '\u{1F3CA}'}</div>
      <div className="text-sm">No Z2 {sportLabel} sessions found in this period</div>
      <div className="text-[12px] mt-1">Complete steady-state workouts to track progress</div>
    </div>
  )
}
