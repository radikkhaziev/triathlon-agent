import { sportTone } from '../lib/constants'
import { stripWorkoutPrefix } from '../lib/formatters'
import type { ScheduledWorkout, ActivityItem } from '../api/types'

// Day-card visual shared by the Week tab and the Today (Wellness) page.
//
// Color contract (mirrors Intervals.icu's day view):
//   • Each SESSION is its own mini-card with its own surface.
//   • Completed actual session → white surface (ink text).
//   • Planned-not-yet-done session → cobalt blue surface (white text).
//   • Past-missed planned       → faint coral surface (coral text).
// The outer day frame is neutral — "today" is marked by the date column
// (today's date number renders in the brand accent), not by inverting the
// whole card the way the previous design did.
//
// Badge text:
//   • Mixed (some done, some pending) → "X из Y сессий" (progress format)
//   • All done                        → "Выполнено · N сессий"
//   • All pending                     → "По плану · N сессий" (or "в процессе" for today, not-yet-done)
//   • Past + all missed               → "Пропущено"
//   • Rest day                        → "День отдыха"

export type DayState = 'past' | 'today' | 'future'

export interface WeekDay {
  date: string
  weekday: string
  state: DayState
  planned: ScheduledWorkout[]
  actuals: ActivityItem[]
}

type TFn = (k: string, o?: Record<string, unknown>) => string

interface DayCardProps {
  d: WeekDay
  t: TFn
  navigate: (path: string) => void
  /** Hide the left-side date column. Used on the Today page where DateStrip
   *  already surfaces the day — the column would just repeat it. */
  hideDate?: boolean
}

type SessionState = 'done' | 'pending' | 'missed'
type SessionRow = {
  key: string
  path: string | null
  sport: string | null
  state: SessionState
  render: (onBlue: boolean) => JSX.Element
}

export default function DayCard({ d, t, navigate, hideDate = false }: DayCardProps) {
  const isToday = d.state === 'today'
  const isPast = d.state === 'past'
  const isFuture = d.state === 'future'
  const planned = d.planned[0] ?? null
  const main = d.actuals[0] ?? null
  // `slice(1)` covers all main-non-null cases; the empty-actuals case slices
  // an empty array to an empty array. The pre-refactor `main ? 1 : 0` ternary
  // was vestigial.
  const extras = d.actuals.slice(1)
  const restDay = d.planned.length === 0 && d.actuals.length === 0
  const missed = isPast && !main && d.planned.length > 0

  // `stripWorkoutPrefix` returns the hard-coded Russian fallback «Тренировка»
  // when name is null — that would leak RU into the EN UI. Branch first, fall
  // back to an i18n label.
  const workoutName = (name: string | null | undefined): string =>
    name ? stripWorkoutPrefix(name) : t('wellness.todays_workout')

  // Build the per-session row list. Each row carries its own completion state
  // so the renderer can pick the right surface color.
  const sessions: SessionRow[] = []
  if (main) {
    sessions.push({
      key: `a-${main.id}`,
      path: `/activity/${main.id}`,
      sport: main.type,
      state: 'done',
      render: (onBlue) => <ActualRow a={main} onBlue={onBlue} t={t} />,
    })
    for (const a of extras) {
      sessions.push({
        key: `a-${a.id}`,
        path: `/activity/${a.id}`,
        sport: a.type,
        state: 'done',
        render: (onBlue) => <ExtraActualRow a={a} onBlue={onBlue} t={t} />,
      })
    }
  }
  // Unpaired planned — render as pending (today/future) or missed (past).
  const pairedIds = new Set(d.actuals.map(a => a.paired_event_id).filter((v): v is number => v != null))
  for (const w of d.planned) {
    if (pairedIds.has(w.id)) continue
    if (isPast && main) {
      sessions.push({
        key: `m-${w.id}`,
        path: `/workout/${w.id}`,
        sport: w.type,
        state: 'missed',
        render: () => (
          <div className="text-[13px] font-medium text-halo-coral">
            {workoutName(w.name)} · {t('merged.skipped')}
          </div>
        ),
      })
      continue
    }
    if (isToday || isFuture) {
      const idx = sessions.length
      const name = workoutName(w.name)
      sessions.push({
        key: `p-${w.id}`,
        path: `/workout/${w.id}`,
        sport: w.type,
        state: 'pending',
        render: (onBlue) =>
          idx === 0 && !main ? (
            <PlannedRow w={w} name={name} onBlue={onBlue} />
          ) : (
            <ExtraPlannedRow w={w} name={name} onBlue={onBlue} />
          ),
      })
    }
  }

  // Badge — progress format when mixed, simple state otherwise.
  const doneCount = sessions.filter(s => s.state === 'done').length
  const pendingCount = sessions.filter(s => s.state === 'pending').length
  const missedRowCount = sessions.filter(s => s.state === 'missed').length
  const sessionCount = sessions.length
  const multi = sessionCount > 1

  let badgeText: string
  let badgeCls: string
  if (missed) {
    badgeText = t('merged.missed')
    badgeCls = 'text-halo-coral'
  } else if (restDay) {
    badgeText = t('merged.rest_day')
    badgeCls = 'text-halo-ink-dimmer'
  } else if (doneCount > 0 && (pendingCount > 0 || missedRowCount > 0)) {
    // Mixed — done + remaining (pending) OR done + missed (past brick day
    // where one planned was skipped). Total covers all rendered rows so the
    // badge matches what the user actually sees.
    badgeText = t('merged.progress_count', { count: sessionCount, done: doneCount })
    badgeCls = 'text-halo-brand-dark'
  } else if (doneCount > 0) {
    // All rendered sessions completed.
    badgeText = multi
      ? `${t('merged.completed')} · ${t('merged.sessions_count', { count: doneCount })}`
      : t('merged.completed')
    badgeCls = 'text-halo-status-green'
  } else if (pendingCount > 0) {
    // Nothing started yet.
    if (isToday) {
      badgeText = multi
        ? `${t('merged.in_progress')} · ${t('merged.sessions_count', { count: pendingCount })}`
        : t('merged.in_progress')
      badgeCls = 'text-halo-brand-dark'
    } else {
      badgeText = multi
        ? `${t('merged.planned_state')} · ${t('merged.sessions_count', { count: pendingCount })}`
        : t('merged.planned_state')
      badgeCls = 'text-halo-ink-dim'
    }
  } else if (isPast) {
    badgeText = '—'
    badgeCls = 'text-halo-ink-dimmer'
  } else {
    badgeText = t('merged.planned_state')
    badgeCls = 'text-halo-ink-dim'
  }

  // Date column — today's date gets the brand accent (lost the cobalt frame,
  // so we mark "today" here instead).
  const dateNumCls = isToday
    ? 'text-halo-brand-dark'
    : isPast
      ? 'text-halo-ink-dim'
      : 'text-halo-ink'
  const weekdayCls = isToday ? 'text-halo-brand-dark opacity-90' : 'text-halo-ink-dimmer'

  // Per-session surface — drives bg/text/border.
  // For 'missed': border-only blended into the page surface, so we add an
  // explicit coral-tinted bg via inline style at render time. Pure Tailwind
  // arbitrary-opacity bg would purge unreliably across the build pipeline.
  const sessionSurface = (state: SessionState): string => {
    if (state === 'pending') return 'bg-halo-brand text-white shadow-card'
    if (state === 'missed') return 'border border-halo-coral/30 text-halo-ink'
    return 'bg-halo-surface text-halo-ink border border-halo-border shadow-card'
  }
  const sessionStyle = (state: SessionState): React.CSSProperties | undefined =>
    state === 'missed'
      ? { background: 'color-mix(in srgb, var(--color-coral) 8%, var(--color-surface))' }
      : undefined
  const sessionHover = (state: SessionState): string =>
    state === 'pending' ? 'hover:brightness-110' : 'hover:bg-halo-surface-2'

  // The outer frame: neutral on all days. Rest day gets a faint surface-2.
  const outerCls = restDay
    ? 'rounded-card bg-halo-surface-2 p-3.5 border border-halo-border'
    : 'rounded-card p-0'
  const outerStyle = missed && !restDay
    ? { background: 'color-mix(in srgb, var(--color-coral) 8%, transparent)' }
    : undefined

  return (
    <div className={outerCls} style={outerStyle}>
      <div className="flex items-start gap-3">
        {!hideDate && (
          <div className="min-w-[38px] shrink-0 pt-1 text-center">
            <div className={`text-[10px] font-semibold uppercase tracking-[0.7px] ${weekdayCls}`}>
              {d.weekday}
            </div>
            <div className={`text-[24px] font-semibold leading-none tracking-[-0.5px] ${dateNumCls}`}>
              {d.date.slice(8)}
            </div>
          </div>
        )}

        <div className="flex-1 min-w-0">
          <div className={`mb-2 text-[10px] font-bold uppercase tracking-[0.6px] ${badgeCls}`}>
            {badgeText}
          </div>

          {/* Per-session mini-cards stacked. Each one a separate visual unit
              with state-driven surface. */}
          {sessions.length > 0 && (
            <div className="flex flex-col gap-2">
              {sessions.map(s => {
                const onBlue = s.state === 'pending'
                const handleClick = s.path ? () => navigate(s.path!) : undefined
                return (
                  <div
                    key={s.key}
                    role={handleClick ? 'button' : undefined}
                    tabIndex={handleClick ? 0 : undefined}
                    aria-label={
                      handleClick
                        ? s.sport
                          ? t('merged.open_session', { sport: s.sport })
                          : t('merged.open_session_generic')
                        : undefined
                    }
                    onClick={handleClick}
                    onKeyDown={e => {
                      if (!handleClick) return
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault()
                        handleClick()
                      }
                    }}
                    className={`rounded-card p-3 transition-all ${sessionSurface(s.state)} ${handleClick ? `cursor-pointer ${sessionHover(s.state)}` : ''}`}
                    style={sessionStyle(s.state)}
                  >
                    <div className="flex items-start gap-3">
                      <div className="flex-1 min-w-0">{s.render(onBlue)}</div>
                      <span
                        aria-hidden="true"
                        className={`shrink-0 self-center text-[20px] leading-none ${onBlue ? 'text-white/70' : 'text-halo-ink-dimmer'}`}
                      >
                        ›
                      </span>
                    </div>
                  </div>
                )
              })}
            </div>
          )}

          {/* Past-missed shorthand line (kept for past+missed days with no
              actual — gives the user a single "skipped" summary instead of an
              empty card). */}
          {missed && sessions.length === 0 && (
            <div className="text-[13px] font-medium text-halo-coral">
              {workoutName(planned?.name)} · {t('merged.skipped')}
            </div>
          )}

          {/* Rest day — static. */}
          {restDay && (
            <div className="text-[14px] italic text-halo-ink-dim">{t('merged.recover_well')}</div>
          )}
        </div>
      </div>
    </div>
  )
}

function SportPill({ type, onBlue }: { type: string | null; onBlue?: boolean }) {
  if (!type) return null
  if (onBlue) {
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

function PlannedRow({ w, name, onBlue }: { w: ScheduledWorkout; name: string; onBlue: boolean }) {
  const ink = onBlue ? 'text-white' : 'text-halo-ink'
  const dim = onBlue ? 'text-white/70' : 'text-halo-ink-dim'
  const desc = onBlue ? 'text-white/80' : 'text-halo-ink-dim'
  return (
    <>
      <div className="flex items-center gap-2">
        <SportPill type={w.type} onBlue={onBlue} />
        <span className={`text-[15px] font-semibold ${ink}`}>{name}</span>
      </div>
      {w.description && (
        <div className={`mt-1.5 text-[13px] leading-snug ${desc}`}>
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

function ExtraPlannedRow({ w, name, onBlue }: { w: ScheduledWorkout; name: string; onBlue: boolean }) {
  const ink = onBlue ? 'text-white' : 'text-halo-ink'
  const dim = onBlue ? 'text-white/70' : 'text-halo-ink-dim'
  const desc = onBlue ? 'text-white/75' : 'text-halo-ink-dim'
  return (
    <>
      <div className="flex items-center gap-2">
        <SportPill type={w.type} onBlue={onBlue} />
        <span className={`text-[15px] font-semibold ${ink}`}>{name}</span>
      </div>
      {w.description && (
        <div className={`mt-1.5 text-[13px] leading-snug ${desc}`}>
          {w.description}
        </div>
      )}
      <div className={`mt-2 flex gap-3.5 text-[12px] ${dim}`}>
        {w.duration && <span>⏱ {w.duration}</span>}
        {w.distance_km != null && <span>↔ {w.distance_km.toFixed(1)} km</span>}
      </div>
    </>
  )
}

function ActualRow({ a, onBlue, t }: { a: ActivityItem; onBlue: boolean; t: TFn }) {
  const ink = onBlue ? 'text-white' : 'text-halo-ink'
  const dim = onBlue ? 'text-white/70' : 'text-halo-ink-dim'
  return (
    <>
      <div className="flex items-center gap-2">
        <SportPill type={a.type} onBlue={onBlue} />
        <span className={`text-[15px] font-semibold ${ink}`}>{a.duration || '—'}</span>
        {a.is_race && (
          <span className="rounded-pill bg-halo-coral px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.4px] text-white">
            {t('merged.race')}
          </span>
        )}
      </div>
      <div className={`mt-1.5 flex items-center gap-3.5 text-[12px] ${dim}`}>
        {a.icu_training_load != null && <span>{Math.round(a.icu_training_load)} TSS</span>}
        {a.average_hr != null && <span>{Math.round(a.average_hr)} bpm</span>}
        {a.compliance != null && a.compliance > 0 && <ComplianceChip value={a.compliance} onBlue={onBlue} t={t} />}
      </div>
    </>
  )
}

function ComplianceChip({ value, onBlue, t }: { value: number; onBlue: boolean; t: TFn }) {
  const c = Math.round(value)
  const color = c >= 90 ? 'var(--color-status-green)' : c >= 70 ? 'var(--color-amber)' : 'var(--color-coral)'
  const tailCls = onBlue ? 'text-white/55' : 'text-halo-ink-dimmer'
  return (
    <span className="ml-auto inline-flex items-center gap-1.5" style={{ color }}>
      <span className="h-1.5 w-1.5 shrink-0 rounded-full" style={{ background: color }} />
      <span className="font-semibold">{c}%</span>
      <span className={`font-normal ${tailCls}`}>{t('merged.compliance_tail')}</span>
    </span>
  )
}

function ExtraActualRow({ a, onBlue, t }: { a: ActivityItem; onBlue: boolean; t: TFn }) {
  const ink = onBlue ? 'text-white' : 'text-halo-ink'
  const dim = onBlue ? 'text-white/70' : 'text-halo-ink-dim'
  return (
    <>
      <div className="flex items-center gap-2">
        <SportPill type={a.type} onBlue={onBlue} />
        <span className={`text-[15px] font-semibold ${ink}`}>{a.duration || '—'}</span>
        {a.is_race && (
          <span className="rounded-pill bg-halo-coral px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.4px] text-white">
            {t('merged.race')}
          </span>
        )}
      </div>
      <div className={`mt-1.5 flex items-center gap-3.5 text-[12px] ${dim}`}>
        {a.icu_training_load != null && <span>{Math.round(a.icu_training_load)} TSS</span>}
        {a.average_hr != null && <span>{Math.round(a.average_hr)} bpm</span>}
        {a.compliance != null && a.compliance > 0 && <ComplianceChip value={a.compliance} onBlue={onBlue} t={t} />}
      </div>
    </>
  )
}
