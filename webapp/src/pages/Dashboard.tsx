import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'
import Layout from '../components/Layout'
import { TopBar, SegmentedTabs, Gauge, TaperBar } from '../components/halo'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import { apiFetch } from '../api/client'
import { useAuth } from '../auth/useAuth'
import { CHART_COLORS } from '../lib/constants'
import type {
  AuthMeResponse,
  GoalResponse,
  GoalProgress,
  GoalProjection,
  WeeklyReportListItem,
  WeeklyReportListResponse,
} from '../api/types'
import RacePlanPanel from '../components/RacePlanPanel'
import LoadTab from './DashboardLoadTab'

// Trends screen tabs. Labels are literal English (de-i18n'd, matching the
// design's `BDashboard` tabs). The Load tab merges the former /progress
// screen — Endurance Score placeholder, per-sport trend cards (Decoupling,
// Zone Distribution, EF/Drift, Bike Readiness / Marathon Shape, swim pace).
type TabKey = 'goal' | 'load' | 'recap'
const TAB_LABEL: Record<TabKey, string> = { goal: 'Goal', load: 'Load', recap: 'Recap' }

export default function Dashboard() {
  const { t } = useTranslation()
  const { isDemo } = useAuth()
  const [searchParams, setSearchParams] = useSearchParams()
  // null = unknown (still loading OR /api/auth/me failed). We optimistically
  // keep the Goal tab in that case so the common path (race set) doesn't
  // flicker, and a flaky network doesn't strand the user without a Goal tab
  // they actually own. If GoalTab itself can't fetch, it renders
  // <ErrorMessage/> with a retry path. Only an explicit `false` from a
  // successful response drops the Goal tab.
  const [hasGoal, setHasGoal] = useState<boolean | null>(null)

  useEffect(() => {
    apiFetch<AuthMeResponse>('/api/auth/me')
      .then(data => setHasGoal(!!data.goal))
      .catch(() => setHasGoal(null))
  }, [])

  // Recap is the weekly-reports archive — own-history-only (/api/weekly-reports
  // is require_athlete, since reports can mention injuries / personal context).
  // A demo user would only hit a 403, so drop the tab for them entirely rather
  // than render a broken error state.
  const baseTabs: TabKey[] = hasGoal === false ? ['load', 'recap'] : ['goal', 'load', 'recap']
  const tabKeys = isDemo ? baseTabs.filter(k => k !== 'recap') : baseTabs
  const tabs = tabKeys.map(k => ({ key: k, label: TAB_LABEL[k] }))

  // Active tab lives in the URL (?tab=) so a detail page — e.g. a weekly
  // report — can deep-link straight back to the Recap tab, and browser-back
  // restores it. Clamp to an actually-available tab: guards a hand-typed
  // ?tab=recap for a demo user, or ?tab=goal once the goal is gone.
  const tabParam = searchParams.get('tab')
  const requestedTab: TabKey =
    tabParam === 'load' || tabParam === 'recap' || tabParam === 'goal' ? tabParam : 'goal'
  const activeTab: TabKey = tabKeys.includes(requestedTab) ? requestedTab : tabKeys[0]
  const setActiveTab = (key: TabKey) => setSearchParams({ tab: key }, { replace: true })

  return (
    <Layout maxWidth="480px">
      <div className="-mx-4 -mt-4 md:-mb-8 min-h-screen bg-halo-bg px-4 md:px-9 font-sans text-halo-ink">
        {/* Header matches the bottom-tab name ("Trends"); the Recap tab is the
            weekly-reports archive, so it gets its own «Weekly reports» title. */}
        <TopBar
          title={activeTab === 'recap' ? t('weekly.title') : t('nav.trends')}
          subtitle={t('dashboard.desktop_subtitle')}
        />
        <div className="sticky top-0 z-10 bg-halo-bg py-3 md:static">
          <SegmentedTabs<TabKey> tabs={tabs} active={activeTab} onChange={setActiveTab} />
        </div>

        <div className="flex flex-col gap-3.5 pb-4">
          {activeTab === 'goal' && <GoalTab />}
          {activeTab === 'load' && <LoadTab />}
          {activeTab === 'recap' && <RecapTab />}
        </div>
      </div>
    </Layout>
  )
}

// Sport key → display label + color. `CHART_COLORS` keeps the Goal-tab
// per-sport bars on the app-wide sport palette.
const SPORT_META: Record<'swim' | 'ride' | 'run', { label: string; color: string }> = {
  swim: { label: 'Swim', color: CHART_COLORS.swim },
  ride: { label: 'Ride', color: CHART_COLORS.ride },
  run: { label: 'Run', color: CHART_COLORS.run },
}

// Short "Jul 5" date for projection captions. UTC parse+format — a local-TZ
// parse shifts the day by ±1 across DST for a UTC-positive user.
function fmtProjDate(iso: string): string {
  return new Date(iso + 'T00:00:00Z').toLocaleDateString(undefined, {
    month: 'short',
    day: 'numeric',
    timeZone: 'UTC',
  })
}

// Whole weeks the projected target-hit date lands past race day (clamped ≥ 0).
function weeksPastRace(projectedDate: string, eventDate: string): number {
  const proj = new Date(projectedDate + 'T00:00:00Z')
  const ev = new Date(eventDate + 'T00:00:00Z')
  return Math.max(0, Math.round((proj.getTime() - ev.getTime()) / (7 * 86400e3)))
}

// Footer warning string for an off-track projection. Only called when
// ``on_track === false``; the projected_date branch carries the concrete
// delta, the null branches surface why we couldn't project.
function formatProjectionWarning(
  label: string,
  p: GoalProjection,
  target: number | null,
  eventDate: string,
): string {
  if (!p.projected_date) {
    if (p.reason === 'declining') return `${label}: CTL is declining — target not reachable at current rate`
    if (p.reason === 'flat') return `${label}: CTL is flat — target not reachable at current rate`
    return `${label}: not enough data to project`
  }
  const tgt = target !== null ? Math.round(target) : '?'
  return `${label}: at current pace you'll hit ${tgt} on ${fmtProjDate(p.projected_date)} — ${weeksPastRace(
    p.projected_date,
    eventDate,
  )}w late`
}

// Per-row projection caption + status pill — Goal-tab "Progress · projection"
// card (prototype `BDashboard`). The backend (`project_ctl_target`) already
// computed the ramp / projected date / on_track verdict — we only format it.
// English-only chrome ("on plan", "proj.", "Nw late") — coaching shorthand,
// consistent with `formatProjectionWarning` (also literal English).
type ProjTone = 'on_plan' | 'late' | 'risk'
const PROJ_TONE_COLOR: Record<ProjTone, string> = {
  on_plan: 'var(--color-status-green)',
  late: 'var(--color-amber)',
  risk: 'var(--color-coral)',
}

function projectionInfo(
  p: GoalProjection | null,
  eventDate: string,
): { ramp: string | null; tail: string | null; pill: { label: string; tone: ProjTone } | null } {
  if (!p) return { ramp: null, tail: null, pill: null }
  const ramp = p.ramp_per_week != null ? `${p.ramp_per_week >= 0 ? '+' : ''}${p.ramp_per_week.toFixed(1)}/wk` : null
  if (p.reason === 'already_at_target') return { ramp, tail: 'target reached', pill: null }
  if (p.reason === 'declining' || p.reason === 'flat') {
    return { ramp, tail: 'stalled — no progress this cycle', pill: null }
  }
  if (!p.projected_date) return { ramp, tail: null, pill: null } // insufficient_data
  const tail = `proj. ${fmtProjDate(p.projected_date)}`
  if (p.on_track !== false) return { ramp, tail, pill: { label: 'on plan', tone: 'on_plan' } }
  const wk = weeksPastRace(p.projected_date, eventDate)
  return { ramp, tail, pill: { label: `${wk}w late`, tone: wk <= 2 ? 'late' : 'risk' } }
}

// One row of the projection card — name + current/target + taper-aware bar +
// the ramp/projection caption and status pill.
function ProjectionRow({
  name,
  current,
  target,
  color,
  projection,
  eventDate,
  overall,
}: {
  name: string
  current: number | null
  target: number
  color: string
  projection: GoalProjection | null
  eventDate: string
  overall?: boolean
}) {
  const { t } = useTranslation()
  const over = current != null && current > target
  const info = projectionInfo(projection, eventDate)
  const caption = [info.ramp, info.tail].filter(Boolean).join(' · ')
  return (
    <div>
      <div className="flex items-baseline justify-between gap-2">
        <span className={`text-[13px] ${overall ? 'font-bold tracking-[-0.1px]' : 'font-semibold'}`}>{name}</span>
        <span className="whitespace-nowrap text-[13px] font-medium text-halo-ink-dim">
          {current != null ? current.toFixed(1) : '—'} / {Math.round(target)}
          {over && (
            <span
              className="ml-1.5 rounded-pill px-1.5 py-0.5 text-[10px] font-bold uppercase tracking-[0.4px]"
              style={{ background: 'color-mix(in srgb, var(--color-amber) 22%, transparent)', color: 'var(--color-amber)' }}
            >
              {t('dashboard.taper')}
            </span>
          )}
        </span>
      </div>
      <TaperBar current={current ?? 0} target={target} color={color} height={overall ? 10 : 8} />
      {(caption || info.pill) && (
        <div className="mt-1.5 flex items-baseline justify-between gap-2">
          <span className="text-[11px] font-medium text-halo-ink-dim">{caption}</span>
          {info.pill && (
            <span
              className="shrink-0 rounded-pill px-[7px] py-0.5 text-[10px] font-bold uppercase tracking-[0.4px]"
              style={{
                background: `color-mix(in srgb, ${PROJ_TONE_COLOR[info.pill.tone]} 14%, transparent)`,
                color: PROJ_TONE_COLOR[info.pill.tone],
              }}
            >
              {info.pill.label}
            </span>
          )}
        </div>
      )}
    </div>
  )
}

function GoalTab() {
  const { t } = useTranslation()
  const [goalsResponse, setGoalsResponse] = useState<GoalResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    apiFetch<GoalResponse>('/api/goal')
      .then(setGoalsResponse)
      .catch(err => setError(err instanceof Error ? err.message : 'Failed to load'))
      .finally(() => setLoading(false))
  }, [])

  if (loading) return <LoadingSpinner />
  if (error) return <ErrorMessage message={error} />
  // The Dashboard shell already hides the Goal tab when the athlete has no
  // race, so we should never actually hit `has_goals: false` here. Keep a
  // lightweight fallback in case the two fetches race (Dashboard reads
  // /api/auth/me, GoalTab reads /api/goal — the goal could be deleted
  // between the two).
  if (!goalsResponse || !goalsResponse.has_goals) {
    return <div className="py-6 text-center text-halo-ink-dim">{t('dashboard.no_race')}</div>
  }

  return (
    <>
      {goalsResponse.goals.map(g => (
        <GoalCard key={g.id} goal={g} />
      ))}
    </>
  )
}

function GoalCard({ goal: g }: { goal: GoalProgress }) {
  const { t, i18n } = useTranslation()
  // Collect off-track warnings once so the JSX stays flat. Per-sport bars
  // only contribute when their target is set (block !== undefined).
  const warnings: string[] = []
  if (g.projection && g.projection.on_track === false) {
    warnings.push(formatProjectionWarning('Overall CTL', g.projection, g.ctl_target, g.event_date))
  }
  if (g.per_sport) {
    for (const sport of ['swim', 'ride', 'run'] as const) {
      const block = g.per_sport[sport]
      if (block?.projection && block.projection.on_track === false) {
        warnings.push(formatProjectionWarning(SPORT_META[sport].label, block.projection, block.ctl_target, g.event_date))
      }
    }
  }

  const pct = g.overall_pct == null ? null : Math.max(0, Math.min(100, g.overall_pct))
  const delta =
    g.ctl_target != null && g.ctl_current != null ? g.ctl_target - g.ctl_current : null
  const dateStr = new Intl.DateTimeFormat(i18n.language, { weekday: 'short', month: 'short', day: 'numeric' }).format(
    new Date(g.event_date + 'T00:00:00'),
  )

  return (
    <>
      {/* Mobile: stacked (prototype `BGoal`). Desktop (`BdDashboard`):
          goal hero + by-sport sit side by side (1fr / 1fr); warnings and
          the race-plan panel stay full width below. */}
      <div className="contents md:grid md:grid-cols-2 md:items-start md:gap-[18px]">
      {/* Goal hero — arc + CTL sidebar */}
      <div className="rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card">
        <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
          {t('dashboard.race_a')}
        </div>
        <div className="mt-1 text-lg font-semibold tracking-[-0.3px] md:text-[20px]">{g.event_name}</div>
        <div className="mt-0.5 text-[13px] text-halo-ink-dim">
          {dateStr} · {t('dashboard.weeks_out', { count: g.weeks_remaining })}
        </div>
        <div className="mt-3.5 flex items-center gap-4 md:gap-6">
          {/* Mobile prototype `BDashboard`: 180×150 arc. Desktop (`BdDashboard`
              line 720): 220×200 arc with bigger numeral, fills the wider card.
              Both wrappers `aria-hidden` — the percentage is decorative here;
              meaningful CTL data lives in the adjacent sidebar text. */}
          <div className="md:hidden" aria-hidden="true">
            <Gauge
              width={180}
              height={150}
              cx={90}
              cy={90}
              r={72}
              strokeWidth={14}
              value={pct}
              color="var(--color-brand)"
              trackColor="var(--color-brand-light)"
              center={(cx, cy) => (
                <>
                  <text x={cx} y={cy} textAnchor="middle" fontSize="44" fontWeight="600" fill="var(--color-ink)" letterSpacing="-1.5">
                    {pct == null ? '—' : pct}
                    {pct != null && <tspan fontSize="20" fill="var(--color-ink-dim)">%</tspan>}
                  </text>
                  <text x={cx} y={cy + 22} textAnchor="middle" fontSize="11" fill="var(--color-ink-dim)" style={{ textTransform: 'uppercase' }}>
                    {t('dashboard.to_target')}
                  </text>
                </>
              )}
            />
          </div>
          <div className="hidden md:block" aria-hidden="true">
            <Gauge
              width={220}
              height={200}
              cx={110}
              cy={110}
              r={88}
              strokeWidth={16}
              value={pct}
              color="var(--color-brand)"
              trackColor="var(--color-brand-light)"
              center={(cx, cy) => (
                <>
                  <text x={cx} y={cy} textAnchor="middle" fontSize="52" fontWeight="600" fill="var(--color-ink)" letterSpacing="-2">
                    {pct == null ? '—' : pct}
                    {pct != null && <tspan fontSize="24" fill="var(--color-ink-dim)">%</tspan>}
                  </text>
                  <text x={cx} y={cy + 26} textAnchor="middle" fontSize="11" fill="var(--color-ink-dim)" style={{ textTransform: 'uppercase' }}>
                    {t('dashboard.to_target')}
                  </text>
                </>
              )}
            />
          </div>
          <div className="flex-1">
            <div className="text-[11px] font-semibold uppercase tracking-[0.4px] text-halo-ink-dim">
              {t('dashboard.fitness_ctl')}
            </div>
            <div className="mt-0.5 flex items-baseline gap-1.5 md:mt-1 md:gap-2">
              <span className="text-[32px] font-semibold tracking-[-1px] md:text-[38px]">
                {g.ctl_current != null ? g.ctl_current.toFixed(0) : '—'}
              </span>
              <span className="text-sm text-halo-ink-dim md:text-base">/ {g.ctl_target != null ? Math.round(g.ctl_target) : '—'}</span>
            </div>
            {delta != null && delta > 0 && (
              <div className="mt-1 text-xs font-semibold text-halo-brand-dark">
                {t('dashboard.ctl_needed', { delta: delta.toFixed(1), weeks: g.weeks_remaining })}
              </div>
            )}
          </div>
        </div>
      </div>

      {/* Progress · projection — per-discipline ramp + projected target-hit
          date vs race day. The backend supplies ramp_per_week + projected_date
          + on_track per sport and overall; off-track rows collect into the
          footer alert. */}
      {g.per_sport && (
        <div className="rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card">
          <div className="flex items-center justify-between">
            <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
              Progress · projection
            </div>
            <span className="text-[11px] font-medium text-halo-ink-dim">race in {g.days_remaining}d</span>
          </div>
          <div className="mt-3 flex flex-col gap-3.5">
            {g.ctl_target != null && (
              <>
                <ProjectionRow
                  name="Overall CTL"
                  current={g.ctl_current}
                  target={g.ctl_target}
                  color="var(--color-brand)"
                  projection={g.projection}
                  eventDate={g.event_date}
                  overall
                />
                <div className="h-px bg-halo-border" />
              </>
            )}
            {(['swim', 'ride', 'run'] as const).map(sport => {
              const block = g.per_sport?.[sport]
              if (!block) return null
              const meta = SPORT_META[sport]
              return (
                <ProjectionRow
                  key={sport}
                  name={meta.label}
                  current={block.ctl_current}
                  target={block.ctl_target}
                  color={meta.color}
                  projection={block.projection}
                  eventDate={g.event_date}
                />
              )
            })}
          </div>
          {warnings.length > 0 && (
            <div className="mt-3.5 flex flex-col gap-1.5 border-t border-halo-border pt-3" role="status">
              {warnings.map(w => {
                const ci = w.indexOf(':')
                return (
                  <div key={w} className="flex items-start gap-2 text-[12px] leading-snug text-halo-ink">
                    <span
                      aria-hidden="true"
                      className="mt-px flex h-4 w-4 shrink-0 items-center justify-center rounded-full text-[10px] font-bold"
                      style={{ background: 'color-mix(in srgb, var(--color-coral) 14%, transparent)', color: 'var(--color-coral)' }}
                    >
                      !
                    </span>
                    <span>
                      <span className="sr-only">Warning: </span>
                      <strong className="font-bold">{w.slice(0, ci + 1)}</strong>
                      {w.slice(ci + 1)}
                    </span>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      )}
      </div>

      <RacePlanPanel goalId={g.id} daysRemaining={g.days_remaining} />
    </>
  )
}

// ---------------------------------------------------------------------------
// Recap tab — weekly reports
//
// Port of the prototype `BDashboard` recap tab (direction-b-halo.jsx:1959+):
// each card is a real AI weekly report — the leading `# ` headline, the week's
// training volume, and the CTL/ramp/TSB it bought — and taps through to the
// full markdown at /weekly/:week_start. Driven by /api/weekly-reports (not the
// retired /api/weekly-recap activity buckets) so every card has a headline.
// ---------------------------------------------------------------------------

// Design's top-bar reads «8 weeks» — match it as the first-page size. Older
// weeks page in via the cursor («Load earlier weeks»).
const RECAP_PAGE_SIZE = 8

// Backend `by_sport` keys (swimming/cycling/running — see _SPORT_BUCKET in
// api/routers/weekly_reports.py) → card label + dot color.
const RECAP_SPORT_META: Record<string, { label: string; color: string }> = {
  swimming: { label: 'Swim', color: CHART_COLORS.swim },
  cycling: { label: 'Ride', color: CHART_COLORS.ride },
  running: { label: 'Run', color: CHART_COLORS.run },
}
const RECAP_SPORT_ORDER = ['swimming', 'cycling', 'running']

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

// ISO Monday → "May 11 – 17" Mon-Sun range. Reports carry only the Monday, so
// the Sunday is synthesised locally. Parse + format in UTC — a local-TZ parse
// shifts a UTC-positive user's Monday into the preceding Sunday at format time
// (same TZ-shift bug fixed in WeeklyReports.tsx / shiftIsoDate).
function formatRecapWeekRange(isoMonday: string, locale: string): string {
  const monday = new Date(`${isoMonday}T00:00:00Z`)
  const sunday = new Date(monday)
  sunday.setUTCDate(monday.getUTCDate() + 6)
  const opts: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric', timeZone: 'UTC' }
  return `${monday.toLocaleDateString(locale, opts)} – ${sunday.toLocaleDateString(locale, opts)}`
}

// Current week's Monday for the "This week" pill. Derived from the viewer's
// LOCAL clock — "this week" is the viewer's notion of now. (formatRecapWeekRange
// parses the backend's fixed ISO Monday in UTC purely for TZ-stable display;
// here we need today, so local components are read consistently — mixing
// local `new Date()` with `getUTC*` would shift the pill near midnight.)
function currentMondayIso(): string {
  const now = new Date()
  const dow = (now.getDay() + 6) % 7 // 0 = Monday
  const monday = new Date(now.getFullYear(), now.getMonth(), now.getDate() - dow)
  const y = monday.getFullYear()
  const m = String(monday.getMonth() + 1).padStart(2, '0')
  const d = String(monday.getDate()).padStart(2, '0')
  return `${y}-${m}-${d}`
}

function RecapCard({ item, isCurrent }: { item: WeeklyReportListItem; isCurrent: boolean }) {
  const { t, i18n } = useTranslation()
  const sports = RECAP_SPORT_ORDER.filter(s => item.by_sport[s])
  const totalSec = sports.reduce((a, s) => a + (item.by_sport[s]?.duration_sec || 0), 0)
  const totalTss = sports.reduce((a, s) => a + (item.by_sport[s]?.tss || 0), 0)
  // Legacy reports (pre-headline-prompt) have headline === null — fall back to
  // the prose preview so the card always has a title line.
  const headline = item.headline || item.preview
  const { ctl_start, ctl_end, ctl_delta, ramp, tsb_end } = item

  const deltaStr = ctl_delta == null ? '' : `${ctl_delta > 0 ? '+' : ''}${ctl_delta.toFixed(1)}`
  const deltaColor =
    ctl_delta == null || (ctl_delta > -0.5 && ctl_delta < 0.5)
      ? 'var(--color-ink-dim)'
      : ctl_delta > 0
        ? 'var(--color-status-green)'
        : 'var(--color-amber)'
  // Ramp tone — amber only above the project's >7/wk ramp-rate warning
  // threshold (CLAUDE.md business rules); the number itself always shows.
  const rampColor = ramp != null && ramp > 7 ? 'var(--color-amber)' : 'var(--color-ink)'
  const rampStr = ramp == null ? null : `${ramp > 0 ? '+' : ''}${ramp.toFixed(1)}`

  return (
    <Link
      to={`/weekly/${item.week_start}`}
      className="block rounded-card border border-halo-border bg-halo-surface p-4 no-underline text-halo-ink shadow-card transition-colors hover:bg-halo-surface-2"
    >
      <div className="flex items-center justify-between">
        <div className="text-[12px] font-bold uppercase tracking-[0.6px] text-halo-ink-dim">
          {formatRecapWeekRange(item.week_start, i18n.language)}
        </div>
        {isCurrent && (
          <span className="rounded-pill bg-halo-brand px-2 py-[3px] text-[10px] font-bold uppercase tracking-[0.6px] text-white">
            {t('weekly.this_week')}
          </span>
        )}
      </div>

      <div className="mt-2 line-clamp-2 text-[15px] font-semibold leading-[1.35] tracking-[-0.2px]">
        {headline}
      </div>

      {/* Totals — total time + TSS pulled up so the eye sees "how much work?"
          before the per-sport columns. Hidden for a week with no volume. */}
      {totalSec > 0 && (
        <div className="mt-3 flex items-baseline gap-2 border-t border-halo-border pt-3">
          <span className="text-lg font-semibold tracking-[-0.4px]">{formatHm(totalSec)}</span>
          <span className="text-[12px] font-medium text-halo-ink-dim">· {totalTss.toFixed(0)} TSS</span>
        </div>
      )}

      {sports.length > 0 && (
        <div className="mt-2.5 flex flex-col gap-1.5">
          {sports.map(s => {
            const b = item.by_sport[s]
            const meta = RECAP_SPORT_META[s]
            return (
              <div key={s} className="flex items-center gap-2.5 text-[13px] tabular-nums">
                <span className="h-[7px] w-[7px] shrink-0 rounded-full" style={{ background: meta.color }} />
                <span className="w-11 font-semibold">{meta.label}</span>
                <span className="w-16 text-halo-ink-dim">{formatHm(b.duration_sec)}</span>
                <span className="flex-1 text-halo-ink-dim">{formatKm(b.distance_m)}</span>
                <span className="min-w-[32px] text-right font-bold">{b.tss.toFixed(0)}</span>
              </div>
            )
          })}
        </div>
      )}

      {/* Footer — CTL transition, ramp, TSB. CTL change gets the emphasis
          ("what did this week buy me?"); ramp & TSB stay muted captions. */}
      <div className="mt-3 flex flex-wrap items-center gap-x-3 gap-y-1 border-t border-halo-border pt-2.5">
        {ctl_start != null && ctl_end != null && (
          <div className="text-[12px] font-medium text-halo-ink-dim">
            CTL <span className="font-semibold text-halo-ink">{ctl_start.toFixed(0)}</span>
            <span className="mx-1 text-halo-ink-dim">→</span>
            <span className="font-semibold text-halo-ink">{ctl_end.toFixed(0)}</span>
            {ctl_delta != null && (
              <span className="ml-1 font-semibold" style={{ color: deltaColor }}>
                ({deltaStr})
              </span>
            )}
          </div>
        )}
        {rampStr != null && (
          <div className="text-[11px] font-medium text-halo-ink-dim">
            ramp{' '}
            <span className="font-bold" style={{ color: rampColor }}>
              {rampStr}
            </span>
          </div>
        )}
        {tsb_end != null && (
          <div className="text-[11px] font-medium text-halo-ink-dim">
            TSB{' '}
            <span className="font-bold text-halo-ink">
              {tsb_end > 0 ? '+' : ''}
              {tsb_end.toFixed(0)}
            </span>
          </div>
        )}
        <span className="ml-auto text-base leading-none text-halo-ink-dim">›</span>
      </div>
    </Link>
  )
}

function RecapTab() {
  const { t } = useTranslation()
  const [items, setItems] = useState<WeeklyReportListItem[]>([])
  const [nextBefore, setNextBefore] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchPage = (before: string | null) => {
    const params = new URLSearchParams({ limit: String(RECAP_PAGE_SIZE) })
    if (before) params.set('before', before)
    return apiFetch<WeeklyReportListResponse>(`/api/weekly-reports?${params}`)
  }

  useEffect(() => {
    let cancelled = false
    fetchPage(null)
      .then(resp => {
        if (cancelled) return
        setItems(resp.items)
        setNextBefore(resp.next_before)
      })
      .catch(err => {
        if (!cancelled) setError(err instanceof Error ? err.message : t('weekly.error_load'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadMore = async () => {
    if (!nextBefore || loadingMore) return
    setLoadingMore(true)
    setError(null)
    try {
      const resp = await fetchPage(nextBefore)
      // Append — paginating older history into the tail of the list.
      setItems(prev => [...prev, ...resp.items])
      setNextBefore(resp.next_before)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('weekly.error_load'))
    } finally {
      setLoadingMore(false)
    }
  }

  if (loading) return <LoadingSpinner />
  if (error && items.length === 0) return <ErrorMessage message={error} />

  const thisMonday = currentMondayIso()

  return (
    <>
      {items.length === 0 ? (
        <div className="py-6 text-center text-sm text-halo-ink-dim">{t('weekly.empty')}</div>
      ) : (
        /* Desktop: weekly report cards tile into a 2-col grid. */
        <div className="contents md:grid md:grid-cols-2 md:items-start md:gap-[18px]">
          {items.map(item => (
            <RecapCard key={item.week_start} item={item} isCurrent={item.week_start === thisMonday} />
          ))}
        </div>
      )}

      {nextBefore && (
        <button
          type="button"
          onClick={loadMore}
          disabled={loadingMore}
          className="w-full rounded-card border border-halo-border bg-halo-surface py-3 text-sm font-semibold text-halo-ink-dim transition-colors hover:bg-halo-surface-2 disabled:cursor-not-allowed disabled:opacity-60"
        >
          {loadingMore ? t('weekly.loading_more') : t('weekly.load_more')}
        </button>
      )}

      {/* «Load more» failure shows inline so the loaded cards stay readable. */}
      {error && items.length > 0 && <ErrorMessage message={error} />}
    </>
  )
}
