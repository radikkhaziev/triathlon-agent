import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Chart, registerables } from 'chart.js'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import { apiFetch } from '../api/client'
import { CHART_COLORS } from '../lib/constants'
import type { TrainingLoadSeries, ActivitiesSeries, GoalResponse, WeeklyRecapBucket, WeeklyRecapResponse, RecoveryTrendSeries } from '../api/types'

Chart.register(...registerables)

const TABS = ['load', 'goal', 'week'] as const
type TabKey = typeof TABS[number]

const TAB_LABELS: Record<TabKey, string> = {
  load: 'Load',
  goal: 'Goal',
  week: 'Week',
}

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState<TabKey>('load')

  return (
    <Layout maxWidth="480px">
      {/* Sticky Tabs */}
      <div className="flex gap-1 py-3 sticky top-0 bg-bg z-10">
        {TABS.map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`flex-1 py-2 px-1 border-none rounded-lg text-[13px] font-semibold cursor-pointer transition-all font-sans ${
              activeTab === tab
                ? 'bg-[var(--button)] text-[var(--button-text)]'
                : 'bg-[var(--surface)] text-text-dim'
            }`}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </div>

      {activeTab === 'load' && <LoadTab />}
      {activeTab === 'goal' && <GoalTab />}
      {activeTab === 'week' && <WeekTab />}
    </Layout>
  )
}

function TsbZoneBadge({ tsb }: { tsb: number | null }) {
  const { t } = useTranslation()
  if (tsb === null) return null
  let label: string, color: string
  if (tsb > 10) { label = t('dashboard.undertraining'); color = '#3b82f6' }
  else if (tsb >= -10) { label = t('dashboard.optimal'); color = '#22c55e' }
  else if (tsb >= -25) { label = t('dashboard.productive_overreach'); color = '#f59e0b' }
  else { label = t('dashboard.overtraining_risk'); color = '#ef4444' }

  const tsbStr = tsb > 0 ? `+${tsb.toFixed(0)}` : tsb.toFixed(0)
  return (
    <div className="bg-[var(--surface)] rounded-xl p-3 mb-3 flex justify-between items-center">
      <span className="text-[13px] text-text-dim">{t('dashboard.tsb_zone')}</span>
      <div className="flex items-center gap-2">
        <span className="text-[13px] font-mono font-semibold" style={{ color }}>{tsbStr}</span>
        <span className="text-xs font-semibold px-2 py-0.5 rounded-full text-white" style={{ background: color }}>{label}</span>
      </div>
    </div>
  )
}

function LoadTab() {
  const loadChartRef = useRef<HTMLCanvasElement>(null)
  const tssChartRef = useRef<HTMLCanvasElement>(null)
  const recoveryChartRef = useRef<HTMLCanvasElement>(null)
  const chartsRef = useRef<Chart[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [currentTsb, setCurrentTsb] = useState<number | null>(null)

  useEffect(() => {
    Promise.all([
      apiFetch<TrainingLoadSeries>('/api/training-load?days=84'),
      apiFetch<ActivitiesSeries>('/api/activities?days=28'),
      apiFetch<RecoveryTrendSeries>('/api/recovery-trend?days=21').catch(() => null),
    ]).then(([loadData, actData, recData]) => {
      chartsRef.current.forEach(c => c.destroy())
      chartsRef.current = []

      if (loadData.tsb?.length) {
        setCurrentTsb(loadData.tsb[loadData.tsb.length - 1])
      }

      if (loadChartRef.current && loadData.dates?.length) {
        const labels = loadData.dates.map(d => { const p = d.split('-'); return `${p[1]}/${p[2]}` })
        chartsRef.current.push(new Chart(loadChartRef.current, {
          type: 'line',
          data: {
            labels,
            datasets: [
              { label: 'CTL', data: loadData.ctl, borderColor: CHART_COLORS.ctl, fill: false, tension: 0.3, pointRadius: 0, borderWidth: 2 },
              { label: 'ATL', data: loadData.atl, borderColor: CHART_COLORS.atl, fill: false, tension: 0.3, pointRadius: 0, borderWidth: 2 },
              { label: 'TSB', data: loadData.tsb, borderColor: CHART_COLORS.tsb, backgroundColor: CHART_COLORS.tsb + '15', fill: true, tension: 0.3, pointRadius: 0, borderWidth: 2 },
            ],
          },
          options: chartOptions('Training Load (12 weeks)'),
        }))
      }

      if (tssChartRef.current && actData.activities?.length) {
        const byDate: Record<string, { swim: number; ride: number; run: number }> = {}
        for (const act of actData.activities) {
          if (!byDate[act.date]) byDate[act.date] = { swim: 0, ride: 0, run: 0 }
          const sport = act.sport === 'swimming' ? 'swim' : act.sport === 'cycling' ? 'ride' : act.sport === 'running' ? 'run' : null
          if (sport && act.tss) byDate[act.date][sport] += act.tss
        }
        const dates = Object.keys(byDate).sort()
        const labels = dates.map(d => { const p = d.split('-'); return `${p[1]}/${p[2]}` })
        chartsRef.current.push(new Chart(tssChartRef.current, {
          type: 'bar',
          data: {
            labels,
            datasets: [
              { label: 'Swim', data: dates.map(d => byDate[d].swim), backgroundColor: CHART_COLORS.swim + 'cc', borderRadius: 2 },
              { label: 'Ride', data: dates.map(d => byDate[d].ride), backgroundColor: CHART_COLORS.ride + 'cc', borderRadius: 2 },
              { label: 'Run', data: dates.map(d => byDate[d].run), backgroundColor: CHART_COLORS.run + 'cc', borderRadius: 2 },
            ],
          },
          options: { ...chartOptions('Daily TSS by Sport'), scales: { x: { stacked: true, ticks: { font: { size: 10 }, maxRotation: 45 } }, y: { stacked: true, ticks: { font: { size: 10 } } } } },
        }))
      }

      if (recoveryChartRef.current && recData?.dates?.length) {
        const labels = recData.dates.map(d => { const p = d.split('-'); return `${p[1]}/${p[2]}` })
        chartsRef.current.push(new Chart(recoveryChartRef.current, {
          type: 'line',
          data: {
            labels,
            datasets: [
              {
                label: 'Recovery Score',
                data: recData.recovery,
                borderColor: '#a855f7',
                backgroundColor: '#a855f720',
                fill: true,
                tension: 0.4,
                pointRadius: 3,
                pointBackgroundColor: '#a855f7',
                borderWidth: 2,
                yAxisID: 'y',
              },
              {
                label: 'HRV (RMSSD)',
                data: recData.hrv,
                borderColor: '#f59e0b',
                fill: false,
                tension: 0.4,
                pointRadius: 2,
                pointBackgroundColor: '#f59e0b',
                borderWidth: 1.5,
                yAxisID: 'y1',
              },
            ],
          },
          options: {
            responsive: true,
            maintainAspectRatio: false,
            plugins: {
              legend: { position: 'top', labels: { boxWidth: 12, padding: 8, font: { size: 11 } } },
              title: { display: true, text: 'Recovery & HRV (21 days)', font: { size: 13 } },
            },
            scales: {
              x: { grid: { color: 'rgba(128,128,128,0.15)' }, ticks: { font: { size: 10 }, maxRotation: 45 } },
              y: { min: 0, max: 100, grid: { color: 'rgba(128,128,128,0.15)' }, ticks: { font: { size: 10 } }, position: 'left' },
              y1: { min: 30, max: 75, grid: { drawOnChartArea: false }, ticks: { font: { size: 10 } }, position: 'right' },
            },
          },
        }))
      }
    }).catch(err => setError(err instanceof Error ? err.message : 'Failed to load')).finally(() => setLoading(false))

    return () => { chartsRef.current.forEach(c => c.destroy()) }
  }, [])

  if (loading) return <LoadingSpinner />
  if (error) return <ErrorMessage message={error} />

  return (
    <>
      <ChartContainer><canvas ref={loadChartRef} /></ChartContainer>
      <TsbZoneBadge tsb={currentTsb} />
      <ChartContainer><canvas ref={tssChartRef} /></ChartContainer>
      <ChartContainer><canvas ref={recoveryChartRef} /></ChartContainer>
    </>
  )
}

function GoalTab() {
  const chartRef = useRef<HTMLCanvasElement>(null)
  const chartInstRef = useRef<Chart | null>(null)
  const [goal, setGoal] = useState<GoalResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([
      apiFetch<GoalResponse>('/api/goal'),
      apiFetch<TrainingLoadSeries>('/api/training-load?days=84'),
    ]).then(([goalData, loadData]) => {
      setGoal(goalData)
      if (chartRef.current && loadData.dates?.length) {
        chartInstRef.current?.destroy()
        const labels = loadData.dates.map(d => { const p = d.split('-'); return `${p[1]}/${p[2]}` })
        chartInstRef.current = new Chart(chartRef.current, {
          type: 'line',
          data: {
            labels,
            datasets: [
              { label: 'Swim CTL', data: loadData.ctl_swim || [], borderColor: CHART_COLORS.swim, tension: 0.3, pointRadius: 0, borderWidth: 2 },
              { label: 'Ride CTL', data: loadData.ctl_ride || [], borderColor: CHART_COLORS.ride, tension: 0.3, pointRadius: 0, borderWidth: 2 },
              { label: 'Run CTL', data: loadData.ctl_run || [], borderColor: CHART_COLORS.run, tension: 0.3, pointRadius: 0, borderWidth: 2 },
            ],
          },
          options: chartOptions('CTL by Sport'),
        })
      }
    }).catch(err => setError(err instanceof Error ? err.message : 'Failed to load')).finally(() => setLoading(false))

    return () => { chartInstRef.current?.destroy() }
  }, [])

  if (loading) return <LoadingSpinner />
  if (error) return <ErrorMessage message={error} />
  if (!goal) return <div className="text-center py-6 text-text-dim">No goal data.</div>

  return (
    <>
      <div className="text-center py-4 text-xl font-bold">
        <span className="text-[var(--button)]">{goal.weeks_remaining}</span> weeks to {goal.event_name}
      </div>
      {(['swim', 'bike', 'run'] as const).map(sport => {
        const pct = goal[`${sport}_pct`]
        const colors: Record<string, string> = { swim: '#3b82f6', bike: '#22c55e', run: '#f59e0b' }
        return (
          <div key={sport} className="flex items-center gap-2 mb-2">
            <span className="w-[50px] text-[13px] font-semibold capitalize">{sport}</span>
            <div className="flex-1 h-2.5 bg-bg rounded-full overflow-hidden">
              <div className="h-full rounded-full transition-[width] duration-500" style={{ width: `${Math.min(100, pct)}%`, background: colors[sport] }} />
            </div>
            <span className="w-10 text-[13px] text-right">{pct.toFixed(0)}%</span>
          </div>
        )
      })}
      <ChartContainer><canvas ref={chartRef} /></ChartContainer>
    </>
  )
}

// Sport buckets are keyed by the backend's normalized name (swimming / cycling
// / running — what _SPORT_MAP in api/routers/dashboard.py emits). Anything else
// is dropped server-side, so we don't need a fallback row in the UI.
const WEEK_SPORT_META: Record<string, { label: string; emoji: string }> = {
  swimming: { label: 'Swim', emoji: '🏊' },
  cycling: { label: 'Ride', emoji: '🚴' },
  running: { label: 'Run', emoji: '🏃' },
}
const WEEK_SPORT_ORDER = ['swimming', 'cycling', 'running']

function formatHm(seconds: number): string {
  if (seconds <= 0) return '—'
  const h = Math.floor(seconds / 3600)
  const m = Math.round((seconds % 3600) / 60)
  if (h === 0) return `${m}m`
  if (m === 0) return `${h}h`
  return `${h}h ${m}m`
}

// Garmin-style: meters → km. Sub-1km still shows in metres so 800m doesn't
// round to "0.8 km" and lose readability for short swim sessions. Above 100km
// we drop the decimal — the extra digit is noise at that scale.
function formatKm(meters: number): string {
  if (meters <= 0) return '—'
  if (meters < 1000) return `${Math.round(meters)} m`
  const km = meters / 1000
  return km >= 100 ? `${km.toFixed(0)} km` : `${km.toFixed(1)} km`
}

function formatWeekRange(weekStart: string, weekEnd: string): string {
  const start = new Date(weekStart + 'T00:00:00')
  const end = new Date(weekEnd + 'T00:00:00')
  const opts: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' }
  return `${start.toLocaleDateString('en-US', opts)} – ${end.toLocaleDateString('en-US', opts)}`
}

function tsbZone(tsb: number): { label: string; color: string } {
  if (tsb > 10) return { label: 'Under', color: '#3b82f6' }
  if (tsb >= -10) return { label: 'Optimal', color: '#22c55e' }
  if (tsb >= -25) return { label: 'Productive', color: '#f59e0b' }
  return { label: 'Risk', color: '#ef4444' }
}

function WeekLoadCard({ week }: { week: WeeklyRecapBucket }) {
  const { ctl_start, ctl_end, ctl_delta, tsb_end } = week
  // Bootstrap or pre-Intervals weeks have no wellness rows at the bookends —
  // render nothing rather than a half-rendered "CTL — → 73 (—)" row.
  if (ctl_start === null || ctl_end === null) return null

  const deltaStr =
    ctl_delta === null ? '' : ctl_delta > 0 ? `+${ctl_delta.toFixed(1)}` : ctl_delta.toFixed(1)
  const deltaColor =
    ctl_delta === null
      ? 'var(--text-dim)'
      : ctl_delta > 0.5
        ? '#22c55e'
        : ctl_delta < -0.5
          ? '#ef4444'
          : 'var(--text-dim)'
  const zone = tsb_end !== null ? tsbZone(tsb_end) : null
  const tsbStr = tsb_end === null ? '—' : tsb_end > 0 ? `+${tsb_end.toFixed(0)}` : tsb_end.toFixed(0)

  return (
    <div className="flex justify-between items-center text-[12px] text-text-dim mt-1.5">
      <span>
        CTL{' '}
        <span className="font-mono text-text">
          {ctl_start !== null ? ctl_start.toFixed(0) : '—'}
          {' → '}
          {ctl_end !== null ? ctl_end.toFixed(0) : '—'}
        </span>{' '}
        {ctl_delta !== null && (
          <span className="font-mono font-semibold" style={{ color: deltaColor }}>
            ({deltaStr})
          </span>
        )}
      </span>
      {zone && (
        <span className="flex items-center gap-1.5">
          <span className="font-mono" style={{ color: zone.color }}>TSB {tsbStr}</span>
          <span
            className="text-[10px] font-semibold px-1.5 py-0.5 rounded-full text-white"
            style={{ background: zone.color }}
          >
            {zone.label}
          </span>
        </span>
      )}
    </div>
  )
}

function WeekCard({ week, isCurrent }: { week: WeeklyRecapBucket; isCurrent: boolean }) {
  const sports = WEEK_SPORT_ORDER.filter(s => week.by_sport[s])
  const totalTss = sports.reduce((a, s) => a + (week.by_sport[s]?.tss || 0), 0)

  return (
    <div className="bg-[var(--surface)] rounded-xl p-3 mb-3">
      <div className="flex justify-between items-baseline mb-1">
        <div className="text-sm font-bold">
          {formatWeekRange(week.week_start, week.week_end)}
          {isCurrent && <span className="ml-2 text-[10px] uppercase font-semibold text-text-dim">This week</span>}
        </div>
        <div className="text-[13px] font-semibold">TSS {totalTss.toFixed(0)}</div>
      </div>
      {sports.length === 0 ? (
        <div className="text-[13px] text-text-dim py-1">No activities</div>
      ) : (
        sports.map(sport => {
          const s = week.by_sport[sport]
          const meta = WEEK_SPORT_META[sport]
          return (
            <div key={sport} className="flex justify-between items-center py-1 text-[13px]">
              <span>{meta.emoji} <span className="font-semibold">{meta.label}</span></span>
              <div className="flex gap-3 tabular-nums">
                <span className="text-text-dim w-14 text-right">{formatHm(s.duration_sec)}</span>
                <span className="text-text-dim w-16 text-right">{formatKm(s.distance_m)}</span>
                <span className="font-semibold w-14 text-right">{s.tss.toFixed(0)}</span>
              </div>
            </div>
          )
        })
      )}
      <WeekLoadCard week={week} />
    </div>
  )
}

function WeekTab() {
  const [recap, setRecap] = useState<WeeklyRecapResponse | null>(null)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    apiFetch<WeeklyRecapResponse>(`/api/weekly-recap?weeks=4&offset=${offset}`)
      .then(setRecap)
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [offset])

  if (loading && !recap) return <LoadingSpinner />
  if (error) return <ErrorMessage message={error} />
  if (!recap) return null

  // ``has_prev`` reflects whether ANY activity exists before the window start,
  // so we can scroll into recovery weeks with empty buckets without dead-end
  // navigation. The server caps ``offset`` at -52 (FastAPI ge=-52); without this
  // clamp athletes with >1y of history would see has_prev=true at the cap and
  // the next click would 422. ``canNext`` is bound to offset directly — once
  // the freshest visible week is the current week (offset 0), Later locks.
  const canPrev = recap.has_prev && offset > -52
  const canNext = offset < 0
  const range = recap.weeks.length > 0
    ? formatWeekRange(recap.weeks[recap.weeks.length - 1].week_start, recap.weeks[0].week_end)
    : null

  return (
    <>
      <div className="flex justify-between items-center mb-2">
        <button
          onClick={() => canPrev && setOffset(o => Math.max(o - 4, -52))}
          disabled={!canPrev}
          className="px-3 py-1.5 rounded-lg bg-[var(--surface)] text-[13px] font-semibold disabled:opacity-30 disabled:cursor-not-allowed border-none cursor-pointer"
        >
          ← Earlier
        </button>
        <span className="text-[12px] text-text-dim">{range}</span>
        <button
          onClick={() => canNext && setOffset(o => o + 4)}
          disabled={!canNext}
          className="px-3 py-1.5 rounded-lg bg-[var(--surface)] text-[13px] font-semibold disabled:opacity-30 disabled:cursor-not-allowed border-none cursor-pointer"
        >
          Later →
        </button>
      </div>

      {recap.weeks.length === 0 ? (
        <div className="text-center py-6 text-text-dim text-sm">No activities in this window</div>
      ) : (
        recap.weeks.map((w, i) => (
          <WeekCard key={w.week_start} week={w} isCurrent={offset === 0 && i === 0} />
        ))
      )}
    </>
  )
}

function ChartContainer({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-[var(--surface)] rounded-xl p-3 mb-3">
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
