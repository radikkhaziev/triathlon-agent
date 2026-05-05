import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Chart, registerables } from 'chart.js'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import { apiFetch } from '../api/client'
import { CHART_COLORS, SPORT_ICONS, TSB_ZONE_COLORS } from '../lib/constants'
import type { AuthMeResponse, TrainingLoadSeries, ActivitiesSeries, GoalResponse, WeeklyRecapBucket, WeeklyRecapResponse, RecoveryTrendSeries } from '../api/types'

Chart.register(...registerables)

type TabKey = 'load' | 'goal' | 'week'

const TAB_LABELS: Record<TabKey, string> = {
  load: 'Load',
  goal: 'Goal',
  week: 'Week',
}

export default function Dashboard() {
  const [activeTab, setActiveTab] = useState<TabKey>('load')
  // null = unknown (still loading OR /api/auth/me failed). We optimistically
  // render all three tabs in that case so the common path (race set) doesn't
  // flicker a missing tab in for a frame, and so a flaky network doesn't
  // strand the user without a Goal tab they actually own. If GoalTab itself
  // can't fetch, it renders <ErrorMessage/> with a retry path. Only an
  // explicit `false` from a successful response collapses the tabs to two.
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
  if (tsb > 10) { label = t('dashboard.undertraining'); color = TSB_ZONE_COLORS.under }
  else if (tsb >= -10) { label = t('dashboard.optimal'); color = TSB_ZONE_COLORS.optimal }
  else if (tsb >= -25) { label = t('dashboard.productive_overreach'); color = TSB_ZONE_COLORS.productive }
  else { label = t('dashboard.overtraining_risk'); color = TSB_ZONE_COLORS.risk }

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

type LoadTabData = {
  load: TrainingLoadSeries
  activities: ActivitiesSeries
  recovery: RecoveryTrendSeries | null
}

function LoadTab() {
  const loadChartRef = useRef<HTMLCanvasElement>(null)
  const tssChartRef = useRef<HTMLCanvasElement>(null)
  const recoveryChartRef = useRef<HTMLCanvasElement>(null)
  const chartsRef = useRef<Chart[]>([])
  const [data, setData] = useState<LoadTabData | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    Promise.all([
      apiFetch<TrainingLoadSeries>('/api/training-load?days=84'),
      apiFetch<ActivitiesSeries>('/api/activities?days=28'),
      apiFetch<RecoveryTrendSeries>('/api/recovery-trend?days=21').catch(() => null),
    ])
      .then(([loadData, actData, recData]) => setData({ load: loadData, activities: actData, recovery: recData }))
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load'))
  }, [])

  // Chart creation runs in a separate effect that fires AFTER `data` commits
  // and the canvases mount. The earlier "create charts inside Promise.then"
  // shape silently no-op'd because the refs were still null while the spinner
  // was rendered (END-51). Cleanup (return below) destroys old charts before
  // each re-run, so we don't need to clear `chartsRef` at the top.
  useEffect(() => {
    if (!data) return

    const { load: loadData, activities: actData, recovery: recData } = data

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
        options: {
          ...chartOptions('Daily TSS by Sport'),
          scales: {
            x: { stacked: true, grid: { color: 'rgba(128,128,128,0.2)' }, ticks: { font: { size: 12 }, maxRotation: 45, autoSkip: true, maxTicksLimit: 10 } },
            y: { stacked: true, grid: { color: 'rgba(128,128,128,0.2)' }, ticks: { font: { size: 12 } } },
          },
        },
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
            legend: { position: 'top', labels: { boxWidth: 12, padding: 10, font: { size: 13 } } },
            title: { display: true, text: 'Recovery & HRV (21 days)', font: { size: 14, weight: 'bold' } },
          },
          scales: {
            x: { grid: { color: 'rgba(128,128,128,0.2)' }, ticks: { font: { size: 12 }, maxRotation: 45, autoSkip: true, maxTicksLimit: 10 } },
            y: { min: 0, max: 100, grid: { color: 'rgba(128,128,128,0.2)' }, ticks: { font: { size: 12 } }, position: 'left' },
            y1: { min: 30, max: 75, grid: { drawOnChartArea: false }, ticks: { font: { size: 12 } }, position: 'right' },
          },
        },
      }))
    }

    return () => { chartsRef.current.forEach(c => c.destroy()); chartsRef.current = [] }
  }, [data])

  if (error) return <ErrorMessage message={error} />
  if (!data) return <LoadingSpinner />

  const currentTsb = data.load.tsb?.length ? data.load.tsb[data.load.tsb.length - 1] : null

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
  // Round to total minutes first, then split — splitting before rounding can
  // bubble 59.5min up to "60m" or "1h 60m" (Copilot review #283).
  const totalMin = Math.round(seconds / 60)
  const h = Math.floor(totalMin / 60)
  const m = totalMin % 60
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
  // `undefined` = browser/runtime default locale. Respects the user's system
  // language instead of forcing en-US on Russian users (whose app is otherwise
  // localised via i18next).
  const opts: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' }
  return `${start.toLocaleDateString(undefined, opts)} – ${end.toLocaleDateString(undefined, opts)}`
}

function tsbZone(tsb: number, t: (k: string) => string): { label: string; color: string } {
  if (tsb > 10) return { label: t('dashboard.week.tsb_under'), color: TSB_ZONE_COLORS.under }
  if (tsb >= -10) return { label: t('dashboard.week.tsb_optimal'), color: TSB_ZONE_COLORS.optimal }
  if (tsb >= -25) return { label: t('dashboard.week.tsb_productive'), color: TSB_ZONE_COLORS.productive }
  return { label: t('dashboard.week.tsb_risk'), color: TSB_ZONE_COLORS.risk }
}

function WeekLoadCard({ week }: { week: WeeklyRecapBucket }) {
  const { t } = useTranslation()
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
        ? TSB_ZONE_COLORS.optimal
        : ctl_delta < -0.5
          ? TSB_ZONE_COLORS.risk
          : 'var(--text-dim)'
  const zone = tsb_end !== null ? tsbZone(tsb_end, t) : null
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
  const { t } = useTranslation()
  const sports = WEEK_SPORT_ORDER.filter(s => week.by_sport[s])
  const totalTss = sports.reduce((a, s) => a + (week.by_sport[s]?.tss || 0), 0)
  const totalSec = sports.reduce((a, s) => a + (week.by_sport[s]?.duration_sec || 0), 0)

  return (
    <div className="bg-[var(--surface)] rounded-xl p-3 mb-3">
      <div className="flex justify-between items-baseline mb-1">
        <div className="text-sm font-bold">
          {formatWeekRange(week.week_start, week.week_end)}
          {isCurrent && <span className="ml-2 text-[10px] uppercase font-semibold text-text-dim">{t('dashboard.week.this_week')}</span>}
        </div>
        <div className="text-[13px] font-semibold tabular-nums">
          {totalSec > 0 && <span className="text-text-dim font-normal mr-2">{formatHm(totalSec)}</span>}
          TSS {totalTss.toFixed(0)}
        </div>
      </div>
      {sports.length === 0 ? (
        <div className="text-[13px] text-text-dim py-1">{t('dashboard.week.no_activities')}</div>
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
  const { t } = useTranslation()
  const [recap, setRecap] = useState<WeeklyRecapResponse | null>(null)
  const [offset, setOffset] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
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
  // While a fetch is in-flight we keep the previous window visible (no
  // spinner-flash) but lock both buttons — otherwise a double-click could
  // bump offset twice and `canPrev` (derived from the now-stale recap) would
  // briefly disagree with the new offset.
  const canPrev = recap.has_prev && offset > -52 && !loading
  const canNext = offset < 0 && !loading
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
          {t('dashboard.week.earlier')}
        </button>
        <span className="text-[12px] text-text-dim">{loading ? '…' : range}</span>
        <button
          onClick={() => canNext && setOffset(o => o + 4)}
          disabled={!canNext}
          className="px-3 py-1.5 rounded-lg bg-[var(--surface)] text-[13px] font-semibold disabled:opacity-30 disabled:cursor-not-allowed border-none cursor-pointer"
        >
          {t('dashboard.week.later')}
        </button>
      </div>

      {recap.weeks.length === 0 ? (
        <div className="text-center py-6 text-text-dim text-sm">{t('dashboard.week.no_activities_window')}</div>
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
      <div style={{ height: 280 }}>{children}</div>
    </div>
  )
}

function chartOptions(title: string): Record<string, unknown> {
  return {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: { position: 'top', labels: { boxWidth: 12, padding: 10, font: { size: 13 } } },
      title: { display: true, text: title, font: { size: 14, weight: 'bold' } },
    },
    scales: {
      x: { grid: { color: 'rgba(128,128,128,0.2)' }, ticks: { font: { size: 12 }, maxRotation: 45, autoSkip: true, maxTicksLimit: 10 } },
      y: { grid: { color: 'rgba(128,128,128,0.2)' }, ticks: { font: { size: 12 } } },
    },
  }
}
