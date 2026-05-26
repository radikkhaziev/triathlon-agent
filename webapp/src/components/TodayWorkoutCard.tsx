import { useMemo } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import DayCard, { type WeekDay } from './DayCard'
import { useApi } from '../hooks/useApi'
import { sportTone } from '../lib/constants'
import { stripWorkoutPrefix } from '../lib/formatters'
import type {
  ActivitiesWeekResponse,
  ActivityItem,
  ScheduledWorkout,
  ScheduledWorkoutsResponse,
} from '../api/types'

// Monday of the week containing `d`, normalised to local midnight. Used to
// derive `week_offset` for the selected day so the /scheduled-workouts call
// targets the right week (Wellness DateStrip can land on the previous week's
// Sunday).
function mondayOf(d: Date): Date {
  const day = d.getDay() // 0=Sun, 1=Mon, ..., 6=Sat
  const diff = (day + 6) % 7 // days since Monday
  const m = new Date(d)
  m.setDate(d.getDate() - diff)
  m.setHours(0, 0, 0, 0)
  return m
}

type TFn = (k: string, o?: Record<string, unknown>) => string

interface Props {
  /** ISO 'YYYY-MM-DD' for the day to render (the day Wellness is showing). */
  dateStr: string
  /** Same day as `dateStr`, as a Date (used to compute week offset). */
  currentDate: Date
  /** True if `dateStr` is the user's local today. */
  isToday: boolean
}

/**
 * Compact "Plan / Actual" card for the Wellness (Today) page.
 *
 * Pulls the selected day's planned workout + actual activity from the same
 * /scheduled-workouts + /activities-week endpoints used by the Week tab,
 * filters to one date, and renders them:
 *   • Mobile — reuses the shared `DayCard` so it reads identically to the
 *     Week tab's per-day card. Each session is its own mini-card (white for
 *     done, cobalt for planned-not-done, coral-tinted for past-missed); the
 *     outer frame stays neutral and «today» surfaces via the date column.
 *     `hideDate` strips the left date column here since Wellness's DateStrip
 *     already shows the day above.
 *   • Desktop — keeps the original 2-col Plan|Actual side-by-side layout
 *     (the wide canvas reads better as a direct comparison than as a single
 *     stacked card with a date column the page already shows above).
 *
 * Renders `null` when there's nothing to show (rest day with no plan + no
 * actual, or the API call is still loading / errored).
 */
export default function TodayWorkoutCard({ dateStr, currentDate, isToday }: Props) {
  const { t } = useTranslation()
  const navigate = useNavigate()
  // `new Date()` was called every render — within the same calendar day all
  // those Dates collapse to the same Monday after mondayOf(), so it didn't
  // actually trigger refetches, but it's wasted work and a midnight-edge
  // re-render would otherwise quietly shift week-offset. Memoise on `dateStr`
  // (changes per day-swipe in the parent) — that's enough to keep the offset
  // fresh across day rollover without depending on identity-unstable `Date`.
  const weekOffset = useMemo(() => {
    const todayMonday = mondayOf(new Date())
    const selMonday = mondayOf(currentDate)
    return Math.round((selMonday.getTime() - todayMonday.getTime()) / (7 * 86400000))
  }, [dateStr, currentDate])

  const plan = useApi<ScheduledWorkoutsResponse>(`/api/scheduled-workouts?week_offset=${weekOffset}`)
  const acts = useApi<ActivitiesWeekResponse>(`/api/activities-week?week_offset=${weekOffset}`)

  if (plan.loading || acts.loading) return null
  if (plan.error || acts.error || !plan.data || !acts.data) return null

  const planDay = plan.data.days.find(d => d.date === dateStr)
  const actDay = acts.data.days.find(d => d.date === dateStr)
  const planned = planDay?.workouts ?? []
  const actuals = actDay?.activities ?? []
  // Nothing to surface — Wellness's other cards still carry the day. No empty
  // placeholder card (avoids «—  /  —» noise on real rest days).
  if (planned.length === 0 && actuals.length === 0) return null

  // Shared-DayCard input — derive state the same way `MergedWeek.buildWeek`
  // does. `weekday` falls back to the activities-week feed if the plan feed
  // doesn't carry the selected day.
  const day: WeekDay = {
    date: dateStr,
    weekday: planDay?.weekday ?? actDay?.weekday ?? '',
    state: dateStr === plan.data.today ? 'today' : dateStr < plan.data.today ? 'past' : 'future',
    planned,
    actuals,
  }

  return (
    <>
      {/* Mobile — same per-session DayCard as the Week tab. */}
      <div className="md:hidden">
        <DayCard d={day} t={t} navigate={navigate} hideDate />
      </div>
      {/* Desktop — original 2-col Plan|Actual layout (untouched per request). */}
      <div className="hidden md:block">
        <DesktopPlanActualCard
          planned={planned}
          actuals={actuals}
          isPast={dateStr < plan.data.today}
          isToday={isToday}
          t={t}
        />
      </div>
    </>
  )
}

// ─── Desktop-only Plan|Actual card (preserved verbatim from the pre-refactor
// `TodayWorkoutCard`) ────────────────────────────────────────────────────────

interface DesktopProps {
  planned: ScheduledWorkout[]
  actuals: ActivityItem[]
  isPast: boolean
  isToday: boolean
  t: TFn
}

function DesktopPlanActualCard({ planned, actuals, isPast, isToday, t }: DesktopProps) {
  const pairedIds = new Set(actuals.map(a => a.paired_event_id).filter((v): v is number => v != null))
  const unpairedPlanned = planned.filter(w => !pairedIds.has(w.id))
  const missed = isPast && actuals.length === 0 && planned.length > 0

  // State badge — same vocabulary as the Week tab DayCard so users see the
  // same words on both screens.
  let badgeText: string
  let badgeCls: string
  if (missed) {
    badgeText = t('merged.missed')
    badgeCls = 'text-halo-coral'
  } else if (actuals.length > 0 && unpairedPlanned.length === 0) {
    badgeText = t('merged.completed')
    badgeCls = 'text-halo-status-green'
  } else if (actuals.length > 0) {
    badgeText = isToday ? t('merged.in_progress') : t('merged.completed')
    badgeCls = isToday ? 'text-halo-brand-dark' : 'text-halo-status-green'
  } else if (isToday) {
    badgeText = t('merged.in_progress')
    badgeCls = 'text-halo-brand-dark'
  } else {
    badgeText = t('merged.planned_state')
    badgeCls = 'text-halo-ink-dim'
  }

  return (
    <div className="rounded-card border border-halo-border bg-halo-surface p-4 shadow-card">
      <div className="mb-3 flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
          {t('wellness.todays_workout')}
        </span>
        <span className={`text-[10px] font-bold uppercase tracking-[0.6px] ${badgeCls}`}>
          {badgeText}
        </span>
      </div>
      <div className="grid gap-4 md:grid-cols-2">
        <PlanCol planned={planned} t={t} />
        <ActualCol actuals={actuals} t={t} />
      </div>
    </div>
  )
}

function SectionLabel({ children }: { children: React.ReactNode }) {
  return (
    <div className="mb-2 text-[10px] font-bold uppercase tracking-[0.6px] text-halo-ink-dimmer">
      {children}
    </div>
  )
}

function SportPill({ type }: { type: string | null }) {
  if (!type) return null
  const tone = sportTone(type)
  return (
    <span
      className="rounded-pill px-1.5 py-[2px] text-[10px] font-semibold uppercase tracking-[0.4px]"
      style={{ background: tone.bg, color: tone.fg }}
    >
      {type}
    </span>
  )
}

function PlanCol({ planned, t }: { planned: ScheduledWorkout[]; t: TFn }) {
  return (
    <div>
      <SectionLabel>{t('activities.col_plan')}</SectionLabel>
      {planned.length === 0 ? (
        <div className="text-[13px] text-halo-ink-dim">—</div>
      ) : (
        <div className="flex flex-col gap-2">
          {planned.map(w => (
            <Link
              key={w.id}
              to={`/workout/${w.id}`}
              className="-mx-2 block rounded-lg px-2 py-1.5 no-underline text-inherit transition-colors hover:bg-halo-surface-2"
            >
              <div className="flex items-center gap-2">
                <SportPill type={w.type} />
                {/* stripWorkoutPrefix falls back to a Russian word on null —
                    avoid the locale leak by branching first and using the
                    i18n'd «Тренировка / Workout» label. */}
                <span className="text-[14px] font-semibold text-halo-ink">
                  {w.name ? stripWorkoutPrefix(w.name) : t('wellness.todays_workout')}
                </span>
                <span aria-hidden="true" className="ml-auto text-[15px] leading-none text-halo-ink-dimmer">›</span>
              </div>
              {w.description && (
                <div className="mt-1 line-clamp-2 text-[12px] leading-snug text-halo-ink-dim">
                  {w.description}
                </div>
              )}
              <div className="mt-1.5 flex flex-wrap gap-x-3 gap-y-0.5 text-[11px] text-halo-ink-dim">
                {w.duration && (
                  <span aria-label="duration">
                    <span aria-hidden="true">⏱ </span>{w.duration}
                  </span>
                )}
                {w.distance_km != null && (
                  <span aria-label="distance">
                    <span aria-hidden="true">↔ </span>{w.distance_km.toFixed(1)} km
                  </span>
                )}
                {w.icu_training_load != null && <span>{Math.round(w.icu_training_load)} TSS</span>}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

function ActualCol({ actuals, t }: { actuals: ActivityItem[]; t: TFn }) {
  return (
    <div className="md:border-l md:border-halo-border md:pl-4">
      <SectionLabel>{t('activities.col_actual')}</SectionLabel>
      {actuals.length === 0 ? (
        <div className="text-[13px] text-halo-ink-dim">—</div>
      ) : (
        <div className="flex flex-col gap-2">
          {actuals.map(a => (
            <Link
              key={a.id}
              to={`/activity/${a.id}`}
              className="-mx-2 block rounded-lg px-2 py-1.5 no-underline text-inherit transition-colors hover:bg-halo-surface-2"
            >
              <div className="flex items-center gap-2">
                <SportPill type={a.type} />
                <span className="text-[14px] font-semibold text-halo-ink">{a.duration || '—'}</span>
                {a.is_race && (
                  <span className="rounded-pill bg-halo-coral px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.4px] text-white">
                    {t('merged.race')}
                  </span>
                )}
                <span aria-hidden="true" className="ml-auto text-[15px] leading-none text-halo-ink-dimmer">›</span>
              </div>
              <div className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 text-[11px] text-halo-ink-dim">
                {a.icu_training_load != null && <span>{Math.round(a.icu_training_load)} TSS</span>}
                {a.average_hr != null && <span>{Math.round(a.average_hr)} bpm</span>}
                {a.compliance != null && a.compliance > 0 && (
                  <ComplianceTag value={a.compliance} t={t} />
                )}
              </div>
            </Link>
          ))}
        </div>
      )}
    </div>
  )
}

// Same threshold ladder as MergedWeek's ComplianceChip (≥90 green / 70–89
// amber / <70 coral) so the chip reads identically on both screens.
function ComplianceTag({ value, t }: { value: number; t: TFn }) {
  const c = Math.round(value)
  const color = c >= 90 ? 'var(--color-status-green)' : c >= 70 ? 'var(--color-amber)' : 'var(--color-coral)'
  return (
    <span className="inline-flex items-center gap-1" style={{ color }}>
      <span className="h-1.5 w-1.5 rounded-full" style={{ background: color }} />
      <span className="font-semibold">{c}%</span>
      <span className="font-normal text-halo-ink-dimmer">{t('merged.compliance_tail')}</span>
    </span>
  )
}
