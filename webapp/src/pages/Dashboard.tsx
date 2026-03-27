import { useState, useEffect, useRef } from 'react'
import { Chart, registerables } from 'chart.js'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import Gauge from '../components/Gauge'
import { apiFetch } from '../api/client'
import { CHART_COLORS } from '../lib/constants'
import type { DashboardResponse, TrainingLoadSeries, ActivitiesSeries, GoalResponse, WeeklySummary, ScheduledList } from '../api/types'

Chart.register(...registerables)

const TABS = ['today', 'load', 'goal', 'week'] as const
type TabKey = typeof TABS[number]

const TAB_LABELS: Record<TabKey, string> = {
  today: 'Today',
  load: 'Load',
  goal: 'Goal',
  week: 'Week',
}

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState<TabKey>('today')

  return (
    <Layout backTo="/" maxWidth="480px">
      {/* Sticky Tabs */}
      <div className="flex gap-1 py-3 sticky top-0 bg-bg z-10">
        {TABS.map(tab => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`flex-1 py-2 px-1 border-none rounded-lg text-[13px] font-semibold cursor-pointer transition-all font-sans ${
              activeTab === tab
                ? 'bg-[var(--button)] text-[var(--button-text)]'
                : 'bg-[var(--tg-theme-secondary-bg-color,#f0f0f0)] text-text-dim'
            }`}
          >
            {TAB_LABELS[tab]}
          </button>
        ))}
      </div>

      {activeTab === 'today' && <TodayTab />}
      {activeTab === 'load' && <LoadTab />}
      {activeTab === 'goal' && <GoalTab />}
      {activeTab === 'week' && <WeekTab />}
    </Layout>
  )
}

function TodayTab() {
  const [data, setData] = useState<DashboardResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<DashboardResponse>('/api/dashboard')
      .then(setData)
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <LoadingSpinner />
  if (error) return <ErrorMessage message={error} />
  if (!data?.has_data) return <div className="text-center py-6 text-text-dim text-sm">No data for today.</div>

  const colors: Record<string, string> = { green: '#22c55e', yellow: '#f59e0b', red: '#ef4444' }
  const color = colors[data.readiness_level] || colors.yellow
  const hrvDelta = data.hrv_baseline ? ((data.hrv_last - data.hrv_baseline) / data.hrv_baseline * 100).toFixed(0) : '\u2014'

  return (
    <>
      <div className="flex justify-center py-4">
        <div className="relative w-40 h-40">
          <Gauge score={data.readiness_score} color={color} size={160} lineWidth={12} />
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-center">
            <div className="text-4xl font-bold leading-none">{data.readiness_score}</div>
            <div className="text-xs text-text-dim mt-0.5">Readiness</div>
          </div>
        </div>
      </div>

      <div className="grid grid-cols-2 gap-2 mb-4">
        <MetricCard label="HRV \u0394" value={`${Number(hrvDelta) > 0 ? '+' : ''}${hrvDelta}%`} />
        <MetricCard label="Sleep" value={data.sleep_score != null ? String(data.sleep_score) : '\u2014'} />
        <MetricCard label="RHR" value={data.resting_hr ? `${data.resting_hr.toFixed(0)}` : '\u2014'} />
        <MetricCard label="CTL" value={data.ctl?.toFixed(0) ?? '\u2014'} />
      </div>

      <div className="bg-[var(--tg-theme-secondary-bg-color,#f0f0f0)] rounded-xl p-4 mb-3">
        <div className="text-sm font-bold mb-2">AI Recommendation</div>
        <p className="text-[13px] leading-relaxed whitespace-pre-wrap">{data.ai_recommendation || 'No recommendation available.'}</p>
      </div>
    </>
  )
}

function MetricCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="bg-[var(--tg-theme-secondary-bg-color,#f0f0f0)] rounded-xl p-3 text-center">
      <div className="text-[22px] font-bold">{value}</div>
      <div className="text-[11px] text-text-dim mt-0.5">{label}</div>
    </div>
  )
}

function LoadTab() {
  const loadChartRef = useRef<HTMLCanvasElement>(null)
  const tssChartRef = useRef<HTMLCanvasElement>(null)
  const chartsRef = useRef<Chart[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([
      apiFetch<TrainingLoadSeries>('/api/training-load?days=84'),
      apiFetch<ActivitiesSeries>('/api/activities?days=28'),
    ]).then(([loadData, actData]) => {
      chartsRef.current.forEach(c => c.destroy())
      chartsRef.current = []

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
        const byDate: Record<string, { swim: number; bike: number; run: number }> = {}
        for (const act of actData.activities) {
          if (!byDate[act.date]) byDate[act.date] = { swim: 0, bike: 0, run: 0 }
          const sport = act.sport === 'swimming' ? 'swim' : act.sport === 'cycling' ? 'bike' : act.sport === 'running' ? 'run' : null
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
              { label: 'Bike', data: dates.map(d => byDate[d].bike), backgroundColor: CHART_COLORS.bike + 'cc', borderRadius: 2 },
              { label: 'Run', data: dates.map(d => byDate[d].run), backgroundColor: CHART_COLORS.run + 'cc', borderRadius: 2 },
            ],
          },
          options: { ...chartOptions('Daily TSS by Sport'), scales: { x: { stacked: true, ticks: { font: { size: 10 }, maxRotation: 45 } }, y: { stacked: true, ticks: { font: { size: 10 } } } } },
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
      <ChartContainer><canvas ref={tssChartRef} /></ChartContainer>
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
              { label: 'Bike CTL', data: loadData.ctl_bike || [], borderColor: CHART_COLORS.bike, tension: 0.3, pointRadius: 0, borderWidth: 2 },
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

function WeekTab() {
  const [summary, setSummary] = useState<WeeklySummary | null>(null)
  const [sched, setSched] = useState<ScheduledList | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([
      apiFetch<WeeklySummary>('/api/weekly-summary'),
      apiFetch<ScheduledList>('/api/scheduled?days=7'),
    ]).then(([w, s]) => { setSummary(w); setSched(s) })
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <LoadingSpinner />
  if (error) return <ErrorMessage message={error} />

  const sportEmoji: Record<string, string> = { swimming: '🏊', cycling: '🚴', running: '🏃' }

  return (
    <>
      {sched?.workouts?.length ? (
        <div className="bg-[var(--tg-theme-secondary-bg-color,#f0f0f0)] rounded-xl p-4 mb-3">
          <div className="text-sm font-bold mb-2">Planned Workouts</div>
          <table className="w-full border-collapse text-[13px]">
            <thead>
              <tr>
                <th className="text-left py-1.5 px-1 border-b border-text-dim text-text-dim font-medium">Date</th>
                <th className="text-left py-1.5 px-1 border-b border-text-dim text-text-dim font-medium">Workout</th>
                <th className="text-left py-1.5 px-1 border-b border-text-dim text-text-dim font-medium">TSS</th>
              </tr>
            </thead>
            <tbody>
              {sched.workouts.map((w, i) => (
                <tr key={i}>
                  <td className="py-2 px-1 border-b border-[var(--tg-theme-secondary-bg-color,#f0f0f0)]">{w.date}</td>
                  <td className="py-2 px-1 border-b border-[var(--tg-theme-secondary-bg-color,#f0f0f0)]">{sportEmoji[w.sport] || '🏋'} {w.workout_name}</td>
                  <td className="py-2 px-1 border-b border-[var(--tg-theme-secondary-bg-color,#f0f0f0)]">{w.planned_tss ? w.planned_tss.toFixed(0) : '\u2014'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="text-center py-6 text-text-dim text-sm">No scheduled workouts</div>
      )}

      {summary && (
        <div className="bg-[var(--tg-theme-secondary-bg-color,#f0f0f0)] rounded-xl p-4 mb-3">
          <div className="text-sm font-bold mb-2">Weekly Summary</div>
          <pre className="text-[13px] leading-relaxed whitespace-pre-wrap">
            {Object.entries(summary.by_sport || {}).map(([sport, s]) => {
              const emoji = sportEmoji[sport] || '🏋'
              return `${emoji} ${sport}: ${(s.duration_sec / 3600).toFixed(1)}h, ${(s.distance_m / 1000).toFixed(1)}km, TSS ${s.tss.toFixed(0)}`
            }).join('\n')}
          </pre>
        </div>
      )}
    </>
  )
}

function ChartContainer({ children }: { children: React.ReactNode }) {
  return (
    <div className="bg-[var(--tg-theme-secondary-bg-color,#f0f0f0)] rounded-xl p-3 mb-3">
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
