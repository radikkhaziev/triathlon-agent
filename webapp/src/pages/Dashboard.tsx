import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Chart, registerables } from 'chart.js'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import { apiFetch } from '../api/client'
import { CHART_COLORS, SPORT_ICONS } from '../lib/constants'
import type { AuthMeResponse, TrainingLoadSeries, ActivitiesSeries, GoalResponse, WeeklySummary, ScheduledList, RecoveryTrendSeries } from '../api/types'

Chart.register(...registerables)

type TabKey = 'load' | 'goal' | 'week'

const TAB_LABELS: Record<TabKey, string> = {
  load: 'Load',
  goal: 'Goal',
  week: 'Week',
}

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState<TabKey>('load')
  // null = still loading, true/false = known. We optimistically render all
  // three tabs while loading so the common case (race set) doesn't flicker
  // a missing tab in for a frame; if the athlete turns out to have no goal
  // we drop the Goal tab and bounce them back to Load.
  const [hasGoal, setHasGoal] = useState<boolean | null>(null)

  useEffect(() => {
    apiFetch<AuthMeResponse>('/api/auth/me')
      .then(data => setHasGoal(!!data.goal))
      .catch(() => setHasGoal(null))
  }, [])

  useEffect(() => {
    if (hasGoal === false && activeTab === 'goal') setActiveTab('load')
  }, [hasGoal, activeTab])

  const tabs: TabKey[] = hasGoal === false ? ['load', 'week'] : ['load', 'goal', 'week']

  return (
    <Layout maxWidth="480px">
      {/* Sticky Tabs */}
      <div className="flex gap-1 py-3 sticky top-0 bg-bg z-10">
        {tabs.map(tab => (
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

// Sport key → display label + emoji + color. Reuses CHART_COLORS so the
// Goal-tab bars match the Load-tab TSS chart, and SPORT_ICONS so the
// vocabulary lines up with the bot (END-12 visuals decision).
const SPORT_META: Record<'swim' | 'ride' | 'run', { label: string; emoji: string; color: string }> = {
  swim: { label: 'Swim', emoji: SPORT_ICONS.Swim, color: CHART_COLORS.swim },
  ride: { label: 'Ride', emoji: SPORT_ICONS.Ride, color: CHART_COLORS.ride },
  run: { label: 'Run', emoji: SPORT_ICONS.Run, color: CHART_COLORS.run },
}

function ProgressBar({
  label,
  current,
  target,
  pct,
  color,
}: {
  label: React.ReactNode
  current: number | null
  target: number | null
  pct: number | null
  color: string
}) {
  // Bar fill is clamped to 100% so an over-target athlete (pct > 100) doesn't
  // overflow the row, but the numeric pct is shown as-is so they can see the
  // overshoot. A null pct (no target or no current CTL) renders an empty bar
  // and "—" instead of a misleading 0%.
  const fill = pct === null ? 0 : Math.min(100, Math.max(0, pct))
  return (
    <div className="flex items-center gap-2 mb-2">
      <span className="w-[72px] text-[13px] font-semibold">{label}</span>
      <div className="flex-1 h-2.5 bg-bg rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-[width] duration-500"
          style={{ width: `${fill}%`, background: color }}
        />
      </div>
      <span className="w-12 text-[13px] text-right tabular-nums">
        {pct === null ? '—' : `${pct}%`}
      </span>
      <span className="w-16 text-[11px] text-right text-text-dim tabular-nums">
        {current === null ? '—' : current.toFixed(0)}
        {target !== null ? ` / ${Math.round(target)}` : ''}
      </span>
    </div>
  )
}

function GoalTab() {
  const [goal, setGoal] = useState<GoalResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<GoalResponse>('/api/goal')
      .then(setGoal)
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <LoadingSpinner />
  if (error) return <ErrorMessage message={error} />
  // The Dashboard shell already hides the Goal tab when the athlete has no
  // race, so we should never actually hit `has_goal: false` here. Keep a
  // lightweight fallback in case the two fetches race (Dashboard reads
  // /api/auth/me, GoalTab reads /api/goal — the goal could be deleted
  // between the two).
  if (!goal || !goal.has_goal) {
    return <div className="text-center py-6 text-text-dim">No race set.</div>
  }

  return (
    <>
      <div className="text-center py-4 text-xl font-bold">
        <span className="text-[var(--button)]">{goal.weeks_remaining}</span> weeks to {goal.event_name}
      </div>

      <div className="bg-[var(--surface)] rounded-xl p-3 mb-3">
        <ProgressBar
          label={<span>Overall CTL</span>}
          current={goal.ctl_current}
          target={goal.ctl_target}
          pct={goal.overall_pct}
          color={CHART_COLORS.ctl}
        />

        {goal.per_sport && (
          <div className="mt-3 pt-3 border-t border-bg">
            {(['swim', 'ride', 'run'] as const).map(sport => {
              const block = goal.per_sport?.[sport]
              if (!block) return null
              const meta = SPORT_META[sport]
              return (
                <ProgressBar
                  key={sport}
                  label={<span>{meta.emoji} {meta.label}</span>}
                  current={block.ctl_current}
                  target={block.ctl_target}
                  pct={block.pct}
                  color={meta.color}
                />
              )
            })}
          </div>
        )}

        {!goal.per_sport && goal.ctl_target && (
          <div className="text-[11px] text-text-dim mt-3 pt-3 border-t border-bg">
            Set per-sport CTL targets in Settings to see swim / ride / run progress.
          </div>
        )}
      </div>
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

  const sportVals = Object.values(summary?.by_sport || {})
  const totalDur = sportVals.reduce((a, s) => a + s.duration_sec, 0)
  const totalDist = sportVals.reduce((a, s) => a + s.distance_m, 0)
  const totalTss = sportVals.reduce((a, s) => a + s.tss, 0)
  const showTotal = sportVals.length > 1

  return (
    <>
      {sched?.workouts?.length ? (
        <div className="bg-[var(--surface)] rounded-xl p-4 mb-3">
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
              {sched.workouts.map((w) => (
                <tr key={`${w.date}-${w.sport}-${w.workout_name}`}>
                  <td className="py-2 px-1 border-b border-[var(--surface)]">{w.date}</td>
                  <td className="py-2 px-1 border-b border-[var(--surface)]">{sportEmoji[w.sport] || '🏋'} {w.workout_name}</td>
                  <td className="py-2 px-1 border-b border-[var(--surface)]">{w.planned_tss ? w.planned_tss.toFixed(0) : '\u2014'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <div className="text-center py-6 text-text-dim text-sm">No scheduled workouts</div>
      )}

      {summary && (
        <div className="bg-[var(--surface)] rounded-xl p-4 mb-3">
          <div className="text-sm font-bold mb-2">Weekly Summary</div>
          {Object.entries(summary.by_sport || {}).map(([sport, s]) => (
            <div key={sport} className="flex justify-between items-center py-2 border-b border-bg last:border-b-0">
              <span className="text-sm">{sportEmoji[sport] || '🏋'} <span className="font-semibold capitalize">{sport}</span></span>
              <div className="flex gap-3 text-[13px]">
                <span className="text-text-dim">{(s.duration_sec / 3600).toFixed(1)}h</span>
                <span className="text-text-dim">{(s.distance_m / 1000).toFixed(1)}km</span>
                <span className="font-semibold">TSS {s.tss.toFixed(0)}</span>
              </div>
            </div>
          ))}
          {showTotal && (
            <div className="flex justify-between items-center pt-2 mt-1">
              <span className="text-sm font-bold">Total</span>
              <div className="flex gap-3 text-[13px]">
                <span className="text-text-dim">{(totalDur / 3600).toFixed(1)}h</span>
                <span className="text-text-dim">{(totalDist / 1000).toFixed(1)}km</span>
                <span className="font-bold">TSS {totalTss.toFixed(0)}</span>
              </div>
            </div>
          )}
        </div>
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
