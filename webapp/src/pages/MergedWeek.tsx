import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import { useWeekNav } from '../hooks/useWeekNav'
import { useApi } from '../hooks/useApi'
import { sportTone } from '../lib/constants'
import { stripWorkoutPrefix } from '../lib/formatters'
import type {
  ScheduledWorkoutsResponse,
  ScheduledWorkout,
  ActivitiesWeekResponse,
  ActivityItem,
} from '../api/types'

/**
 * Week tab — port of `BWeek` from `design-package/endurai/direction-b-halo.jsx`
 * (lines 1351-1530) + `BWeekSummary` (lines 1320-1351).
 *
 * Layout per design:
 *   • Header row — «X done · Y to go» summary + prev/next chevrons.
 *   • Single-column day cards — state-driven content (NOT a 2-col Plan|Done
 *     grid like the previous merge attempt):
 *       past + actual      → sport pill + duration; TSS · HR; extras stacked
 *                            with `+ Sport` prefix; race chip on the extra row
 *       past + missed      → coral «{name} · skipped» line
 *       past + no plan     → «—»
 *       today (not done)   → white-on-cobalt card; sport pill + name + desc +
 *                            duration · km (planned row); badge «in progress»
 *       today (done)       → white-on-cobalt card; same actual+extras layout
 *                            as past; badge «completed». Diverges from the
 *                            static prototype, which never modelled this case
 *                            (direction-b-halo.jsx:1408).
 *       future + planned   → regular card; sport pill + name + desc + dur · km
 *       rest day           → surface-2 card; italic «Recover well»
 *     Right chevron `›` appears on every tappable card.
 *   • Plan vs Actual summary card at the bottom — Time / TSS / Sessions, each
 *     row with a % readout and a bar with a 100% tick (over/under reads
 *     against the plan target).
 *
 * Tap targets per session row (NOT per day — the card itself isn't clickable):
 *   • actual session  → `/activity/:id` of that activity
 *   • planned session → `/workout/:id` of that planned workout
 *   • missed / rest   → static (no rows, just a label)
 *
 * Brick handling: a day with multiple sessions renders each one as its own
 * tappable row (main row + `+ Sport` extras row below a divider). Clicking
 * RUN vs +RIDE opens the correct detail page. The state badge appends a
 * count for multi-session days («Выполнено · 2 сессии», «По плану · 2
 * сессии»). Mirrors design direction-b-halo.jsx:1410-1509.
 */

type DayState = 'past' | 'today' | 'future'

interface WeekDay {
  date: string
  weekday: string
  state: DayState
  planned: ScheduledWorkout[]
  actuals: ActivityItem[]
}

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
  // Today's planned only counts toward «to go» if the workout isn't done yet
  // (otherwise it double-counts against `doneCount`).
  const toGoCount = week.reduce(
    (s, d) => {
      if (d.state === 'future') return s + d.planned.length
      if (d.state === 'today' && d.actuals.length === 0) return s + d.planned.length
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
// Day card — state-driven content. Each session row is its OWN tap target
// (RUN vs +RIDE on a brick day open different detail pages); the card frame
// itself isn't clickable. Multi-session days show «… · N сессий» on the
// badge. Design direction-b-halo.jsx:1454+ — same per-row pattern.
// ─────────────────────────────────────────────────────────────────────────────
interface DayCardProps {
  d: WeekDay
  t: (k: string, o?: Record<string, unknown>) => string
  navigate: (path: string) => void
}

function DayCard({ d, t, navigate }: DayCardProps) {
  const isToday = d.state === 'today'
  const isPast = d.state === 'past'
  const isFuture = d.state === 'future'
  const planned = d.planned[0] ?? null
  const main = d.actuals[0] ?? null
  const extras = d.actuals.slice(main ? 1 : 0)
  const restDay = d.planned.length === 0 && d.actuals.length === 0

  // Card surface — today inverted to cobalt, rest day on surface-2, missed
  // day with a faint coral tint. Everything else on plain surface.
  const missed = isPast && !main && d.planned.length > 0
  const cardCls = isToday
    ? 'bg-halo-brand text-white'
    : restDay
      ? 'bg-halo-surface-2 text-halo-ink border border-halo-border'
      : 'bg-halo-surface text-halo-ink border border-halo-border'
  const cardStyle = missed && !isToday
    ? { background: 'color-mix(in srgb, var(--color-coral) 8%, var(--color-surface))' }
    : undefined

  // Tonal helpers tied to today's inversion.
  const dim = isToday ? 'text-white/70' : 'text-halo-ink-dim'
  const dimmer = isToday ? 'text-white/55' : 'text-halo-ink-dimmer'
  const ink = isToday ? 'text-white' : 'text-halo-ink'
  const divider = isToday ? 'border-white/20' : 'border-halo-border'

  // Sessions to render as individual tap-target rows. Each row carries its
  // own navigation target — clicking RUN vs +RIDE on a brick day opens
  // different detail pages. Design direction-b-halo.jsx:1410+.
  //
  // Today / past with at least one actual: render the actuals AND any
  // planned sessions that weren't paired with an actual (covers the partial-
  // day case — planned SWIM not done + planned RUN done shows both rows).
  // Past + un-paired planned → coloured «skipped» so the user sees the miss;
  // today + un-paired planned → regular planned row, still tappable.
  //
  // Today / past with no actual: all planned render as planned rows. Future:
  // same. Empty otherwise (rest day renders its own static message below).
  type SessionRow = { key: string; path: string; sport: string | null; render: () => JSX.Element }
  const sessions: SessionRow[] = []
  if (main && (isPast || isToday)) {
    sessions.push({
      key: `a-${main.id}`,
      path: `/activity/${main.id}`,
      sport: main.type,
      render: () => <ActualRow a={main} ink={ink} dim={dim} isToday={isToday} t={t} />,
    })
    for (const a of extras) {
      sessions.push({
        key: `a-${a.id}`,
        path: `/activity/${a.id}`,
        sport: a.type,
        render: () => <ExtraActualRow a={a} ink={ink} dim={dim} t={t} isToday={isToday} />,
      })
    }
  }
  // Planned workouts that haven't been «covered» by an actual via Intervals'
  // pairing — render as their own rows so partially-done days don't drop the
  // outstanding plan from view.
  const pairedIds = new Set(d.actuals.map(a => a.paired_event_id).filter((v): v is number => v != null))
  for (const w of d.planned) {
    if (pairedIds.has(w.id)) continue
    if (isPast) {
      // Past + unpaired planned. If there's no main actual at all the day
      // already routes through the «missed» branch below; this loop only
      // contributes when main exists but Intervals couldn't pair THIS
      // particular planned to any actual (brick day variants).
      if (!main) continue
      sessions.push({
        key: `m-${w.id}`,
        path: `/workout/${w.id}`,
        sport: w.type,
        render: () => (
          <div className="text-[13px] font-medium text-halo-coral">
            {stripWorkoutPrefix(w.name)} · {t('merged.skipped')}
          </div>
        ),
      })
      continue
    }
    if (isToday || isFuture) {
      const idx = sessions.length
      sessions.push({
        key: `p-${w.id}`,
        path: `/workout/${w.id}`,
        sport: w.type,
        render: () =>
          idx === 0 ? (
            <PlannedRow w={w} ink={ink} dim={dim} isToday={isToday} />
          ) : (
            <ExtraPlannedRow w={w} ink={ink} dim={dim} isToday={isToday} />
          ),
      })
    }
  }

  // State badge — ALL CAPS, 10px, above the body. Multi-session days append
  // the count («Выполнено · 2 сессии», «По плану · 2 сессии»). Today flips
  // from «in progress» to «completed» once any actual exists.
  const sessionCount = sessions.length
  const multi = sessionCount > 1
  const completedTxt = multi
    ? `${t('merged.completed')} · ${t('merged.sessions_count', { count: sessionCount })}`
    : t('merged.completed')
  const plannedTxt = multi
    ? `${t('merged.planned_state')} · ${t('merged.sessions_count', { count: sessionCount })}`
    : t('merged.planned_state')
  const todayDone = isToday && main
  const badge = todayDone
    ? { text: completedTxt, cls: 'text-white/85' }
    : isToday
      ? { text: t('merged.in_progress'), cls: 'text-white/85' }
      : isPast && main
        ? { text: completedTxt, cls: 'text-halo-brand-dark' }
        : missed
          ? { text: t('merged.missed'), cls: 'text-halo-coral' }
          : isPast
            ? { text: '—', cls: dimmer }
            : restDay
              ? { text: t('merged.rest_day'), cls: dimmer }
              : { text: plannedTxt, cls: dim }

  // Per-row click — replaces the previous card-level onClick so brick days
  // (RUN + RIDE, SWIM + RUN) navigate to the right session instead of always
  // the first. Card itself is no longer a button.
  const onRow = (path: string) => () => navigate(path)
  const rowHoverCls = isToday ? 'hover:bg-white/8' : 'hover:bg-halo-surface-2'

  return (
    <div className={`rounded-card p-3.5 shadow-card transition-colors ${cardCls}`} style={cardStyle}>
      <div className="flex items-start gap-3.5">
        {/* Date column */}
        <div className="min-w-[38px] shrink-0 pt-1 text-center">
          <div className={`text-[10px] font-semibold uppercase tracking-[0.7px] ${isToday ? 'opacity-75' : 'opacity-55'}`}>
            {d.weekday}
          </div>
          <div className={`text-[24px] font-semibold leading-none tracking-[-0.5px] ${ink}`}>
            {d.date.slice(8)}
          </div>
        </div>

        {/* Body */}
        <div className="flex-1 min-w-0">
          <div className={`mb-1.5 text-[10px] font-bold uppercase tracking-[0.6px] ${badge.cls}`}>
            {badge.text}
          </div>

          {/* Per-session rows — each its own tap target. */}
          {sessions.map((s, i) => (
            <div
              key={s.key}
              role="button"
              tabIndex={0}
              aria-label={t('merged.open_session', { sport: s.sport ?? '—' })}
              onClick={onRow(s.path)}
              onKeyDown={e => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  navigate(s.path)
                }
              }}
              className={`-mx-2 cursor-pointer rounded-lg px-2 py-2 transition-colors ${rowHoverCls} ${i > 0 ? `mt-2 border-t pt-3 ${divider}` : ''}`}
            >
              <div className="flex items-start gap-3">
                <div className="flex-1 min-w-0">{s.render()}</div>
                <span
                  aria-hidden="true"
                  className={`shrink-0 self-center text-[20px] leading-none ${isToday ? 'text-white/70' : 'text-halo-ink-dimmer'}`}
                >
                  ›
                </span>
              </div>
            </div>
          ))}

          {/* past + missed planned — static, no tap target. */}
          {missed && (
            <div className="text-[13px] font-medium text-halo-coral">
              {stripWorkoutPrefix(planned?.name)} · {t('merged.skipped')}
            </div>
          )}

          {/* rest day — static. */}
          {restDay && (
            <div className={`text-[14px] italic ${dim}`}>{t('merged.recover_well')}</div>
          )}
        </div>
      </div>
    </div>
  )
}

// Sport-tinted pill — sport name in ALL CAPS, soft tonal background.
function SportPill({ type, isToday }: { type: string | null; isToday?: boolean }) {
  if (!type) return null
  if (isToday) {
    // White-on-cobalt variant — semi-transparent white background, white text.
    return (
      <span className="rounded-pill bg-white/20 px-2 py-[3px] text-[11px] font-semibold uppercase tracking-[0.4px] text-white">
        {type}
      </span>
    )
  }
  const tone = sportTone(type)
  return (
    <span
      className="rounded-pill px-2 py-[3px] text-[11px] font-semibold uppercase tracking-[0.4px]"
      style={{ background: tone.bg, color: tone.fg }}
    >
      {type}
    </span>
  )
}

function PlannedRow({
  w,
  ink,
  dim,
  isToday,
}: {
  w: ScheduledWorkout
  ink: string
  dim: string
  isToday: boolean
}) {
  return (
    <>
      <div className="flex items-center gap-2">
        <SportPill type={w.type} isToday={isToday} />
        <span className={`text-[15px] font-semibold ${ink}`}>{stripWorkoutPrefix(w.name)}</span>
      </div>
      {w.description && (
        <div className={`mt-1.5 text-[13px] leading-snug ${isToday ? 'text-white/80' : 'text-halo-ink-dim'}`}>
          {w.description}
        </div>
      )}
      <div className={`mt-2 flex gap-3.5 text-[12px] ${dim}`}>
        {w.duration && <span aria-label="duration">⏱ {w.duration}</span>}
        {w.distance_km != null && <span aria-label="distance">↔ {w.distance_km.toFixed(1)} km</span>}
      </div>
    </>
  )
}

// Extra planned session row — used for the 2nd+ planned workout on a multi-
// session day (design direction-b-halo.jsx:1490, `j>0 ? '+ '` prefix on the
// sport pill). Tighter copy than `PlannedRow` (no big name line); still
// shows description + duration · km so the row is informative enough to act
// as its own tap target.
function ExtraPlannedRow({
  w,
  ink,
  dim,
  isToday,
}: {
  w: ScheduledWorkout
  ink: string
  dim: string
  isToday?: boolean
}) {
  // Skip the sportTone lookup on today's cobalt card — pill uses white-on-cobalt
  // instead of the per-sport tone, so the tone result would be dead code.
  const tone = isToday ? null : sportTone(w.type)
  const pillCls = isToday
    ? 'rounded-pill bg-white/20 px-2 py-[3px] text-[11px] font-semibold uppercase tracking-[0.4px] text-white'
    : 'rounded-pill px-2 py-[3px] text-[11px] font-semibold uppercase tracking-[0.4px]'
  const pillStyle = tone ? { background: tone.bg, color: tone.fg } : undefined
  return (
    <>
      <div className="flex items-center gap-2">
        <span className={pillCls} style={pillStyle}>
          + {w.type ?? '—'}
        </span>
        <span className={`text-[13px] font-semibold ${ink}`}>{stripWorkoutPrefix(w.name)}</span>
      </div>
      {w.description && (
        <div className={`mt-1 text-[12px] leading-snug ${isToday ? 'text-white/75' : 'text-halo-ink-dim'}`}>
          {w.description}
        </div>
      )}
      <div className={`mt-1 flex gap-3.5 text-[12px] ${dim}`}>
        {w.duration && <span>⏱ {w.duration}</span>}
        {w.distance_km != null && <span>↔ {w.distance_km.toFixed(1)} km</span>}
      </div>
    </>
  )
}

function ActualRow({ a, ink, dim, isToday, t }: { a: ActivityItem; ink: string; dim: string; isToday?: boolean; t: (k: string) => string }) {
  return (
    <>
      <div className="flex items-center gap-2">
        <SportPill type={a.type} isToday={isToday} />
        <span className={`text-[15px] font-semibold ${ink}`}>{a.duration || '—'}</span>
      </div>
      <div className={`mt-1.5 flex items-center gap-3.5 text-[12px] ${dim}`}>
        {a.icu_training_load != null && <span>{Math.round(a.icu_training_load)} TSS</span>}
        {a.average_hr != null && <span>{Math.round(a.average_hr)} bpm</span>}
        {/* Intervals returns 0 for activities with no paired planned workout
            (= nothing to score against). Hide the chip in that case — «0% on
            plan» reads as a failure when really there was no plan at all. */}
        {a.compliance != null && a.compliance > 0 && <ComplianceChip value={a.compliance} isToday={isToday} t={t} />}
      </div>
    </>
  )
}

// Per-design (direction-b-halo.jsx:1489) compliance reads as a subtle dot +
// percent + «on plan» chip in the metrics row, right-aligned via `ml-auto`.
// Thresholds: ≥90 sage / 70–89 amber / <70 coral. On the cobalt today-card
// the dot keeps its colour and the «on plan» tail uses white/55 so it still
// reads as secondary text against the inverted background.
function ComplianceChip({ value, isToday, t }: { value: number; isToday?: boolean; t: (k: string) => string }) {
  const c = Math.round(value)
  const color = c >= 90 ? 'var(--color-status-green)' : c >= 70 ? 'var(--color-amber)' : 'var(--color-coral)'
  const tailCls = isToday ? 'text-white/55' : 'text-halo-ink-dimmer'
  return (
    <span className="ml-auto inline-flex items-center gap-1.5" style={{ color }}>
      <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: color }} />
      <span className="font-semibold">{c}%</span>
      <span className={`font-normal ${tailCls}`}>{t('merged.compliance_tail')}</span>
    </span>
  )
}

function ExtraActualRow({
  a,
  ink,
  dim,
  t,
  isToday,
}: {
  a: ActivityItem
  ink: string
  dim: string
  t: (k: string) => string
  isToday?: boolean
}) {
  // White-on-cobalt variant mirrors `SportPill` on today's inverted card so
  // the colored sport tones don't clash with the brand background. Skip the
  // sportTone lookup when isToday — tone result would be dead code.
  const tone = isToday ? null : sportTone(a.type)
  const pillCls = isToday
    ? 'rounded-pill bg-white/20 px-2 py-[3px] text-[11px] font-semibold uppercase tracking-[0.4px] text-white'
    : 'rounded-pill px-2 py-[3px] text-[11px] font-semibold uppercase tracking-[0.4px]'
  const pillStyle = tone ? { background: tone.bg, color: tone.fg } : undefined
  return (
    <>
      <div className="flex items-center gap-2">
        <span className={pillCls} style={pillStyle}>
          + {a.type ?? '—'}
        </span>
        <span className={`text-[13px] font-semibold ${ink}`}>{a.duration || '—'}</span>
        {a.is_race && (
          <span className="rounded-pill bg-halo-coral px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.4px] text-white">
            {t('merged.race')}
          </span>
        )}
      </div>
      <div className={`mt-1 text-[12px] ${dim}`}>
        {[a.icu_training_load != null ? `${Math.round(a.icu_training_load)} TSS` : null,
          a.average_hr != null ? `${Math.round(a.average_hr)} bpm` : null]
          .filter(Boolean)
          .join(' · ')}
      </div>
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
