import { useState, useEffect, useRef } from 'react'
import { Link } from 'react-router-dom'
import { Chart, registerables } from 'chart.js'
import annotationPlugin from 'chartjs-plugin-annotation'
import Layout from '../components/Layout'
import TabSwitcher from '../components/TabSwitcher'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import { useApi } from '../hooks/useApi'
import { apiFetch } from '../api/client'
import { CHART_COLORS } from '../lib/constants'
import { num } from '../lib/formatters'
import type { ProgressResponse, DecouplingTrend } from '../api/types'

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

export default function Progress() {
  const [sport, setSport] = useState<Sport>('bike')
  const [days, setDays] = useState('180')

  const endpoint = `/api/progress?sport=${sport}&days=${days}`
  const { data, loading, error } = useApi<ProgressResponse>(endpoint)

  return (
    <Layout title="Progress">
      <TabSwitcher tabs={SPORT_TABS} active={sport} onChange={k => setSport(k as Sport)} />
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
      <TrendBadge data={data} sport={sport} />
      {data.decoupling_trend && <DecouplingBadge trend={data.decoupling_trend} />}
      {sport !== 'swim' && <PolarizationWidget sport={sport} />}
      {sport === 'bike' && <ProgressionWidget />}
      {sport === 'swim' ? <SwimCharts data={data} /> : (
        <>
          <EFChart data={data} sport={sport} />
          <DecouplingChart data={data} sport={sport} />
        </>
      )}
      <FitnessProjectionChart />
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
    <div className="bg-surface border border-border rounded-[14px] p-4 mb-3 flex items-center justify-between">
      <span className="text-[13px] text-text-dim">{label}</span>
      <span className="text-sm font-bold" style={{ color }}>
        {arrow} {Math.abs(trend.pct).toFixed(1)}%
      </span>
    </div>
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

  return (
    <ChartContainer>
      <canvas ref={chartRef} />
    </ChartContainer>
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
          title: { display: true, text: `Cardiac Drift — ${sport === 'bike' ? 'Bike' : 'Run'}`, font: { size: 13 } },
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
              font: { size: 10 },
              callback: (val) => labels[val as number] || '',
              maxRotation: 45,
            },
            grid: { color: 'rgba(128,128,128,0.15)' },
          },
          y: {
            grid: { color: 'rgba(128,128,128,0.15)' },
            ticks: {
              font: { size: 10 },
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
    <ChartContainer>
      <canvas ref={chartRef} />
    </ChartContainer>
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

  return (
    <>
      <ChartContainer><canvas ref={paceRef} /></ChartContainer>
      {hasSwolf && <ChartContainer><canvas ref={swolfRef} /></ChartContainer>}
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
                {act.ef != null && <span>{num(act.ef, 4)}</span>}
                {act.decoupling != null && (
                  <span
                    className="text-[12px] ml-2 font-medium"
                    style={{ color: STATUS_COLORS[act.decoupling_status || 'green'] }}
                  >
                    {num(act.decoupling, 1)}%
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
  total_tss_all: 'Total TSS',
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
  ef_mean: 'EF avg',
  recovery_below_40: 'Low recovery days',
  sleep_mean: 'Sleep score',
  hrv_mean: 'HRV avg',
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

  const features = data.shap.features.slice(0, 5)

  return (
    <div className="bg-surface border border-border rounded-[14px] p-4 mb-3">
      <div className="flex items-center justify-between mb-3">
        <span className="text-[13px] text-text-dim">EF Drivers (Ride)</span>
        <span className="text-[10px] text-text-dim font-mono">
          {data.n_examples} weeks · R²={data.r2?.toFixed(2)}
        </span>
      </div>

      <div className="space-y-2">
        {features.map((f, i) => {
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
            {data.shap.latest_drivers.map((d, i) => (
              <span
                key={i}
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
            backgroundColor: CHART_COLORS.ctl + '15',
            fill: true,
            tension: 0.3,
            pointRadius: 0,
            borderWidth: 2,
            spanGaps: true,
          },
          {
            label: 'CTL (projection)',
            data: ctlFuture,
            borderColor: CHART_COLORS.ctl,
            borderDash: [6, 4],
            backgroundColor: CHART_COLORS.ctl + '08',
            fill: true,
            tension: 0.3,
            pointRadius: 0,
            borderWidth: 2,
            spanGaps: true,
          },
          {
            label: 'ATL',
            data: atlPast,
            borderColor: CHART_COLORS.atl,
            tension: 0.3,
            pointRadius: 0,
            borderWidth: 1.5,
            spanGaps: true,
          },
          {
            label: 'ATL (projection)',
            data: atlFuture,
            borderColor: CHART_COLORS.atl,
            borderDash: [6, 4],
            tension: 0.3,
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
          legend: { position: 'top', labels: { boxWidth: 12, padding: 8, font: { size: 11 }, filter: (item) => !item.text.includes('projection') } },
          title: { display: true, text: 'Fitness Projection (CTL / ATL)', font: { size: 13 } },
          annotation: todayIdx >= 0 ? {
            annotations: {
              todayLine: {
                type: 'line',
                xMin: todayIdx,
                xMax: todayIdx,
                borderColor: 'rgba(128,128,128,0.5)',
                borderWidth: 1,
                borderDash: [4, 4],
                label: { content: 'Today', display: true, position: 'start', font: { size: 10 } },
              },
            },
          } : {},
        },
        scales: {
          x: { grid: { color: 'rgba(128,128,128,0.15)' }, ticks: { font: { size: 10 }, maxRotation: 45, maxTicksLimit: 12 } },
          y: { grid: { color: 'rgba(128,128,128,0.15)' }, ticks: { font: { size: 10 } } },
        },
      },
    })

    return () => { chartInstRef.current?.destroy() }
  }, [projection])

  if (!projection || projection.count === 0) return null

  return (
    <ChartContainer>
      <canvas ref={chartRef} />
    </ChartContainer>
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

function ChartContainer({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-surface border border-border rounded-[14px] p-3 mb-3">
      <div style={{ maxHeight: 250 }}>{children}</div>
    </div>
  )
}

function chartOptions(title: string): Record<string, unknown> {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { position: 'top', labels: { boxWidth: 12, padding: 8, font: { size: 11 } } },
      title: { display: true, text: title, font: { size: 13 } },
    },
    scales: {
      x: { grid: { color: 'rgba(128,128,128,0.15)' }, ticks: { font: { size: 10 }, maxRotation: 45 } },
      y: { grid: { color: 'rgba(128,128,128,0.15)' }, ticks: { font: { size: 10 } } },
    },
  }
}
