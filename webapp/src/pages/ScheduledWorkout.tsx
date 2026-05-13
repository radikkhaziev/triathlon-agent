import { useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import ZoneBar from '../components/ZoneBar'
import { useApi } from '../hooks/useApi'
import { fmtDateShort, fmtDuration, fmtPace, sportLabel, stripWorkoutPrefix } from '../lib/formatters'
import { SPORT_ICONS, ZONE_COLORS } from '../lib/constants'
import type {
  ScheduledWorkoutDetail,
  WorkoutDetailThresholds,
  WorkoutDetailZones,
  WorkoutEnrichment,
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

  return (
    <Layout backTo="/plan" backLabel={t('plan.back_to_list')} hideBottomTabs>
      <div className="py-4 pb-3">
        <div className="text-xl font-bold flex items-center gap-2">
          <span>{icon}</span>
          <span>{name}</span>
          <span className="text-sm text-text-dim ml-1">{sportLabel(data.type)}</span>
        </div>
        <div className="text-[13px] text-text-dim mt-1">{fmtDateShort(data.date, i18n.language)}</div>
      </div>

      <PrimaryStats data={data} t={t} />

      <SecondaryCards enrichment={data.enrichment} />

      <ZoneTimes enrichment={data.enrichment} sport={data.type} />

      {data.steps && data.steps.length > 0 ? (
        <>
          <TimelineChart
            steps={data.steps}
            sport={data.type}
            thresholds={data.thresholds}
            zones={data.zones}
            title={t('plan.timeline_title')}
          />
          <div className="bg-surface border border-border rounded-xl px-3.5 py-3 mb-4">
            <div className="text-sm font-bold mb-2 pb-1 border-b border-border">{t('plan.steps_title')}</div>
            <StepList steps={data.steps} sport={data.type} thresholds={data.thresholds} />
          </div>
        </>
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

      {data.rationale && (
        <div className="bg-surface border border-border rounded-xl px-3.5 py-3 mb-4">
          <div className="text-sm font-bold mb-2 pb-1 border-b border-border">{t('plan.rationale_title')}</div>
          <p className="text-[13px] leading-relaxed text-text-dim m-0 whitespace-pre-wrap">{data.rationale}</p>
        </div>
      )}
    </Layout>
  )
}

/**
 * Top-of-page metric strip mirroring Intervals.icu's workout-detail header.
 * Four labels in one row: Duration · Distance · Load (TSS) · Intensity (IF%).
 * Cells with no data render as «—» so the layout doesn't shift across workouts.
 */
function PrimaryStats({
  data,
  t,
}: {
  data: ScheduledWorkoutDetail
  t: (key: string) => string
}) {
  const e = data.enrichment
  const cells: { label: string; value: string }[] = [
    { label: t('plan.stat_duration'), value: data.duration || '—' },
    { label: t('plan.stat_distance'), value: data.distance_km != null ? `${data.distance_km.toFixed(1)} km` : '—' },
    { label: t('plan.stat_load'), value: e.tss != null ? e.tss.toFixed(0) : '—' },
    {
      label: t('plan.stat_intensity'),
      value: e.intensity_pct != null ? `${Math.round(e.intensity_pct)}%` : '—',
    },
  ]
  return (
    <div className="bg-surface border border-border rounded-xl px-3.5 py-3 mb-3 grid grid-cols-4 gap-2">
      {cells.map(c => (
        <div key={c.label} className="min-w-0">
          <div className="text-[11px] text-text-dim uppercase tracking-wide truncate">{c.label}</div>
          <div className="text-base font-bold mt-0.5">{c.value}</div>
        </div>
      ))}
    </div>
  )
}

/**
 * Secondary derived metrics (NP / VI / PI) — kept separate from the primary
 * strip because they're sport-specific and analysis-oriented; an athlete
 * scanning the page wants Load/Intensity prominent, NP/VI/PI on-demand.
 */
function SecondaryCards({ enrichment: e }: { enrichment: WorkoutEnrichment }) {
  const cards: { label: string; value: string }[] = []
  if (e.normalized_power != null && e.normalized_power > 0) {
    cards.push({ label: 'NP', value: `${Math.round(e.normalized_power)}W` })
  }
  if (e.variability_index != null) {
    cards.push({ label: 'VI', value: e.variability_index.toFixed(2) })
  }
  if (e.polarization_index != null && e.polarization_index > 0) {
    cards.push({ label: 'PI', value: e.polarization_index.toFixed(2) })
  }
  if (cards.length === 0) return null
  return (
    <div className="grid grid-cols-3 gap-2 mb-4">
      {cards.map(c => (
        <div key={c.label} className="bg-surface border border-border rounded-xl px-3 py-2">
          <div className="text-[10px] text-text-dim uppercase tracking-wide">{c.label}</div>
          <div className="text-base font-bold">{c.value}</div>
        </div>
      ))}
    </div>
  )
}

function ZoneTimes({
  enrichment: e,
  sport,
}: {
  enrichment: WorkoutEnrichment
  sport: string | null
}) {
  const { t } = useTranslation()
  if (!e.zone_times || e.zone_times.length === 0) return null
  // Server emits `[{id: "Z1", secs: 0}, …]` — and sometimes an extra `SS`
  // (Sweet Spot, see docs/INTERVALS_WEBHOOKS_RESEARCH.md). Some payloads may
  // also be sparse (missing `Z2`) or out of order. Densify by zone index so
  // ZoneBar's positional [0]=Z1, [1]=Z2, … contract holds regardless of
  // server-side ordering / completeness.
  const byIndex = new Map<number, number>()
  for (const z of e.zone_times) {
    const m = /^Z(\d+)$/.exec(z.id)
    if (!m) continue  // skip SS / non-numeric buckets
    byIndex.set(parseInt(m[1], 10) - 1, z.secs || 0)
  }
  if (byIndex.size === 0) return null
  const maxIdx = Math.max(...byIndex.keys())
  const secs = Array.from({ length: maxIdx + 1 }, (_, i) => byIndex.get(i) || 0)
  if (secs.every(s => s === 0)) return null

  // Zone-times bucketing on Intervals' side follows the sport's default
  // workout metric (Ride→power, Run→HR, Swim→pace). If Claude pushes a Run
  // workout with pace-only targets, the chart's `TimelineChart` will pick
  // pace zones from the steps, but `zone_times` here will still be HR-zone
  // distribution (Intervals' default). Label by sport — matches what
  // Intervals actually computed. No per-workout `zone_times_kind` field in
  // Intervals API to override the heuristic.
  const labelKey =
    sport === 'Ride' ? 'plan.power_zones' : sport === 'Run' ? 'plan.hr_zones' : 'plan.pace_zones'
  return (
    <div className="bg-surface border border-border rounded-xl px-3.5 py-3 mb-4">
      <ZoneBar zones={secs} label={t(labelKey)} size="detail" />
    </div>
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

// ---------------------------------------------------------------------------
// Timeline chart — Intervals-style vertical bars over time, fill = zone colour.
// ---------------------------------------------------------------------------

type FlatStep = {
  step: WorkoutStep
  startSec: number
  durationSec: number
}

/**
 * Flatten repeat groups into a sequential list with cumulative start times.
 * The native-format renderer (data/intervals/dto.py) rejects nested repeats at
 * DTO construction, so we expect depth ≤ 1. Recurse defensively anyway —
 * a hand-edited DB row could carry a nested group and we'd lose the inner
 * steps' durations otherwise, shifting all downstream bars on the X-axis.
 * `cursor` is mutated inside `push` and shared across iterations — that's the
 * intended cumulative-time behaviour.
 */
function flattenSteps(steps: WorkoutStep[]): FlatStep[] {
  const flat: FlatStep[] = []
  let cursor = 0
  const push = (s: WorkoutStep) => {
    const dur = s.duration > 0 ? s.duration : 0
    flat.push({ step: s, startSec: cursor, durationSec: dur })
    cursor += dur
  }
  const walk = (list: WorkoutStep[]) => {
    for (const s of list) {
      if (s.reps && s.steps) {
        for (let i = 0; i < s.reps; i++) walk(s.steps)
      } else {
        push(s)
      }
    }
  }
  walk(steps)
  return flat
}

/**
 * Pick the primary target key for a step based on sport convention.
 * Returns the dict (`{units, start, end?}`) or `null` for rest / unknown.
 */
function primaryTarget(step: WorkoutStep, sport: string | null): WorkoutTarget | null {
  if (sport === 'Ride') return step.power || step.hr || null
  if (sport === 'Run') return step.hr || step.pace || null
  if (sport === 'Swim') return step.pace || null
  return step.power || step.hr || step.pace || null
}

/**
 * Convert a step's target corridor to an absolute Y-axis range in the sport's
 * native unit (W for power, bpm for HR, sec/100m or sec/km for pace). Returns
 * `null` when the threshold is missing or the target is unsupported.
 */
function targetToYRange(
  target: WorkoutTarget,
  sport: string | null,
  t: WorkoutDetailThresholds,
): { lo: number; hi: number; midPct: number; unit: 'W' | 'bpm' | 'sec_per_100m' | 'sec_per_km' } | null {
  const u = target.units.toLowerCase()
  const end = target.end ?? target.start
  const midPct = (target.start + end) / 2
  if (u.includes('ftp')) {
    if (!t.ftp) return null
    return { lo: (target.start * t.ftp) / 100, hi: (end * t.ftp) / 100, midPct, unit: 'W' }
  }
  if (u.includes('lthr') || u === '%hr') {
    const lthr = sport === 'Ride' ? t.lthr_bike : sport === 'Run' ? t.lthr_run : null
    if (!lthr) return null
    return { lo: (target.start * lthr) / 100, hi: (end * lthr) / 100, midPct, unit: 'bpm' }
  }
  if (u.includes('pace')) {
    // Inverted: higher % = faster = smaller seconds.
    if (sport === 'Run') {
      const thr = t.threshold_pace_run_sec_per_km
      if (!thr) return null
      const slow = (thr * 100) / target.start
      const fast = (thr * 100) / end
      return { lo: fast, hi: slow, midPct, unit: 'sec_per_km' }
    }
    if (sport === 'Swim') {
      const thr = t.css_sec_per_100m
      if (!thr) return null
      const slow = (thr * 100) / target.start
      const fast = (thr * 100) / end
      return { lo: fast, hi: slow, midPct, unit: 'sec_per_100m' }
    }
  }
  return null
}

/**
 * Pick zone index (0-based) by walking ascending boundaries until `value <
 * boundaries[i]`. Caller's responsibility to pass `value` and `boundaries` in
 * matching units — absolute bpm for HR zones, `%FTP` for power zones,
 * `%threshold` for pace zones.
 *
 * Returns -1 when boundaries are missing (caller should fall back to a
 * neutral colour, not Z1) — silently bucketing into 0 would hide the
 * cold-start «no zone data» state.
 *
 * Boundary equality picks the upper zone (`< boundaries[i]` not `<=`),
 * matching Intervals.icu convention where boundary values are upper-exclusive
 * of the preceding zone.
 */
function pickZoneIndex(value: number, boundaries: number[] | null): number {
  if (!boundaries || boundaries.length === 0) return -1
  for (let i = 0; i < boundaries.length; i++) {
    if (value < boundaries[i]) return i
  }
  return boundaries.length
}

function TimelineChart({
  steps,
  sport,
  thresholds,
  zones,
  title,
}: {
  steps: WorkoutStep[]
  sport: string | null
  thresholds: WorkoutDetailThresholds
  zones: WorkoutDetailZones
  title: string
}) {
  const flat = flattenSteps(steps)
  if (flat.length === 0) return null
  const totalSec = flat[flat.length - 1].startSec + flat[flat.length - 1].durationSec
  if (totalSec === 0) return null

  // Per-step target → absolute Y range (W / bpm / sec/100m / sec/km).
  const yRangesAll: ReturnType<typeof targetToYRange>[] = flat.map(({ step }) => {
    const tgt = primaryTarget(step, sport)
    return tgt ? targetToYRange(tgt, sport, thresholds) : null
  })

  // Mixed-unit workouts (e.g. Run mixing HR and pace steps) can't share a
  // single Y-axis without misinterpreting one set as the other. Pick the
  // most-frequent unit and skip steps in other units (rendered as gaps).
  type ChartUnit = NonNullable<ReturnType<typeof targetToYRange>>['unit']
  const unitCounts = new Map<ChartUnit, number>()
  for (const r of yRangesAll) {
    if (r) unitCounts.set(r.unit, (unitCounts.get(r.unit) || 0) + 1)
  }
  if (unitCounts.size === 0) return null
  // Sort entries by count desc and take the first key — declaring `unit` via
  // destructuring avoids TS narrowing it to the literal type of an initializer
  // (which would break all `unit === 'X'` comparisons below).
  const sortedUnits: [ChartUnit, number][] = Array.from(unitCounts.entries())
  sortedUnits.sort((a, b) => b[1] - a[1])
  const unit: ChartUnit = sortedUnits[0][0]

  // Mask out steps whose unit doesn't match the chosen one — they render as gaps.
  const yRanges = yRangesAll.map(r => (r && r.unit === unit ? r : null))

  const validRanges = yRanges.filter((r): r is NonNullable<typeof r> => r !== null)
  if (validRanges.length === 0) return null

  const allValues = validRanges.flatMap(r => [r.lo, r.hi])
  // Add 10% padding above/below to avoid bars hugging the chart edges.
  const dataMin = allValues.reduce((a, b) => Math.min(a, b))
  const dataMax = allValues.reduce((a, b) => Math.max(a, b))
  const range = Math.max(dataMax - dataMin, 1)
  const yMin = dataMin - range * 0.1
  const yMax = dataMax + range * 0.1

  // Chart geometry — viewBox so it scales fluidly across screen widths.
  const W = 800
  const H = 160
  const padL = 40
  const padR = 8
  const padT = 8
  const padB = 22
  const chartW = W - padL - padR
  const chartH = H - padT - padB

  const xOf = (sec: number) => padL + (sec / totalSec) * chartW
  // For pace (sec/100m, sec/km): lower sec = faster — render higher (smaller Y).
  // For W / bpm: higher value renders higher. We map dataMin→bottom, dataMax→top
  // in both cases — for pace this means smaller-sec→top, which IS «faster→top».
  // Wait: smaller sec → smaller value relative to range → bottom under naive map.
  // For pace we want INVERTED so faster (small sec) is at top. Detect by unit.
  const inverted = unit === 'sec_per_100m' || unit === 'sec_per_km'
  const yOf = (v: number) => {
    const norm = (v - yMin) / (yMax - yMin) // 0..1, low→0
    return inverted
      ? padT + norm * chartH // small (fast) → small Y → top
      : padT + (1 - norm) * chartH // large → small Y → top
  }

  // Zone-bucket dispatch by UNIT (not sport): a Run workout with pace-only
  // targets needs pace_zones, not hr_zones — selecting by sport would bucket
  // pace-second values against bpm boundaries and colour every bar as Z-max.
  // For HR (absolute bpm) bucket by mid bpm; for power (%FTP) / pace (%threshold)
  // bucket by `midPct` already in matching units.
  const boundaries: number[] | null =
    unit === 'W' ? zones.power :
    unit === 'bpm' ? zones.hr :
    unit === 'sec_per_100m' || unit === 'sec_per_km' ? zones.pace :
    null
  const isHrAbsolute = unit === 'bpm'

  // Y-axis labels: pick 4 evenly-spaced ticks across [yMin, yMax].
  const formatY = (v: number): string => {
    if (unit === 'W') return `${Math.round(v)}W`
    if (unit === 'bpm') return `${Math.round(v)}`
    if (unit === 'sec_per_100m' || unit === 'sec_per_km') return fmtPace(v) || ''
    return v.toFixed(0)
  }
  const tickValues = [yMin, yMin + (yMax - yMin) / 3, yMin + (2 * (yMax - yMin)) / 3, yMax]

  // X-axis labels: every ~5 min if total ≤ 60, else every ~10 min. Always
  // append `totalSec` as the final tick so the actual workout end-time is
  // labelled even when totalSec isn't a multiple of xTickStep — collapse the
  // last-before-end tick if it'd sit within half a step of `totalSec` to
  // avoid overlap.
  const xTickStep = totalSec > 3600 ? 600 : 300
  const xTicks: number[] = []
  for (let s = 0; s <= totalSec; s += xTickStep) xTicks.push(s)
  const last = xTicks[xTicks.length - 1]
  if (last !== totalSec) {
    if (totalSec - last < xTickStep / 2) xTicks.pop()
    xTicks.push(totalSec)
  }
  const fmtTime = (sec: number): string => {
    const m = Math.floor(sec / 60)
    const s = Math.round(sec % 60)
    return s === 0 ? `${m}:00` : `${m}:${String(s).padStart(2, '0')}`
  }

  const ariaLabel = `${title}: ${flat.length} steps, ${fmtDuration(totalSec)}`

  return (
    <div className="bg-surface border border-border rounded-xl px-3.5 py-3 mb-4">
      <div className="text-sm font-bold mb-2 pb-1 border-b border-border">{title}</div>
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto block"
        preserveAspectRatio="none"
        role="img"
        aria-label={ariaLabel}
      >
        {/* Y-axis grid + labels */}
        {tickValues.map((v, i) => (
          <g key={i}>
            <line x1={padL} x2={W - padR} y1={yOf(v)} y2={yOf(v)} stroke="var(--border)" strokeDasharray="2,3" />
            <text x={padL - 4} y={yOf(v) + 3} fontSize="9" textAnchor="end" fill="var(--text-dim)">
              {formatY(v)}
            </text>
          </g>
        ))}
        {/* X-axis labels — skip x=0 (Y-axis is the visual «0»); anchor the
            actual-end tick by `end` so its label stays inside the chart frame
            regardless of array position. */}
        {xTicks.map((sec, i) => {
          if (sec === 0) return null
          return (
            <text
              key={i}
              x={xOf(sec)}
              y={H - 6}
              fontSize="9"
              textAnchor={sec === totalSec ? 'end' : 'middle'}
              fill="var(--text-dim)"
            >
              {fmtTime(sec)}
            </text>
          )
        })}
        {/* Step bars — histogram style: each bar roots at chart bottom and
            extends up to the corridor's high-intensity end. The corridor's
            low end is rendered as a faded base shadow inside the bar. Adjacent
            same-target bars share a baseline and a top edge → continuous
            silhouette. Different-target adjacents create a visible step
            (the «staircase» effect that gives the chart its shape). */}
        {flat.map(({ step, startSec, durationSec }, i) => {
          const r = yRanges[i]
          if (!r || durationSec === 0) {
            // Rest / target-less / zero-duration → render a thin baseline placeholder
            return null
          }
          const x = xOf(startSec)
          const w = Math.max(xOf(startSec + durationSec) - x, 1)
          // For pace (inverted): r.lo = fast (high intensity, top of chart).
          // For power/HR: r.hi = high value (high intensity, top of chart).
          const highValue = inverted ? r.lo : r.hi
          const lowValue = inverted ? r.hi : r.lo
          const yHigh = yOf(highValue)
          const yLow = yOf(lowValue)
          const yBaseline = padT + chartH
          const barH = Math.max(yBaseline - yHigh, 2)
          // Pick zone color. For HR (absolute bpm boundaries), use mid bpm value.
          const bucketValue = isHrAbsolute ? (r.lo + r.hi) / 2 : r.midPct
          const zoneIdx = pickZoneIndex(bucketValue, boundaries)
          // `-1` means boundaries weren't available (cold-start athlete).
          // Fall back to a neutral grey so we don't silently miscolour as Z1.
          const fill =
            zoneIdx === -1
              ? 'var(--text-dim)'
              : ZONE_COLORS[Math.min(zoneIdx, ZONE_COLORS.length - 1)]
          const corridorH = Math.max(yLow - yHigh, 2)
          return (
            <g key={i}>
              {/* Baseline shadow — from corridor low down to chart bottom.
                  Same zone color at low opacity so the bar looks rooted but
                  the corridor still reads as the «main» band. */}
              <rect
                x={x}
                y={yLow}
                width={w}
                height={Math.max(yBaseline - yLow, 0)}
                fill={fill}
                opacity={0.3}
              />
              {/* Corridor — the actual target band, full opacity. */}
              <rect x={x} y={yHigh} width={w} height={corridorH} fill={fill} opacity={0.85} />
              {/* Hidden full-height hit area on top of the stack — owns the
                  tooltip for the whole bar (corridor + baseline shadow). */}
              <rect x={x} y={yHigh} width={w} height={barH} fill="transparent">
                <title>{step.text || '—'} · {fmtDuration(durationSec)}</title>
              </rect>
            </g>
          )
        })}
      </svg>
    </div>
  )
}
