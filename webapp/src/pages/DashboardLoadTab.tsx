import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import { InfoIcon, InfoPanel, PeriodFilter } from '../components/halo'
import { apiFetch } from '../api/client'
import { CHART_COLORS } from '../lib/constants'
import { fmtPace } from '../lib/formatters'
import type {
  ProgressResponse,
  BikeReadinessResponse,
  MarathonShapeResponse,
} from '../api/types'

// ---------------------------------------------------------------------------
// Dashboard "Load" tab — port of the Halo prototype's `tab === 'load'` block
// (direction-b-halo.jsx:3443+). Merges what used to be the standalone
// `/progress` screen into the Trends dashboard.
//
// Layout (top → bottom):
//   1. Endurance Score card — STATIC "Coming soon" placeholder (no backend).
//   2. Sport segmented control (bike / run / swim, local state).
//   3. Swim-only period filter (1m/3m/6m/1y).
//   4. bike/run: Decoupling · Zone Distribution · BikeReadiness / Marathon
//      Shape · EF trend · Cardiac Drift · recent sessions.
//   5. swim: Pace trend · SWOLF trend · recent swims.
//
// Charts are hand-rolled inline SVG (Halo convention — no Chart.js); follows
// the pattern in RecoveryTrend.tsx / SleepTrend.tsx.
//
// Metric vocabulary (EF, decoupling, SWOLF, polarization terms, CTL, sport
// names, "Coming soon") stays literal English — consistent with the Halo
// de-i18n precedent. Translatable chrome goes through i18n keys.
// ---------------------------------------------------------------------------

type Sport = 'bike' | 'run' | 'swim'
type Period = '1m' | '3m' | '6m' | '1y'

// Period → days for the swim `/api/progress` query + the EF/Drift trend
// window. Bike/run trend cards already aggregate a fixed window server-side,
// but we still let the user pick the lookback for the per-session series.
const PERIOD_DAYS: Record<Period, number> = { '1m': 30, '3m': 90, '6m': 180, '1y': 365 }

const CARD = 'rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card'
const EYEBROW = 'text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim'

// Zone-distribution colours — Low (aerobic) genuine green, Mid (threshold)
// amber, High (VO2/anaerobic) coral. Matches the prototype `ZONE` tokens.
const ZONE_FILL = { low: '#16a34a', mid: 'var(--color-amber)', high: 'var(--color-coral)' }

// Aerobic-decoupling traffic light (CLAUDE.md business rules: green <5%,
// amber 5-10%, coral >10%; abs() for negative drift).
function decouplingTone(v: number): string {
  const a = Math.abs(v)
  if (a < 5) return ZONE_FILL.low
  if (a < 10) return 'var(--color-amber)'
  return 'var(--color-coral)'
}

// Per-session drift semaphore for the recent-sessions list (prototype uses a
// tighter <2% inner band before the amber step).
function driftTone(v: number): string {
  const a = Math.abs(v)
  if (a < 2) return ZONE_FILL.low
  if (a < 10) return 'var(--color-amber)'
  return 'var(--color-coral)'
}

const sportApi = (s: Sport): string => (s === 'bike' ? 'ride' : s)

// `MM-DD` for chart x-axis labels and recent-session rows.
function shortDate(iso: string): string {
  return iso.length >= 10 ? iso.slice(5) : iso
}

// ---------------------------------------------------------------------------
// 1. Endurance Score — STATIC placeholder. No backend exists for this metric,
//    so the card renders its chrome (eyebrow + description) with a greyed-out
//    "Coming soon" plate where the gauge/score would sit. No fetch, no
//    drill-down. The rest of the tab is fully data-backed.
// ---------------------------------------------------------------------------
function EnduranceScoreCard() {
  const { t } = useTranslation()
  return (
    <div className={CARD}>
      <div className={EYEBROW}>Endurance Score</div>
      <div className="mt-1.5 text-[12px] leading-[1.5] text-halo-ink-dim">
        {t('load.endurance_desc')}
      </div>
      <div className="mt-3 flex h-[120px] items-center justify-center rounded-chip bg-halo-surface-2">
        <span className="rounded-pill bg-halo-surface px-3 py-1 text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dimmer">
          Coming soon
        </span>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 2. Sport segmented control — bike / run / swim. Local state.
// ---------------------------------------------------------------------------
function SportSegmented({ value, onChange }: { value: Sport; onChange: (s: Sport) => void }) {
  const sports: Sport[] = ['bike', 'run', 'swim']
  return (
    <div className="flex gap-1 rounded-chip bg-halo-surface-2 p-[3px]">
      {sports.map(s => (
        <button
          key={s}
          type="button"
          onClick={() => onChange(s)}
          aria-pressed={value === s}
          className={`flex-1 rounded-[7px] py-2 text-[13px] font-semibold capitalize transition-colors ${
            value === s ? 'bg-halo-surface text-halo-ink shadow-card' : 'bg-transparent text-halo-ink-dim'
          }`}
        >
          {s}
        </button>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 3a. Decoupling (last 5) — fixed reading from `/api/progress.decoupling_trend`.
//     No window picker: the backend aggregates the latest qualifying long
//     sessions. Tone follows the aerobic-decoupling ladder.
// ---------------------------------------------------------------------------
function DecouplingCard({ data }: { data: ProgressResponse }) {
  const { t } = useTranslation()
  const [tip, setTip] = useState(false)
  const trend = data.decoupling_trend
  if (!trend) return null
  const tone = decouplingTone(trend.latest.value)
  const stale = trend.latest.days_since > 14
  return (
    <div className={CARD}>
      <div className="flex items-start justify-between gap-3">
        <div className="flex-1">
          <div className="flex items-center text-[15px] font-semibold tracking-[-0.2px]">
            <span>
              Decoupling <span className="font-medium text-halo-ink-dim">(last {trend.last_n})</span>
            </span>
            <InfoIcon open={tip} onClick={() => setTip(v => !v)} />
          </div>
          <div className="mt-1 text-[12px] text-halo-ink-dim">
            Latest: {trend.latest.value.toFixed(1)}% ({trend.latest.date})
            {stale && <span className="ml-1.5 text-halo-ink-dimmer">· stale {trend.latest.days_since}d</span>}
          </div>
          <div className="mt-0.5 text-[12px] text-halo-ink-dim">
            Median {trend.median.toFixed(1)}%
          </div>
        </div>
        <div className="whitespace-nowrap text-[24px] font-bold tracking-[-0.5px]" style={{ color: tone }}>
          {trend.latest.value.toFixed(1)}%
        </div>
      </div>
      {tip && <InfoPanel>{t('load.tip.decoupling')}</InfoPanel>}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 3b. Zone Distribution — polarized-training breakdown over a 1w/2w/4w/8w
//     window. HR ZONES ONLY (`/api/polarization` has no power-zone backend).
//     The prototype's HR/Power toggle is intentionally dropped — see the
//     report. The classification pill is derived from the live mix.
// ---------------------------------------------------------------------------
interface PolarizationWindow {
  low_pct: number
  mid_pct: number
  high_pct: number
  total_hours: number
  n_activities: number
  pattern: string
}
interface PolarizationResponse {
  windows: Record<string, PolarizationWindow>
  // Trend-creep signals (e.g. "grey-zone share rising") — `compute_polarization_trends`.
  signals?: string[]
}

const ZONE_WINDOWS: { key: string; label: string }[] = [
  { key: '7', label: '1w' },
  { key: '14', label: '2w' },
  { key: '28', label: '4w' },
  { key: '56', label: '8w' },
]

const PATTERN_META: Record<string, { label: string; color: string; tint: string }> = {
  polarized: { label: 'Polarized (optimal)', color: ZONE_FILL.low, tint: '#16a34a1f' },
  pyramidal: { label: 'Pyramidal', color: 'var(--color-ink-dim)', tint: 'var(--color-surface-2)' },
  threshold: { label: 'Threshold-heavy', color: 'var(--color-amber)', tint: '#d18b001f' },
  too_easy: { label: 'Too easy', color: 'var(--color-coral)', tint: '#d946401f' },
  too_hard: { label: 'Too hard', color: 'var(--color-coral)', tint: '#d946401f' },
  insufficient_data: { label: 'Not enough data', color: 'var(--color-ink-dimmer)', tint: 'var(--color-surface-2)' },
}

function ZoneDistributionCard({ sport }: { sport: 'bike' | 'run' }) {
  const { t } = useTranslation()
  const [data, setData] = useState<PolarizationResponse | null>(null)
  const [window, setWindow] = useState('28')
  const [tip, setTip] = useState(false)

  useEffect(() => {
    let cancelled = false
    // `days` is irrelevant here — `/api/polarization` always returns ALL
    // windows (7/14/28/56) in `windows`; the card picks `windows[window]`
    // locally. The query param only sets the (unused) `primary` block.
    apiFetch<PolarizationResponse>(`/api/polarization?sport=${sportApi(sport)}&days=28`)
      .then(d => {
        if (!cancelled) setData(d)
      })
      .catch(e => console.warn('polarization fetch failed:', e))
    return () => {
      cancelled = true
    }
  }, [sport])

  if (!data) return null
  const z = data.windows[window]
  if (!z || z.pattern === 'insufficient_data') return null

  const meta = PATTERN_META[z.pattern] || PATTERN_META.insufficient_data
  const days = { '7': 7, '14': 14, '28': 28, '56': 56 }[window] || 28
  const seg = (pct: number, fill: string, minLabel: number) => (
    <div
      className="flex items-center justify-center text-[12px] font-bold text-white"
      style={{ flex: Math.max(pct, 0.0001), background: fill }}
    >
      {pct >= minLabel ? `${pct}%` : ''}
    </div>
  )

  return (
    <div className={CARD}>
      <div className="flex flex-wrap items-start justify-between gap-2.5">
        <div className="flex items-center text-[15px] font-semibold tracking-[-0.2px]">
          <span>
            HR Zone Distribution <span className="font-medium text-halo-ink-dim">({days}d)</span>
          </span>
          <InfoIcon open={tip} onClick={() => setTip(v => !v)} />
        </div>
        <span
          className="whitespace-nowrap rounded-pill px-2.5 py-1 text-[11px] font-bold tracking-[0.3px]"
          style={{ background: meta.tint, color: meta.color }}
        >
          {meta.label}
        </span>
      </div>
      {tip && <InfoPanel>{t('load.tip.zones')}</InfoPanel>}

      {/* Window pills */}
      <div className="mt-2.5 flex gap-1.5">
        {ZONE_WINDOWS.map(w => (
          <button
            key={w.key}
            type="button"
            onClick={() => setWindow(w.key)}
            aria-pressed={window === w.key}
            className={`rounded-pill px-3 py-[5px] text-[12px] font-semibold uppercase tracking-[0.3px] ${
              window === w.key
                ? 'bg-halo-ink text-white'
                : 'border border-halo-border bg-transparent text-halo-ink-dim'
            }`}
          >
            {w.label}
          </button>
        ))}
      </div>

      {/* Stacked bar */}
      <div className="mt-3.5 flex h-[34px] gap-0.5 overflow-hidden rounded-chip">
        {seg(z.low_pct, ZONE_FILL.low, 5)}
        {seg(z.mid_pct, ZONE_FILL.mid, 5)}
        {seg(z.high_pct, ZONE_FILL.high, 5)}
      </div>

      {/* Legend + totals */}
      <div className="mt-3 flex flex-wrap items-center justify-between gap-2 text-[12px] text-halo-ink-dim">
        <div className="flex flex-wrap gap-3">
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm" style={{ background: ZONE_FILL.low }} />
            Low {z.low_pct}%
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm" style={{ background: ZONE_FILL.mid }} />
            Mid {z.mid_pct}%
          </span>
          <span className="inline-flex items-center gap-1.5">
            <span className="h-2 w-2 rounded-sm" style={{ background: ZONE_FILL.high }} />
            High {z.high_pct}%
          </span>
        </div>
        <span className="whitespace-nowrap font-medium">
          {z.total_hours}h · {z.n_activities} sessions
        </span>
      </div>

      {/* Trend-creep signals from the backend (e.g. grey-zone share rising). */}
      {(data.signals ?? []).length > 0 && (
        <div className="mt-3 flex flex-col gap-1.5 border-t border-halo-border pt-3">
          {(data.signals ?? []).map((s, i) => (
            <div key={i} className="flex items-start gap-2 text-[12px] leading-snug text-halo-ink">
              <span aria-hidden="true" style={{ color: 'var(--color-amber)' }}>
                ⚠
              </span>
              <span>{s}</span>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 3c. Bike Readiness — 3-signal model (Volume / Long ride / Durability) with
//     a distance-aware CTL target. Computation ported from Progress.tsx's
//     `BikeReadinessWidget`; rendering rebuilt as a Halo card with an inline
//     SVG CTL-trend chart.
// ---------------------------------------------------------------------------
type BRDistance = 'Olympic' | '70.3' | 'IM'
const BR_DISTANCES: BRDistance[] = ['Olympic', '70.3', 'IM']
const BR_TARGETS: Record<BRDistance, { ctl: number; longRideH: number }> = {
  Olympic: { ctl: 35, longRideH: 1.5 },
  '70.3': { ctl: 50, longRideH: 3.0 },
  IM: { ctl: 80, longRideH: 5.0 },
}
const BR_RATIO_GREEN = 1.0
const BR_RATIO_YELLOW = 0.8
type Signal = 'green' | 'yellow' | 'red' | 'insufficient'
const SIGNAL_COLOR: Record<Signal, string> = {
  green: 'var(--color-status-green)',
  yellow: 'var(--color-status-yellow)',
  red: 'var(--color-status-red)',
  insufficient: 'var(--color-ink-dim)',
}
const VERDICT_COLOR = {
  ready: 'var(--color-status-green)',
  almost: 'var(--color-status-yellow)',
  building: 'var(--color-status-red)',
  unknown: 'var(--color-ink-dim)',
} as const

function ratioToSignal(ratio: number | null): Signal {
  if (ratio === null) return 'insufficient'
  if (ratio >= BR_RATIO_GREEN) return 'green'
  if (ratio >= BR_RATIO_YELLOW) return 'yellow'
  return 'red'
}

// Decimal hours → "H:MM" (2.25 → "2:15").
function formatHoursHM(hours: number): string {
  if (!Number.isFinite(hours) || hours < 0) return '—'
  const total = Math.round(hours * 60)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}`
}

// One signal row — coloured dot + label + actual/target value + optional %.
function SignalRow({
  signal,
  label,
  value,
  pct,
  subLine,
}: {
  signal: Signal
  label: string
  value: string
  pct: number | null
  subLine?: string | null
}) {
  return (
    <div>
      <div className="flex items-center justify-between text-[13px]">
        <span className="flex items-center gap-2.5">
          <span className="h-2.5 w-2.5 rounded-full" style={{ background: SIGNAL_COLOR[signal] }} />
          <span className="font-medium text-halo-ink">{label}</span>
        </span>
        <span className="font-mono text-[12px] text-halo-ink-dim">
          {value}
          {pct !== null && <span className="ml-1.5">({pct}%)</span>}
        </span>
      </div>
      {subLine && <div className="ml-[22px] mt-0.5 text-[11px] text-halo-ink-dim">{subLine}</div>}
    </div>
  )
}

// Inline SVG line chart with a dashed target line + label badge. Shared by
// Bike Readiness (CTL trend) and Marathon Shape (MS trend).
function TargetLineChart({
  title,
  values,
  labels,
  target,
  targetLabel,
  lineColor,
  targetColor,
  yMin,
  yMax,
}: {
  title: string
  values: (number | null)[]
  labels: string[]
  target: number
  targetLabel: string
  lineColor: string
  targetColor: string
  yMin: number
  yMax: number
}) {
  const W = 320
  const H = 200
  const pad = { l: 30, r: 14, t: 14, b: 26 }
  const innerW = W - pad.l - pad.r
  const innerH = H - pad.t - pad.b
  const N = values.length
  const span = yMax - yMin || 1

  const xOf = (i: number) => pad.l + (N <= 1 ? innerW / 2 : (i / (N - 1)) * innerW)
  const yOf = (v: number) => pad.t + innerH - ((v - yMin) / span) * innerH

  // Polyline through non-null points only.
  let d = ''
  let first = -1
  let last = -1
  values.forEach((v, i) => {
    if (v == null) return
    d += (first < 0 ? 'M ' : ' L ') + xOf(i).toFixed(1) + ' ' + yOf(v).toFixed(1)
    if (first < 0) first = i
    last = i
  })
  const area =
    first >= 0
      ? `${d} L ${xOf(last).toFixed(1)} ${(pad.t + innerH).toFixed(1)} L ${xOf(first).toFixed(1)} ${(pad.t + innerH).toFixed(1)} Z`
      : ''

  // 5 evenly-spaced y ticks.
  const yTicks = [0, 1, 2, 3, 4].map(i => yMin + (i * span) / 4)
  // ~6 evenly-spaced x labels.
  const xLabels: { i: number; label: string }[] = []
  if (N > 0) {
    const cnt = Math.min(6, N)
    for (let k = 0; k < cnt; k++) {
      const idx = cnt === 1 ? 0 : Math.round((k * (N - 1)) / (cnt - 1))
      xLabels.push({ i: idx, label: labels[idx] })
    }
  }

  const targetClamped = Math.max(yMin, Math.min(yMax, target))
  const ty = yOf(targetClamped)

  return (
    <div className="mt-3.5 rounded-chip border border-halo-border bg-halo-bg p-3.5">
      <div className="text-center text-[13px] font-bold tracking-[-0.1px]">{title}</div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" className="mt-2.5 block">
        {yTicks.map((tick, i) => (
          <g key={i}>
            <line
              x1={pad.l}
              y1={yOf(tick)}
              x2={pad.l + innerW}
              y2={yOf(tick)}
              stroke="var(--color-border)"
              strokeWidth="1"
            />
            <text x={pad.l - 6} y={yOf(tick) + 3} fontSize="9" textAnchor="end" fill="var(--color-ink-dim)">
              {Math.round(tick)}
            </text>
          </g>
        ))}

        {/* Target dashed line + badge */}
        <line
          x1={pad.l}
          y1={ty}
          x2={pad.l + innerW}
          y2={ty}
          stroke={targetColor}
          strokeWidth="2"
          strokeDasharray="6 4"
        />
        <g>
          <rect x={pad.l + innerW - 116} y={ty - 10} width="116" height="20" rx="4" fill={targetColor} />
          <text
            x={pad.l + innerW - 58}
            y={ty + 4}
            fontSize="10"
            fontWeight="700"
            fill="#fff"
            textAnchor="middle"
          >
            {targetLabel}
          </text>
        </g>

        {first >= 0 && (
          <>
            <path d={area} fill={lineColor} fillOpacity="0.13" />
            <path
              d={d}
              fill="none"
              stroke={lineColor}
              strokeWidth="2.4"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            {values.map((v, i) =>
              v == null ? null : <circle key={i} cx={xOf(i)} cy={yOf(v)} r="3.2" fill={lineColor} />,
            )}
          </>
        )}

        {xLabels.map((l, i) => (
          <text
            key={i}
            x={xOf(l.i)}
            y={H - 8}
            fontSize="9"
            fill="var(--color-ink-dim)"
            textAnchor={i === 0 ? 'start' : i === xLabels.length - 1 ? 'end' : 'middle'}
          >
            {l.label}
          </text>
        ))}
      </svg>
    </div>
  )
}

function BikeReadinessCard() {
  const { t } = useTranslation()
  const [data, setData] = useState<BikeReadinessResponse | null>(null)
  const [distance, setDistance] = useState<BRDistance>('70.3')
  const [tip, setTip] = useState(false)

  useEffect(() => {
    let cancelled = false
    apiFetch<BikeReadinessResponse>('/api/bike-readiness?weeks=12')
      .then(d => {
        if (!cancelled) setData(d)
      })
      .catch(e => console.warn('bike-readiness fetch failed:', e))
    return () => {
      cancelled = true
    }
  }, [])

  if (!data) return null

  const targets = BR_TARGETS[distance]
  const current = data.current_components

  const volumeRatio = current?.ctl_bike != null ? current.ctl_bike / targets.ctl : null
  const longRideRatio =
    current?.longest_ride_hours != null ? current.longest_ride_hours / targets.longRideH : null
  const volumeSignal = ratioToSignal(volumeRatio)
  const longRideSignal = ratioToSignal(longRideRatio)
  const durabilitySignal: Signal =
    !current || current.decoupling_status === null || current.decoupling_n === 0
      ? 'insufficient'
      : current.decoupling_status

  const signals: Signal[] = [volumeSignal, longRideSignal, durabilitySignal]
  const greens = signals.filter(s => s === 'green').length
  const reds = signals.filter(s => s === 'red').length
  const insufficient = signals.filter(s => s === 'insufficient').length
  const available = 3 - insufficient

  let verdictLabel: string
  let verdictColor: string
  let subtext: string | null
  if (insufficient >= 2) {
    verdictLabel = `Not enough data for ${distance}`
    verdictColor = VERDICT_COLOR.unknown
    subtext = null
  } else if (reds >= 1) {
    verdictLabel = `Building for ${distance}`
    verdictColor = VERDICT_COLOR.building
    subtext = `${available - reds} of ${available} signals on track`
  } else if (greens === available) {
    verdictLabel = `Ready for ${distance}`
    verdictColor = VERDICT_COLOR.ready
    subtext = 'All signals on track'
  } else {
    verdictLabel = `Almost ready for ${distance}`
    verdictColor = VERDICT_COLOR.almost
    subtext = `${greens} of ${available} signals on track`
  }

  // Chronological CTL trend for the chart (API returns newest-first).
  const chronological = [...data.weeks].reverse()
  const ctlValues = chronological.map(w => w.ctl_bike)
  const hasChart = ctlValues.some(v => v !== null)
  const maxCtl = Math.max(targets.ctl, ...ctlValues.filter((v): v is number => v != null), 1)

  return (
    <div className={CARD}>
      <div className="flex items-center text-[15px] font-semibold tracking-[-0.2px]">
        <span>Bike Readiness</span>
        <InfoIcon open={tip} onClick={() => setTip(v => !v)} />
      </div>
      {tip && <InfoPanel>{t('load.tip.bike_readiness')}</InfoPanel>}

      {/* Distance selector — green-fill active */}
      <div className="mt-3 flex gap-2">
        {BR_DISTANCES.map(k => (
          <button
            key={k}
            type="button"
            onClick={() => setDistance(k)}
            aria-pressed={distance === k}
            className="rounded-lg border-[1.5px] px-4 py-2 text-[13px] font-semibold transition-colors"
            style={{
              borderColor: distance === k ? ZONE_FILL.low : 'var(--color-border)',
              background: distance === k ? ZONE_FILL.low : 'transparent',
              color: distance === k ? '#fff' : 'var(--color-ink)',
            }}
          >
            {k}
          </button>
        ))}
      </div>

      {/* Verdict headline */}
      <div className="mt-4 text-[18px] font-bold tracking-[-0.3px]" style={{ color: verdictColor }}>
        {verdictLabel}
      </div>
      {subtext && <div className="mt-0.5 text-[13px] text-halo-ink-dim">{subtext}</div>}

      {hasChart && (
        <TargetLineChart
          title="CTL Bike — 12 weeks"
          values={ctlValues}
          labels={chronological.map(w => shortDate(w.week_end))}
          target={targets.ctl}
          targetLabel={`${distance} target ${targets.ctl}`}
          lineColor={ZONE_FILL.low}
          targetColor={ZONE_FILL.low}
          yMin={0}
          yMax={Math.ceil(maxCtl / 5) * 5}
        />
      )}

      {/* 3-signal breakdown */}
      <div className="mt-4 flex flex-col gap-2.5 border-t border-halo-border pt-3.5">
        <SignalRow
          signal={volumeSignal}
          label="Volume"
          value={current?.ctl_bike != null ? `CTL ${Math.round(current.ctl_bike)} / ${targets.ctl}` : 'CTL unavailable'}
          pct={volumeRatio !== null ? Math.round(volumeRatio * 100) : null}
        />
        <SignalRow
          signal={longRideSignal}
          label="Long ride"
          value={
            current?.longest_ride_hours != null
              ? `${formatHoursHM(current.longest_ride_hours)} / ${formatHoursHM(targets.longRideH)}`
              : 'No rides last 28 days'
          }
          pct={longRideRatio !== null ? Math.round(longRideRatio * 100) : null}
        />
        <SignalRow
          signal={durabilitySignal}
          label="Durability"
          value={
            current && current.decoupling_median_pct != null
              ? `Pa:Hr ${current.decoupling_median_pct.toFixed(1)}% (${current.decoupling_n} rides)`
              : 'No valid rides'
          }
          pct={null}
          subLine={
            current?.ef_trend_pct != null
              ? `EF trend ${current.ef_trend_pct >= 0 ? '+' : ''}${current.ef_trend_pct.toFixed(1)}% (12w)`
              : null
          }
        />
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 3d. Marathon Shape — Runalyze-style basic-endurance metric. Computation
//     ported from Progress.tsx's `MarathonShapeWidget`; rendered as a Halo
//     card with an inline SVG MS-trend chart + predicted finish block.
// ---------------------------------------------------------------------------
type MSDistance = '10K' | 'HM' | 'Marathon'
const MS_DISTANCES: MSDistance[] = ['10K', 'HM', 'Marathon']
const MS_DISTANCE_KM: Record<MSDistance, number> = { '10K': 10.0, HM: 21.0975, Marathon: 42.195 }
const MS_DISTANCE_FACTORS: Record<MSDistance, { weekly: number; longjog: number | null }> = {
  '10K': { weekly: 0.26, longjog: null },
  HM: { weekly: 0.57, longjog: 0.69 },
  Marathon: { weekly: 1.0, longjog: 1.0 },
}
const MS_WIDE_CI_THRESHOLD = 0.2
const MS_EXTRAPOLATION_FACTOR = 1.3

// `H:MM:SS` for ≥1h, `M:SS` otherwise.
function formatHMS(sec: number): string {
  if (!(sec > 0)) return '—'
  const total = Math.round(sec)
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  const ss = String(s).padStart(2, '0')
  return h > 0 ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`
}

// `M:SS/km`.
function formatPaceKm(secPerKm: number): string {
  if (!(secPerKm > 0)) return '—'
  const total = Math.round(secPerKm)
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, '0')}/km`
}

function MarathonShapeCard() {
  const { t } = useTranslation()
  const [data, setData] = useState<MarathonShapeResponse | null>(null)
  const [distance, setDistance] = useState<MSDistance>('HM')
  const [tip, setTip] = useState(false)

  useEffect(() => {
    let cancelled = false
    apiFetch<MarathonShapeResponse>('/api/marathon-shape?weeks=12')
      .then(d => {
        if (!cancelled) setData(d)
      })
      .catch(e => console.warn('marathon-shape fetch failed:', e))
    return () => {
      cancelled = true
    }
  }, [])

  if (!data) return null

  const chronological = [...data.weeks].reverse()
  const required = Math.pow(MS_DISTANCE_KM[distance], 1.23)
  const newest = data.weeks[0] ?? null
  const shape = newest?.shape_pct ?? null
  const progressPct = shape !== null ? Math.round((shape / required) * 100) : null

  let badgeLabel = ''
  let badgeColor: string
  if (progressPct === null) {
    badgeColor = VERDICT_COLOR.unknown
  } else if (progressPct >= 100) {
    badgeLabel = `Ready for ${distance}`
    badgeColor = VERDICT_COLOR.ready
  } else if (progressPct >= 80) {
    badgeLabel = `Almost ready for ${distance}`
    badgeColor = VERDICT_COLOR.almost
  } else {
    badgeLabel = `Building for ${distance}`
    badgeColor = VERDICT_COLOR.building
  }

  const current = data.current_components
  const factor = MS_DISTANCE_FACTORS[distance]
  const effTargetWeeklyKm = current ? current.target_weekly_km * factor.weekly : null
  const effTargetLongRunKm =
    current && factor.longjog !== null ? current.displayed_target_long_run_km * factor.longjog : null
  const weeklyPct =
    current && effTargetWeeklyKm ? Math.round((current.actual_weekly_km / effTargetWeeklyKm) * 100) : null
  const longjogPct =
    current && effTargetLongRunKm ? Math.round((current.actual_longjog_km / effTargetLongRunKm) * 100) : null

  const shapeValues = chronological.map(w => w.shape_pct)
  const hasChart = shapeValues.some(v => v !== null)
  const maxShape = Math.max(required, ...shapeValues.filter((v): v is number => v != null), 1)

  const predicted = data.predicted_times?.[distance] ?? null
  const ciSpread = predicted
    ? (predicted.total_sec_ci_high - predicted.total_sec_ci_low) / predicted.total_sec
    : 0
  const wideCi = !!(predicted && ciSpread > MS_WIDE_CI_THRESHOLD)
  const selectedDistanceM = MS_DISTANCE_KM[distance] * 1000
  const isExtrapolated = !!(
    predicted &&
    data.max_run_race_distance_m !== null &&
    selectedDistanceM > data.max_run_race_distance_m * MS_EXTRAPOLATION_FACTOR
  )

  return (
    <div className={CARD}>
      <div className="flex items-center text-[15px] font-semibold tracking-[-0.2px]">
        <span>Marathon Shape</span>
        <InfoIcon open={tip} onClick={() => setTip(v => !v)} />
      </div>
      {tip && <InfoPanel>{t('load.tip.marathon_shape')}</InfoPanel>}

      {/* Distance selector */}
      <div className="mt-3 flex gap-2">
        {MS_DISTANCES.map(k => (
          <button
            key={k}
            type="button"
            onClick={() => setDistance(k)}
            aria-pressed={distance === k}
            className="rounded-lg border-[1.5px] px-4 py-2 text-[13px] font-semibold transition-colors"
            style={{
              borderColor: distance === k ? ZONE_FILL.low : 'var(--color-border)',
              background: distance === k ? ZONE_FILL.low : 'transparent',
              color: distance === k ? '#fff' : 'var(--color-ink)',
            }}
          >
            {k}
          </button>
        ))}
      </div>

      {/* Headline % + label */}
      {progressPct !== null && shape !== null ? (
        <>
          <div className="mt-4 flex flex-wrap items-baseline gap-3">
            <span className="text-[32px] font-bold tracking-[-1px]" style={{ color: badgeColor }}>
              {progressPct}%
            </span>
            <span className="text-sm font-medium" style={{ color: badgeColor }}>
              {badgeLabel}
            </span>
          </div>
          <div className="mt-1 text-[13px] text-halo-ink-dim">
            MS {shape.toFixed(1)} / target {required.toFixed(1)}
          </div>
        </>
      ) : (
        <div className="mt-4 text-[13px] text-halo-ink-dim">
          {newest === null ? 'No data' : 'VO2max unavailable for the most recent week'}
        </div>
      )}

      {/* Predicted block */}
      {predicted && (
        <div className="mt-4 border-t border-halo-border pt-3.5">
          <div className={EYEBROW}>Predicted ({distance})</div>
          <div className="mt-2 flex gap-8">
            <div>
              <div className="text-[12px] text-halo-ink-dim">Time</div>
              <div className="mt-0.5 font-mono text-[18px] font-bold tracking-[-0.3px]">
                {formatHMS(predicted.total_sec)}
              </div>
              <div className="mt-0.5 font-mono text-[11px] text-halo-ink-dim">
                {formatHMS(predicted.total_sec_ci_low)} – {formatHMS(predicted.total_sec_ci_high)}
              </div>
            </div>
            <div>
              <div className="text-[12px] text-halo-ink-dim">Pace</div>
              <div className="mt-0.5 font-mono text-[18px] font-bold tracking-[-0.3px]">
                {formatPaceKm(predicted.pace_sec_per_km)}
              </div>
              <div className="mt-0.5 font-mono text-[11px] text-halo-ink-dim">
                {formatPaceKm(predicted.pace_ci_low)} – {formatPaceKm(predicted.pace_ci_high)}
              </div>
            </div>
          </div>
          {wideCi && (
            <div className="mt-2 text-[11px] italic text-halo-ink-dim">
              Model uncertainty high — limited race history; bands will tighten as more data arrives.
            </div>
          )}
          {isExtrapolated && data.max_run_race_distance_m !== null && (
            <div className="mt-2 text-[11px] italic text-halo-ink-dim">
              Extrapolated — your longest race is {(data.max_run_race_distance_m / 1000).toFixed(1)} km;
              the {distance} prediction goes beyond your training distribution.
            </div>
          )}
        </div>
      )}

      {hasChart && (
        <TargetLineChart
          title="Marathon Shape — 12 weeks"
          values={shapeValues}
          labels={chronological.map(w => shortDate(w.week_end))}
          target={required}
          targetLabel={`${distance} ${required.toFixed(1)}`}
          lineColor="var(--color-amber)"
          targetColor={badgeColor}
          yMin={0}
          yMax={Math.ceil(maxShape / 5) * 5}
        />
      )}

      {/* Components footer */}
      {current && (
        <div className="mt-3.5 flex flex-col gap-1 border-t border-halo-border pt-3.5 text-[12px] text-halo-ink-dim">
          <div className="flex justify-between">
            <span>Weekly volume ({distance})</span>
            <span className="font-mono">
              {current.actual_weekly_km.toFixed(1)} /{' '}
              {effTargetWeeklyKm !== null ? effTargetWeeklyKm.toFixed(1) : '—'} km
              {weeklyPct !== null && <span className="ml-1.5">({weeklyPct}%)</span>}
            </span>
          </div>
          <div className="flex justify-between">
            <span>Long run ({distance})</span>
            <span className="font-mono">
              {effTargetLongRunKm !== null
                ? `${current.actual_longjog_km.toFixed(1)} / ${effTargetLongRunKm.toFixed(1)} km`
                : 'n/a'}
              {longjogPct !== null && <span className="ml-1.5">({longjogPct}%)</span>}
            </span>
          </div>
          <div className="flex justify-between">
            <span>VO2max</span>
            <span className="font-mono">{current.vo2max.toFixed(1)}</span>
          </div>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// 3e. EF trend — weekly Efficiency-Factor area chart. Reads `data.weekly`
//     (bike/run) from `/api/progress`. Delta = first vs last in the window.
// ---------------------------------------------------------------------------
function EfTrendCard({ data, sport }: { data: ProgressResponse; sport: 'bike' | 'run' }) {
  const { t } = useTranslation()
  const [tip, setTip] = useState(false)
  const weekly = (data.weekly ?? []).filter(w => w.ef_mean != null)
  const sportLabel = sport === 'bike' ? 'Bike' : 'Run'

  const vals = weekly.map(w => w.ef_mean as number)
  const N = vals.length
  if (N < 2) {
    return (
      <div className={CARD}>
        <div className="text-[15px] font-semibold tracking-[-0.2px]">EF trend</div>
        <div className="mt-2 text-[13px] text-halo-ink-dim">Efficiency Factor — {sportLabel}</div>
        <div className="mt-3 py-8 text-center text-[13px] text-halo-ink-dim">
          Not enough weekly data yet
        </div>
      </div>
    )
  }

  const first = vals[0]
  const last = vals[N - 1]
  const deltaPct = first ? ((last - first) / first) * 100 : 0
  const improving = deltaPct > 0
  const deltaColor = improving ? ZONE_FILL.low : 'var(--color-coral)'

  const W = 320
  const H = 200
  const pad = { l: 32, r: 10, t: 14, b: 26 }
  const innerW = W - pad.l - pad.r
  const innerH = H - pad.t - pad.b
  const rawMin = Math.min(...vals)
  const rawMax = Math.max(...vals)
  const yMin = Math.floor((rawMin - 0.05) * 10) / 10
  const yMax = Math.ceil((rawMax + 0.05) * 10) / 10
  const span = yMax - yMin || 0.1

  const xOf = (i: number) => pad.l + (N === 1 ? innerW / 2 : (i / (N - 1)) * innerW)
  const yOf = (v: number) => pad.t + innerH - ((v - yMin) / span) * innerH

  const d = vals.map((v, i) => (i === 0 ? 'M ' : ' L ') + xOf(i).toFixed(1) + ' ' + yOf(v).toFixed(1)).join('')
  const area = `${d} L ${xOf(N - 1).toFixed(1)} ${(pad.t + innerH).toFixed(1)} L ${xOf(0).toFixed(1)} ${(pad.t + innerH).toFixed(1)} Z`

  const step = span > 0.6 ? 0.2 : 0.1
  const ticks: number[] = []
  for (let tk = yMin; tk <= yMax + 1e-6; tk += step) ticks.push(+tk.toFixed(2))

  const xLabels: { i: number; label: string }[] = []
  const cnt = Math.min(6, N)
  for (let k = 0; k < cnt; k++) {
    const idx = cnt === 1 ? 0 : Math.round((k * (N - 1)) / (cnt - 1))
    xLabels.push({ i: idx, label: weekly[idx].week.replace(/^\d{4}-/, '') })
  }

  return (
    <div className={CARD}>
      <div className="flex items-center justify-between">
        <div className="flex items-center text-[15px] font-semibold tracking-[-0.2px]">
          <span>EF trend</span>
          <InfoIcon open={tip} onClick={() => setTip(v => !v)} />
        </div>
        <span className="text-[13px] font-bold tracking-[-0.1px]" style={{ color: deltaColor }}>
          {improving ? '↑' : '↓'} {Math.abs(deltaPct).toFixed(1)}%
        </span>
      </div>
      {tip && <InfoPanel>{t('load.tip.ef')}</InfoPanel>}
      <div className="mt-3.5 border-t border-halo-border pt-2.5 text-center text-[13px] font-bold tracking-[-0.1px]">
        Efficiency Factor — {sportLabel}
      </div>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" className="mt-2.5 block">
        {ticks.map((tk, i) => (
          <g key={i}>
            <line
              x1={pad.l}
              y1={yOf(tk)}
              x2={pad.l + innerW}
              y2={yOf(tk)}
              stroke="var(--color-border)"
              strokeWidth="1"
            />
            <text x={pad.l - 6} y={yOf(tk) + 3} fontSize="9" textAnchor="end" fill="var(--color-ink-dim)">
              {tk.toFixed(1)}
            </text>
          </g>
        ))}
        <path d={area} fill={ZONE_FILL.low} fillOpacity="0.13" />
        <path d={d} fill="none" stroke={ZONE_FILL.low} strokeWidth="2.4" strokeLinejoin="round" strokeLinecap="round" />
        {vals.map((v, i) => (
          <circle key={i} cx={xOf(i)} cy={yOf(v)} r="3.2" fill={ZONE_FILL.low} />
        ))}
        {xLabels.map((l, i) => (
          <text
            key={i}
            x={xOf(l.i)}
            y={H - 8}
            fontSize="9"
            fill="var(--color-ink-dim)"
            textAnchor={i === 0 ? 'start' : i === xLabels.length - 1 ? 'end' : 'middle'}
          >
            {l.label}
          </text>
        ))}
      </svg>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 3f. Cardiac Drift — per-session decoupling scatter. Reads `data.activities`
//     (those with a `decoupling` value). Background bands shade the healthy /
//     watch / tired thresholds (±5 / ±10).
// ---------------------------------------------------------------------------
function CardiacDriftCard({ data, sport }: { data: ProgressResponse; sport: 'bike' | 'run' }) {
  const { t } = useTranslation()
  const [tip, setTip] = useState(false)
  const acts = data.activities.filter(a => a.decoupling != null)
  const sportLabel = sport === 'bike' ? 'Bike' : 'Run'
  const N = acts.length

  if (N < 2) {
    return (
      <div className={CARD}>
        <div className="text-center text-[13px] font-bold tracking-[-0.1px]">Cardiac Drift — {sportLabel}</div>
        <div className="mt-3 py-8 text-center text-[13px] text-halo-ink-dim">
          Not enough sessions with drift data
        </div>
      </div>
    )
  }

  const W = 320
  const H = 200
  const pad = { l: 32, r: 10, t: 16, b: 24 }
  const innerW = W - pad.l - pad.r
  const innerH = H - pad.t - pad.b
  const yMin = -30
  const yMax = 30
  const yOf = (v: number) => pad.t + innerH - ((v - yMin) / (yMax - yMin)) * innerH
  const xOf = (i: number) => pad.l + (N <= 1 ? innerW / 2 : (i / (N - 1)) * innerW)

  const ticks = [-30, -20, -10, 0, 10, 20, 30]
  const xLabels: { i: number; label: string }[] = []
  const cnt = Math.min(7, N)
  for (let k = 0; k < cnt; k++) {
    const idx = cnt === 1 ? 0 : Math.round((k * (N - 1)) / (cnt - 1))
    xLabels.push({ i: idx, label: shortDate(acts[idx].date) })
  }

  return (
    <div className={CARD}>
      <div className="flex items-center justify-center text-[13px] font-bold tracking-[-0.1px]">
        <span>Cardiac Drift — {sportLabel}</span>
        <InfoIcon open={tip} onClick={() => setTip(v => !v)} />
      </div>
      {tip && <InfoPanel>{t('load.tip.drift')}</InfoPanel>}
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" className="mt-2.5 block">
        {/* Threshold bands */}
        <rect x={pad.l} y={yOf(30)} width={innerW} height={yOf(10) - yOf(30)} fill={ZONE_FILL.high} opacity="0.1" />
        <rect x={pad.l} y={yOf(10)} width={innerW} height={yOf(5) - yOf(10)} fill={ZONE_FILL.mid} opacity="0.1" />
        <rect x={pad.l} y={yOf(5)} width={innerW} height={yOf(-5) - yOf(5)} fill={ZONE_FILL.low} opacity="0.1" />
        <rect x={pad.l} y={yOf(-5)} width={innerW} height={yOf(-10) - yOf(-5)} fill={ZONE_FILL.mid} opacity="0.1" />
        <rect x={pad.l} y={yOf(-10)} width={innerW} height={yOf(-30) - yOf(-10)} fill={ZONE_FILL.high} opacity="0.1" />

        {ticks.map(tk => (
          <g key={tk}>
            <line
              x1={pad.l}
              y1={yOf(tk)}
              x2={pad.l + innerW}
              y2={yOf(tk)}
              stroke="var(--color-border)"
              strokeWidth="1"
            />
            <text x={pad.l - 6} y={yOf(tk) + 3} fontSize="9" textAnchor="end" fill="var(--color-ink-dim)">
              {tk}%
            </text>
          </g>
        ))}

        {acts.map((a, i) => {
          const v = Math.max(yMin, Math.min(yMax, a.decoupling as number))
          return (
            <circle
              key={i}
              cx={xOf(i)}
              cy={yOf(v)}
              r="4.2"
              fill={driftTone(a.decoupling as number)}
              stroke="#fff"
              strokeWidth="1.2"
            />
          )
        })}

        {xLabels.map((l, i) => (
          <text
            key={i}
            x={xOf(l.i)}
            y={H - 8}
            fontSize="9"
            fill="var(--color-ink-dim)"
            textAnchor={i === 0 ? 'start' : i === xLabels.length - 1 ? 'end' : 'middle'}
          >
            {l.label}
          </text>
        ))}
      </svg>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 3g. Recent sessions — bike/run list with EF + Drift columns, links to the
//     activity detail page. Mirrors the prototype `EfDriftSessionsList`.
// ---------------------------------------------------------------------------
function EfDriftSessionsList({ data }: { data: ProgressResponse }) {
  const { t } = useTranslation()
  const acts = [...data.activities].reverse().slice(0, 10)
  if (acts.length === 0) return null
  return (
    <div className={CARD}>
      <div className="text-[15px] font-semibold tracking-[-0.2px]">{t('load.recent_sessions')}</div>
      <div className="mt-2.5 flex flex-col">
        {acts.map((s, i) => (
          <Link
            key={s.id}
            to={`/activity/${s.id}`}
            className={`flex items-center justify-between gap-2 py-3 no-underline text-halo-ink ${
              i === 0 ? '' : 'border-t border-halo-border'
            }`}
          >
            <div className="flex items-baseline gap-2.5">
              <span className="whitespace-nowrap text-[13px] font-medium text-halo-ink-dim">{s.date}</span>
              <span className="text-[12px] font-medium text-halo-ink-dimmer">{s.duration_min}min</span>
            </div>
            <div className="flex items-baseline gap-3">
              {s.ef != null && (
                <span className="whitespace-nowrap text-[13px] font-medium text-halo-ink-dim">
                  EF{' '}
                  <span className="text-[14px] font-bold tracking-[-0.2px] text-halo-ink">{s.ef.toFixed(2)}</span>
                </span>
              )}
              {s.decoupling != null && (
                <span className="whitespace-nowrap text-[13px] font-medium text-halo-ink-dim">
                  Drift{' '}
                  <span
                    className="text-[14px] font-bold tracking-[-0.2px]"
                    style={{ color: driftTone(s.decoupling) }}
                  >
                    {s.decoupling.toFixed(1)}%
                  </span>
                </span>
              )}
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 4. Swim trend card — weekly Pace (s/100m) or SWOLF area chart. Reused for
//    both metrics (identical structure, different accessor / colour / unit).
// ---------------------------------------------------------------------------
function SwimTrendCard({
  title,
  subtitle,
  unit,
  deltaUnit,
  weekly,
  color,
  formatValue = (v: number) => v.toFixed(1),
}: {
  title: string
  subtitle?: string
  unit: string
  /** Unit for the delta row; defaults to `unit`. Pace splits them — the
   *  headline reads as «2:11 /100m» (formatter already includes ":") while
   *  the small delta stays in raw seconds — «-7.3 s/100m». */
  deltaUnit?: string
  weekly: { week: string; value: number }[]
  color: string
  /**
   * Optional value formatter for the headline number + y-axis ticks. Default
   * is one-decimal float (matches the original SWOLF + raw-pace rendering).
   * Pass `fmtPace` to render seconds as «m:ss» for swim pace.
   */
  formatValue?: (v: number) => string
}) {
  const dUnit = deltaUnit ?? unit
  const N = weekly.length
  if (N < 2) return null

  const vals = weekly.map(w => w.value)
  const first = vals[0]
  const last = vals[N - 1]
  const deltaAbs = last - first
  const deltaPct = first ? (deltaAbs / first) * 100 : 0
  // Pace + SWOLF are lower-is-better: a drop over the window is improvement.
  const improving = deltaAbs < 0
  const deltaColor = improving ? ZONE_FILL.low : 'var(--color-coral)'

  const W = 320
  const H = 150
  const pad = { l: 32, r: 10, t: 14, b: 22 }
  const innerW = W - pad.l - pad.r
  const innerH = H - pad.t - pad.b
  const rawMin = Math.min(...vals)
  const rawMax = Math.max(...vals)
  const range = rawMax - rawMin || 1
  const yMin = Math.floor(rawMin - range * 0.1)
  const yMax = Math.ceil(rawMax + range * 0.1)
  const span = yMax - yMin || 1

  const xOf = (i: number) => pad.l + (N === 1 ? innerW / 2 : (i / (N - 1)) * innerW)
  const yOf = (v: number) => pad.t + innerH - ((v - yMin) / span) * innerH

  const d = vals.map((v, i) => (i === 0 ? 'M ' : ' L ') + xOf(i).toFixed(1) + ' ' + yOf(v).toFixed(1)).join('')
  const area = `${d} L ${xOf(N - 1).toFixed(1)} ${(pad.t + innerH).toFixed(1)} L ${xOf(0).toFixed(1)} ${(pad.t + innerH).toFixed(1)} Z`

  const ticks = [yMin, Math.round((yMin + yMax) / 2), yMax]
  const labelIdx = N >= 3 ? [0, Math.floor((N - 1) / 2), N - 1] : [0]

  // `weekly[].week` is an ISO-week key (`2026-W12`) — drop the year → `W12`.
  const wkLabel = (iso: string) => iso.replace(/^\d{4}-/, '')

  return (
    <div className={CARD}>
      <div className="flex items-start justify-between gap-2">
        <div>
          <div className={EYEBROW}>{title}</div>
          <div className="mt-1 flex items-baseline gap-1.5">
            <span className="text-[30px] font-semibold tracking-[-1px]">{formatValue(last)}</span>
            <span className="text-[12px] font-medium text-halo-ink-dim">{unit}</span>
          </div>
          {subtitle && <div className="mt-0.5 text-[11px] font-medium text-halo-ink-dimmer">{subtitle}</div>}
        </div>
        <div className="text-right">
          <div className="inline-flex items-center gap-1 text-[13px] font-bold" style={{ color: deltaColor }}>
            <span>{deltaAbs >= 0 ? '↑' : '↓'}</span>
            <span>{Math.abs(deltaPct).toFixed(1)}%</span>
          </div>
          <div className="mt-0.5 text-[10px] font-medium text-halo-ink-dim">
            {deltaAbs >= 0 ? '+' : ''}
            {deltaAbs.toFixed(1)} {dUnit}
          </div>
        </div>
      </div>

      <svg viewBox={`0 0 ${W} ${H}`} width="100%" height={H} preserveAspectRatio="none" className="mt-2 block">
        {ticks.map((v, i) => (
          <g key={i}>
            <line
              x1={pad.l}
              y1={yOf(v)}
              x2={pad.l + innerW}
              y2={yOf(v)}
              stroke="var(--color-border)"
              strokeWidth="1"
            />
            <text x={pad.l - 6} y={yOf(v) + 3} fontSize="9" textAnchor="end" fill="var(--color-ink-dimmer)">
              {formatValue(v)}
            </text>
          </g>
        ))}
        <path d={area} fill={color} opacity="0.1" />
        <path d={d} fill="none" stroke={color} strokeWidth="2.5" strokeLinejoin="round" strokeLinecap="round" />
        <circle cx={xOf(N - 1)} cy={yOf(last)} r="5" fill="#fff" stroke={color} strokeWidth="2.5" />
        {labelIdx.map(i => (
          <text
            key={i}
            x={xOf(i)}
            y={H - 5}
            fontSize="9"
            fill="var(--color-ink-dim)"
            textAnchor={i === 0 ? 'start' : i === N - 1 ? 'end' : 'middle'}
            fontWeight="500"
          >
            {wkLabel(weekly[i].week)}
          </text>
        ))}
      </svg>
    </div>
  )
}

// ---------------------------------------------------------------------------
// 4b. Recent swims — flat list of the last ~10 swims with date, duration,
//     pace and SWOLF. Mirrors the prototype `SwimSessionsList`.
// ---------------------------------------------------------------------------
function SwimSessionsList({ data }: { data: ProgressResponse }) {
  const { t } = useTranslation()
  const acts = [...data.activities].reverse().slice(0, 10)
  if (acts.length === 0) return null
  return (
    <div className={CARD}>
      <div className="text-[15px] font-semibold tracking-[-0.2px]">{t('load.recent_sessions')}</div>
      <div className="mt-2.5 flex flex-col">
        {acts.map((s, i) => (
          <Link
            key={s.id}
            to={`/activity/${s.id}`}
            className={`flex items-center justify-between gap-2 py-3 no-underline text-halo-ink ${
              i === 0 ? '' : 'border-t border-halo-border'
            }`}
          >
            <div className="flex items-baseline gap-2.5">
              <span className="whitespace-nowrap text-[13px] font-medium text-halo-ink-dim">{s.date}</span>
              <span className="text-[12px] font-medium text-halo-ink-dimmer">{s.duration_min}min</span>
            </div>
            <div className="flex items-baseline gap-2.5">
              {s.pace_100m != null && (
                <span className="whitespace-nowrap text-[14px] font-bold tracking-[-0.2px]">
                  {fmtPace(s.pace_100m) ?? Math.round(s.pace_100m).toString()}
                  <span className="text-[11px] font-medium text-halo-ink-dim">/100m</span>
                </span>
              )}
              {s.swolf != null && (
                <span className="whitespace-nowrap text-[11px] font-semibold uppercase tracking-[0.4px] text-halo-ink-dim">
                  SWOLF {Math.round(s.swolf)}
                </span>
              )}
            </div>
          </Link>
        ))}
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// Bike / run trend cards — driven by a single `/api/progress` fetch.
// ---------------------------------------------------------------------------
function BikeRunTrends({ sport }: { sport: 'bike' | 'run' }) {
  const { t } = useTranslation()
  const [data, setData] = useState<ProgressResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(false)
    // Fixed wide window — feeds the Decoupling card, whose "last 5" reading is
    // window-independent. The EF/Drift block below fetches its own
    // period-filtered window so the filter there doesn't shrink Decoupling.
    apiFetch<ProgressResponse>(`/api/progress?sport=${sport}&days=180`)
      .then(d => {
        if (!cancelled) setData(d)
      })
      .catch(e => {
        console.warn('progress fetch failed:', e)
        if (!cancelled) setError(true)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [sport])

  if (loading && !data) return <LoadingSpinner />
  if (error && !data) return <ErrorMessage message={t('wellness.load_error')} />
  if (!data) return null

  return (
    <>
      <DecouplingCard data={data} />
      <ZoneDistributionCard sport={sport} />
      {sport === 'bike' ? <BikeReadinessCard /> : <MarathonShapeCard />}
      <EfDriftBlock sport={sport} />
    </>
  )
}

// EF trend + Cardiac Drift + recent sessions — own 1m/3m/6m/1y period filter
// (prototype `BRangeSegmented`, sits above the EF/Drift pair), driving its own
// `/api/progress` window independently of the Decoupling card above.
function EfDriftBlock({ sport }: { sport: 'bike' | 'run' }) {
  const { t } = useTranslation()
  const [period, setPeriod] = useState<Period>('3m')
  const [data, setData] = useState<ProgressResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(false)
    apiFetch<ProgressResponse>(`/api/progress?sport=${sport}&days=${PERIOD_DAYS[period]}`)
      .then(d => {
        if (!cancelled) setData(d)
      })
      .catch(e => {
        console.warn('progress fetch failed:', e)
        if (!cancelled) setError(true)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [sport, period])

  return (
    <>
      <PeriodFilter value={period} onChange={setPeriod} />
      {loading && !data && <LoadingSpinner />}
      {error && !data && <ErrorMessage message={t('wellness.load_error')} />}
      {data && (
        <>
          <EfTrendCard data={data} sport={sport} />
          <CardiacDriftCard data={data} sport={sport} />
          <EfDriftSessionsList data={data} />
        </>
      )}
    </>
  )
}

// ---------------------------------------------------------------------------
// Swim trend cards — driven by a single period-aware `/api/progress` fetch.
// ---------------------------------------------------------------------------
function SwimTrends({ days }: { days: number }) {
  const { t } = useTranslation()
  const [data, setData] = useState<ProgressResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(false)
    apiFetch<ProgressResponse>(`/api/progress?sport=swim&days=${days}`)
      .then(d => {
        if (!cancelled) setData(d)
      })
      .catch(e => {
        console.warn('progress fetch failed:', e)
        if (!cancelled) setError(true)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [days])

  if (loading && !data) return <LoadingSpinner />
  if (error && !data) return <ErrorMessage message={t('wellness.load_error')} />
  if (!data) return null

  const paceWeekly = (data.weekly ?? [])
    .filter(w => w.pace_mean != null)
    .map(w => ({ week: w.week, value: w.pace_mean as number }))
  const swolfWeekly = (data.weekly ?? [])
    .filter(w => w.swolf_mean != null)
    .map(w => ({ week: w.week, value: w.swolf_mean as number }))

  return (
    <>
      {paceWeekly.length >= 2 ? (
        <SwimTrendCard
          title="Pace"
          // Headline unit drops the «s» because `formatValue` already turns
          // the value into «m:ss» (the «s» would mislead — «2:11 s» reads as
          // «2:11 seconds» which is nonsense). Delta keeps «s/100m» because a
          // small 7-second change stays as raw seconds for readability, not
          // «0:07».
          unit="/100m"
          deltaUnit="s/100m"
          weekly={paceWeekly}
          color={CHART_COLORS.swim}
          // `fmtPace` (sec → "m:ss") returns null only for non-positive input —
          // sentinel data which shouldn't reach the weekly aggregate, but fall
          // back to toFixed defensively. Headline + y-axis tick share the
          // formatter so the chart frame agrees with the big number above it.
          formatValue={v => fmtPace(v) ?? v.toFixed(0)}
        />
      ) : (
        <div className={CARD}>
          <div className={EYEBROW}>Pace</div>
          <div className="mt-3 py-8 text-center text-[13px] text-halo-ink-dim">{t('load.no_swim_data')}</div>
        </div>
      )}
      {swolfWeekly.length >= 2 && (
        <SwimTrendCard
          title="SWOLF"
          subtitle="strokes + seconds per length"
          unit=""
          weekly={swolfWeekly}
          color="#7c3aed"
        />
      )}
      <SwimSessionsList data={data} />
    </>
  )
}

// ---------------------------------------------------------------------------
// LoadTab — the export consumed by Dashboard.tsx.
// ---------------------------------------------------------------------------
export default function LoadTab() {
  const [sport, setSport] = useState<Sport>('bike')
  const [swimPeriod, setSwimPeriod] = useState<Period>('3m')

  // The swim view + the bike/run EF-Drift block each own their period filter;
  // the swim one is hoisted here (it drives the whole swim fetch), bike/run's
  // lives inside `EfDriftBlock`.
  return (
    <>
      <EnduranceScoreCard />
      <SportSegmented value={sport} onChange={setSport} />
      {sport === 'swim' && <PeriodFilter value={swimPeriod} onChange={setSwimPeriod} />}
      {sport === 'swim' ? <SwimTrends days={PERIOD_DAYS[swimPeriod]} /> : <BikeRunTrends sport={sport} />}
    </>
  )
}
