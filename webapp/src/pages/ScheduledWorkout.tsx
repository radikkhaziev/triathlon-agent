import { useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import { useApi } from '../hooks/useApi'
import { fmtDateShort, fmtDuration, fmtPace, sportLabel, stripWorkoutPrefix } from '../lib/formatters'
import { SPORT_ICONS } from '../lib/constants'
import type {
  ScheduledWorkoutDetail,
  WorkoutDetailThresholds,
  WorkoutStep,
  WorkoutTarget,
} from '../api/types'

export default function ScheduledWorkout() {
  const { t, i18n } = useTranslation()
  const { id } = useParams<{ id: string }>()
  const numericId = id && /^\d+$/.test(id) ? id : null
  const { data, loading, error } = useApi<ScheduledWorkoutDetail>(
    numericId ? `/api/scheduled-workout/${numericId}` : null,
  )

  if (!numericId) {
    return (
      <Layout backTo="/plan" backLabel={t('plan.back_to_list')} hideBottomTabs>
        <ErrorMessage message={t('plan.load_workout_error')} />
      </Layout>
    )
  }
  if (loading) return <Layout backTo="/plan" backLabel={t('plan.back_to_list')} hideBottomTabs><LoadingSpinner /></Layout>
  if (error || !data) {
    return (
      <Layout backTo="/plan" backLabel={t('plan.back_to_list')} hideBottomTabs>
        <ErrorMessage message={t('plan.load_workout_error')} />
      </Layout>
    )
  }

  const icon = SPORT_ICONS[data.type || ''] || '\u{1F3C6}'
  const name = stripWorkoutPrefix(data.name)
  const sub = [
    fmtDateShort(data.date, i18n.language),
    data.duration,
    data.distance_km ? `${data.distance_km} km` : null,
  ]
    .filter(Boolean)
    .join(' · ')

  return (
    <Layout backTo="/plan" backLabel={t('plan.back_to_list')} hideBottomTabs>
      <div className="py-4 pb-3">
        <div className="text-xl font-bold flex items-center gap-2">
          <span>{icon}</span>
          <span>{name}</span>
          <span className="text-sm text-text-dim ml-1">{sportLabel(data.type)}</span>
        </div>
        <div className="text-[13px] text-text-dim mt-1">{sub}</div>
      </div>

      {data.steps && data.steps.length > 0 ? (
        <div className="bg-surface border border-border rounded-xl px-3.5 py-3 mb-4">
          <div className="text-sm font-bold mb-2 pb-1 border-b border-border">{t('plan.steps_title')}</div>
          <StepList steps={data.steps} sport={data.type} thresholds={data.thresholds} />
        </div>
      ) : data.description ? (
        <div className="bg-surface border border-border rounded-xl px-3.5 py-3 mb-4">
          <div className="text-sm font-bold mb-2 pb-1 border-b border-border">{t('plan.description_title')}</div>
          <pre className="font-mono text-xs leading-relaxed text-text-dim whitespace-pre-wrap break-words m-0">
            {data.description}
          </pre>
        </div>
      ) : (
        <ErrorMessage message={t('plan.no_steps')} />
      )}
    </Layout>
  )
}

function StepList({
  steps,
  sport,
  thresholds,
  depth = 0,
}: {
  steps: WorkoutStep[]
  sport: string | null
  thresholds: WorkoutDetailThresholds
  depth?: number
}) {
  const { t } = useTranslation()
  return (
    <ul className={`list-none m-0 ${depth > 0 ? 'pl-4 mt-1' : 'pl-0'} space-y-1.5`}>
      {steps.map((s, i) => (
        <li key={i} className="text-[13px] leading-snug">
          {s.reps && s.steps ? (
            <RepeatGroup step={s} sport={sport} thresholds={thresholds} depth={depth} />
          ) : (
            <StepLine step={s} sport={sport} thresholds={thresholds} t={t} />
          )}
        </li>
      ))}
    </ul>
  )
}

function RepeatGroup({
  step,
  sport,
  thresholds,
  depth,
}: {
  step: WorkoutStep
  sport: string | null
  thresholds: WorkoutDetailThresholds
  depth: number
}) {
  return (
    <div>
      <div className="font-semibold text-text-dim">{step.reps}&times;</div>
      <StepList steps={step.steps || []} sport={sport} thresholds={thresholds} depth={depth + 1} />
    </div>
  )
}

function StepLine({
  step,
  sport,
  thresholds,
  t,
}: {
  step: WorkoutStep
  sport: string | null
  thresholds: WorkoutDetailThresholds
  t: (key: string) => string
}) {
  const parts: string[] = []
  parts.push(step.text || '—')

  const lenStr = stepLength(step)
  if (lenStr) parts.push(lenStr)

  const target = step.power || step.hr || step.pace
  if (target) {
    const pct = target.end != null && target.end !== target.start
      ? `${target.start}-${target.end}%`
      : `${target.start}%`
    const kind = targetKind(target, t)
    const abs = absoluteRange(target, sport, thresholds)
    parts.push(abs ? `${pct} ${kind} (${abs})` : `${pct} ${kind}`)
  }

  if (step.cadence) {
    const c = step.cadence.end != null && step.cadence.end !== step.cadence.start
      ? `${step.cadence.start}-${step.cadence.end}`
      : `${step.cadence.start}`
    parts.push(`${c} ${step.cadence.units}`)
  }

  return <span>&bull; {parts.join('  ')}</span>
}

function stepLength(step: WorkoutStep): string | null {
  if (step.distance != null && step.distance > 0) {
    if (step.distance >= 1000 && step.distance % 1000 === 0) {
      return `${step.distance / 1000} km`
    }
    return `${Math.round(step.distance)} m`
  }
  if (step.duration > 0) return fmtDuration(step.duration)
  return null
}

function targetKind(target: WorkoutTarget, t: (key: string) => string): string {
  const u = target.units.toLowerCase()
  if (u.includes('ftp')) return t('plan.target_power')
  if (u.includes('pace')) return t('plan.target_pace')
  if (u.includes('lthr') || u.includes('hr')) return t('plan.target_hr')
  return target.units
}

/**
 * Convert a percentage corridor (`%lthr` / `%ftp` / `%pace`) into an absolute
 * range using the athlete's per-sport thresholds. Returns a display string or
 * `null` when the relevant threshold isn't available (cold-start, missing
 * Intervals.icu data) or the sport / units don't match a known conversion.
 *
 * `end` is optional on `WorkoutTarget` — if absent, treat as single-value
 * target (start == end) so we render `{abs}` instead of `{abs}-{nan}`.
 *
 * Pace math is inverted: higher % = faster = smaller seconds. Display order
 * follows Intervals.icu's convention — fastest first, slowest second.
 */
function absoluteRange(
  target: WorkoutTarget,
  sport: string | null,
  t: WorkoutDetailThresholds,
): string | null {
  const u = target.units.toLowerCase()
  const end = target.end ?? target.start
  if (u.includes('ftp')) {
    if (!t.ftp) return null
    const lo = Math.round((target.start * t.ftp) / 100)
    const hi = Math.round((end * t.ftp) / 100)
    return lo === hi ? `${lo}W` : `${lo}-${hi}W`
  }
  if (u.includes('lthr') || u === '%hr') {
    // HR is sport-specific: bike vs run thresholds differ; swim has no
    // canonical LTHR. Returning null for unsupported sports avoids leaking
    // a run-bpm number onto a Swim HR step (the canonical Swim target is
    // pace, not HR — but this guards the edge case).
    let lthr: number | null = null
    if (sport === 'Ride') lthr = t.lthr_bike
    else if (sport === 'Run') lthr = t.lthr_run
    if (!lthr) return null
    const lo = Math.round((target.start * lthr) / 100)
    const hi = Math.round((end * lthr) / 100)
    return lo === hi ? `${lo} bpm` : `${lo}-${hi} bpm`
  }
  if (u.includes('pace')) {
    // Pace target percentages: 100% = threshold pace. Higher % = faster.
    if (sport === 'Run') {
      const thr = t.threshold_pace_run_sec_per_km
      if (!thr) return null
      const slow = (thr * 100) / target.start
      const fast = (thr * 100) / end
      const f = fmtPace(fast)
      const s = fmtPace(slow)
      if (!f || !s) return null
      return f === s ? `${f}/km` : `${f}–${s}/km`
    }
    if (sport === 'Swim') {
      const thr = t.css_sec_per_100m
      if (!thr) return null
      const slow = (thr * 100) / target.start
      const fast = (thr * 100) / end
      const f = fmtPace(fast)
      const s = fmtPace(slow)
      if (!f || !s) return null
      return f === s ? `${f}/100m` : `${f}–${s}/100m`
    }
    return null
  }
  return null
}
