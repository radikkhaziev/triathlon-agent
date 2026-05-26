import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import DayCard, { type WeekDay } from '../components/DayCard'
import { useWeekNav } from '../hooks/useWeekNav'
import { useApi } from '../hooks/useApi'
import type {
  ScheduledWorkoutsResponse,
  ActivitiesWeekResponse,
  ActivityItem,
} from '../api/types'

/**
 * Week tab — port of `BWeek` from `design-package/endurai/direction-b-halo.jsx`
 * (lines 1351-1530) + `BWeekSummary` (lines 1320-1351).
 *
 * Layout per design:
 *   • Header row — «X done · Y to go» summary + prev/next chevrons.
 *   • Single-column day cards (rendered by `DayCard` from `../components/DayCard`,
 *     shared with the Today page). Each session inside a day is its own
 *     mini-card: white surface for completed actuals, cobalt blue for
 *     planned-not-yet-done, coral-tinted for past-missed. The outer day
 *     frame stays neutral — «today» is marked by the date column rendering
 *     in the brand accent rather than inverting the whole card.
 *   • Plan vs Actual summary card at the bottom — Time / TSS / Sessions, each
 *     row with a % readout and a bar with a 100% tick (over/under reads
 *     against the plan target).
 */

function buildWeek(plan: ScheduledWorkoutsResponse, acts: ActivitiesWeekResponse): WeekDay[] {
  const today = plan.today
  const actByDate = new Map<string, ActivityItem[]>()
  for (const d of acts.days) actByDate.set(d.date, d.activities)
  return plan.days.map(d => ({
    date: d.date,
    weekday: d.weekday,
    state: d.date === today ? 'today' : d.date < today ? 'past' : 'future',
    planned: d.workouts,
    actuals: actByDate.get(d.date) ?? [],
  }))
}

export default function MergedWeek() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const { offset, prev, next } = useWeekNav()
  const plan = useApi<ScheduledWorkoutsResponse>(`/api/scheduled-workouts?week_offset=${offset}`)
  const acts = useApi<ActivitiesWeekResponse>(`/api/activities-week?week_offset=${offset}`)

  const loading = plan.loading || acts.loading
  const error = plan.error || acts.error
  const ready = !loading && !error && plan.data && acts.data

  const week = ready ? buildWeek(plan.data!, acts.data!) : []
  const today = plan.data?.today ?? ''

  // Header summary: «X done · Y to go».
  //   done   = activities that count as a real session (have a sport type)
  //   to go  = planned workouts still in the future or today-not-yet-done
  // Past missed days are not counted as «to go» (the week is over for them).
  const doneCount = week.reduce(
    (s, d) => s + (today && d.date <= today ? d.actuals.filter(a => !!a.type).length : 0),
    0,
  )
  // Per-session pairing — a planned workout «covered» by an actual via
  // Intervals' pairing is already in `doneCount`, so it shouldn't double-count
  // here. Track unpaired planned per day instead of the old
  // `actuals.length === 0` blunt check (which lost partial-day remainders:
  // 2 planned + 1 done → previously contributed 0, now contributes 1).
  const toGoCount = week.reduce(
    (s, d) => {
      if (d.state === 'future') return s + d.planned.length
      if (d.state === 'today') {
        const pairedIds = new Set(
          d.actuals.map(a => a.paired_event_id).filter((v): v is number => v != null),
        )
        return s + d.planned.filter(w => !pairedIds.has(w.id)).length
      }
      return s
    },
    0,
  )

  // Permissive prev gate — either feed having more history backward enables
  // it (a week with plan-only or actuals-only is still meaningful).
  // Next uses plan only (activities have no future by definition).
  const canPrev = !!(plan.data && acts.data) && (plan.data.has_prev || acts.data.has_prev)
  const canNext = !!plan.data && plan.data.has_next

  return (
    <>
      <div className="flex items-center justify-between pb-3.5">
        <span className="text-[13px] text-halo-ink-dim">
          {ready
            ? <>
                <span className="font-semibold text-halo-ink">{t('merged.done_count', { count: doneCount })}</span>
                {' · '}{t('merged.to_go_count', { count: toGoCount })}
              </>
            : ' '}
        </span>
        <div className="flex gap-1.5">
          <button
            type="button"
            onClick={prev}
            disabled={!canPrev}
            aria-label={t('merged.prev_week')}
            className="flex h-[26px] w-[26px] items-center justify-center rounded-pill border border-halo-border bg-halo-surface text-halo-ink-dim disabled:opacity-40"
          >
            <span aria-hidden="true">‹</span>
          </button>
          <button
            type="button"
            onClick={next}
            disabled={!canNext}
            aria-label={t('merged.next_week')}
            className="flex h-[26px] w-[26px] items-center justify-center rounded-pill border border-halo-border bg-halo-surface text-halo-ink-dim disabled:opacity-40"
          >
            <span aria-hidden="true">›</span>
          </button>
        </div>
      </div>

      {loading && <LoadingSpinner />}
      {error && <ErrorMessage message={t('plan.load_error')} />}

      {ready && (
        /* Mobile (prototype `BWeekMerged`): single column stack. Desktop
           (`BdWeek` direction-b-desktop.jsx:1166): 7-column day grid would
           cramp the existing DayCard internals — instead we go 2-up on md+ so
           DayCard layout stays unchanged but the canvas isn't half-empty. */
        <div className="flex flex-col gap-2.5 pb-4 md:grid md:grid-cols-2 md:gap-3">
          {week.map(d => <DayCard key={d.date} d={d} t={t} navigate={navigate} />)}
          <div className="md:col-span-2">
            <WeekSummary plan={plan.data!} acts={acts.data!} t={t} />
          </div>
        </div>
      )}
    </>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Plan vs Actual summary card — three rows (Time / TSS / Sessions), each
// with the actual value, the planned value, % completion, and a bar with a
// 100%-tick so over/under reads against the plan target.
// ─────────────────────────────────────────────────────────────────────────────
function fmtHM(min: number): string {
  const h = Math.floor(min / 60)
  const mm = Math.round(min % 60)
  return h ? `${h}h ${String(mm).padStart(2, '0')}m` : `${mm}m`
}

function fmtWeekRange(start: string, end: string): string {
  // ISO "2026-05-11" / "2026-05-17" → "May 11 – 17" (drop year, abbreviate
  // month). Same range string format as the design.
  const opts: Intl.DateTimeFormatOptions = { month: 'short', day: 'numeric' }
  const s = new Date(start + 'T00:00:00').toLocaleDateString('en-US', opts)
  const e = new Date(end + 'T00:00:00').toLocaleDateString('en-US', { day: 'numeric' })
  return `${s} – ${e}`
}

function WeekSummary({
  plan,
  acts,
  t,
}: {
  plan: ScheduledWorkoutsResponse
  acts: ActivitiesWeekResponse
  t: (k: string) => string
}) {
  // Planned aggregates — sum across every workout on every day.
  let planMin = 0
  let planTss = 0
  let planCount = 0
  for (const d of plan.days) {
    for (const w of d.workouts) {
      planMin += Math.round((w.duration_secs ?? 0) / 60)
      planTss += w.icu_training_load ?? 0
      planCount += 1
    }
  }
  // Actual aggregates — typeless activities (rare; an Intervals row with no
  // sport) don't count as a session.
  let actMin = 0
  let actTss = 0
  let actCount = 0
  for (const d of acts.days) {
    for (const a of d.activities) {
      if (!a.type) continue
      actMin += Math.round((a.moving_time ?? 0) / 60)
      actTss += a.icu_training_load ?? 0
      actCount += 1
    }
  }

  return (
    <div className="mt-1 rounded-card border border-halo-border bg-halo-surface px-4 py-3.5 shadow-card">
      <div className="flex items-baseline justify-between">
        <span className="text-[14px] font-semibold tracking-[-0.2px] text-halo-ink">
          {t('merged.plan_vs_actual')}
        </span>
        <span className="text-[11px] tracking-[0.4px] text-halo-ink-dim">
          {fmtWeekRange(plan.week_start, plan.week_end)}
        </span>
      </div>
      {/* Mobile: 3 stacked rows with top borders. Desktop (prototype `BdWeek`
          metric tiles row, direction-b-desktop.jsx:1127): three tiles in a row,
          borders flipped to left dividers so they read as tiles, not stacked. */}
      <div className="md:grid md:grid-cols-3 md:gap-0">
        <PlanVsActualRow label={t('merged.summary_time')} plan={fmtHM(planMin)} actual={fmtHM(actMin)} planRaw={planMin} actualRaw={actMin} unit="" desktopFirst />
        <PlanVsActualRow label={t('merged.summary_tss')} plan={String(Math.round(planTss))} actual={String(Math.round(actTss))} planRaw={planTss} actualRaw={actTss} unit="" />
        <PlanVsActualRow label={t('merged.summary_sessions')} plan={String(planCount)} actual={String(actCount)} planRaw={planCount} actualRaw={actCount} unit="" desktopLast />
      </div>
    </div>
  )
}

function PlanVsActualRow({
  label,
  plan,
  actual,
  planRaw,
  actualRaw,
  unit,
  desktopFirst = false,
  desktopLast = false,
}: {
  label: string
  plan: string
  actual: string
  planRaw: number
  actualRaw: number
  unit: string
  /** First tile in the desktop 3-col layout — strips its left padding+divider
   *  so the row's content edge aligns with the card title above. Mobile keeps
   *  the top border on every row including the first. */
  desktopFirst?: boolean
  /** Last tile — mirrors `desktopFirst` on the right side so the rightmost
   *  bar's edge aligns with the card's content edge (otherwise the leftmost
   *  bar would be ~16px wider than tiles 2/3). */
  desktopLast?: boolean
}) {
  const pct = planRaw ? Math.round((actualRaw / planRaw) * 100) : 0
  // Design tonal bands: 95–115% = green «on plan», 75–94% = amber «under»,
  // anything else = neutral dim. Asymmetric on purpose (overshoot is more
  // forgivable than undershoot).
  const tone =
    pct >= 95 && pct <= 115
      ? 'var(--color-status-green)'
      : pct >= 75
        ? 'var(--color-amber)'
        : 'var(--color-ink-dim)'
  const barPct = Math.min(120, pct) // bar maxes out at 120% so the 100% tick stays in frame
  // Mobile divider: top border on every row.
  // Desktop: no top border (md:border-t-0); inner tiles get a left divider +
  // both-sides padding; outer tiles strip the padding on the outer edge so
  // their bars align with the card content edge.
  const desktopPad = desktopFirst
    ? 'md:pr-4'
    : desktopLast
      ? 'md:border-l md:border-halo-border md:pl-4'
      : 'md:border-l md:border-halo-border md:px-4'
  return (
    <div className={`border-t border-halo-border py-2.5 md:border-t-0 md:py-3.5 ${desktopPad}`}>
      <div className="mb-1.5 flex items-baseline justify-between">
        <span className="text-[11px] font-bold uppercase tracking-[0.5px] text-halo-ink-dim">{label}</span>
        <span className="text-[11px] font-semibold tabular-nums" style={{ color: tone }}>{pct}%</span>
      </div>
      <div className="flex items-baseline justify-between">
        <span className="text-[18px] font-semibold tracking-[-0.3px] text-halo-ink">
          {actual}
          {unit && <span className="ml-0.5 text-[12px] font-medium text-halo-ink-dim">{unit}</span>}
        </span>
        <span className="text-[12px] text-halo-ink-dim">
          plan {plan}{unit ? ` ${unit}` : ''}
        </span>
      </div>
      {/* Bar with the 100% tick — design 1311 */}
      <div className="relative mt-2 h-[5px] overflow-hidden rounded-[3px] bg-halo-surface-2">
        <div
          className="absolute inset-y-0 left-0 rounded-[3px]"
          style={{ width: `${(barPct / 120) * 100}%`, background: tone }}
        />
        <div
          className="absolute inset-y-0 w-px bg-halo-ink/20"
          style={{ left: `${(100 / 120) * 100}%` }}
        />
      </div>
    </div>
  )
}
