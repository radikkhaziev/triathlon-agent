// Endurance Score — composite "where am I in my training cycle" headline
// (5 zones: Detrained → Recovering → Maintaining → Productive → Peaking).
//
// Lives on Wellness home (between Recovery and Training load), with a
// dedicated detail route at `/wellness/endurance`. Spec:
// docs/ENDURANCE_SCORE_SPEC.md. Halo design: direction-b-halo.jsx:3414-3635.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import LoadingSpinner from '../LoadingSpinner'
import ErrorMessage from '../ErrorMessage'
import { InfoIcon, InfoPanel } from './InfoTip'
import StackedBar from './StackedBar'
import { useApi } from '../../hooks/useApi'
import { CHART_COLORS } from '../../lib/constants'
import type { EnduranceScoreResponse, EnduranceZoneId } from '../../api/types'

export const ENDURANCE_MAX = 8000

export type EnduranceZoneDef = {
  id: EnduranceZoneId
  min: number
  color: string
}

// Zone thresholds — spec §3.8. Colors mirror Halo prototype (red → blue).
// SoT note: thresholds + colors are also duplicated in `data/endurance_score.py`
// (`ENDURANCE_ZONES`) and `docs/ENDURANCE_SCORE_SPEC.md` §3.8. Keep all three in
// sync when tuning — the gauge zone (here) must match the server-computed
// `current.zone` (Python). Localized labels live in `load.endurance.zone.{id}`.
export const ENDURANCE_ZONES: EnduranceZoneDef[] = [
  { id: 'detrained',   min: 0,    color: '#ef4444' },
  { id: 'recovering',  min: 3000, color: '#f97316' },
  { id: 'maintaining', min: 4500, color: '#eab308' },
  { id: 'productive',  min: 5500, color: '#22c55e' },
  { id: 'peaking',     min: 6500, color: '#3b82f6' },
]

export function zoneFor(score: number): EnduranceZoneDef {
  let current = ENDURANCE_ZONES[0]
  for (const z of ENDURANCE_ZONES) {
    if (score >= z.min) current = z
  }
  return current
}

const SPORT_COLOR: Record<string, string> = {
  Bike: CHART_COLORS.ride,
  Run: CHART_COLORS.run,
  Swim: CHART_COLORS.swim,
  Other: 'var(--color-ink-dimmer)',
}

// Per-sport display vocabulary matches Training load: Swim → Ride → Run →
// Other, literal English labels (backend's "Bike" renders as "Ride" — same
// thing, project canon is "Ride"). Both locales use the same names since this
// is the established palette and reads identically on home + detail.
const ENDURANCE_SPORT_ORDER: Record<string, number> = { Swim: 0, Bike: 1, Run: 2, Other: 3 }
export const ENDURANCE_SPORT_LABEL: Record<string, string> = {
  Swim: 'Swim',
  Bike: 'Ride',
  Run: 'Run',
  Other: 'Other',
}
const rank = (name: string) => ENDURANCE_SPORT_ORDER[name] ?? 99

export function sortPerSport<T extends { name: string }>(items: T[]): T[] {
  return [...items].sort((a, b) => rank(a.name) - rank(b.name))
}

// %-descending order with the canonical order as a stable tie-breaker, so
// equal shares don't flicker between renders. Used by the BySportCard grid.
export function sortPerSportByShareDesc<T extends { name: string; pct: number }>(items: T[]): T[] {
  return [...items].sort((a, b) => b.pct - a.pct || rank(a.name) - rank(b.name))
}

const CARD = 'rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card'
const EYEBROW = 'text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim'

// 5-segment colored arc gauge. Sweep ~260° (open at the bottom for the big
// score label). Port of `EnduranceGauge` in `direction-b-halo.jsx:3481`,
// retuned for 5 zones + ENDURANCE_MAX=8000.
export function EnduranceGauge({ score, size = 220 }: { score: number; size?: number }) {
  const { t } = useTranslation()
  const cx = size / 2
  const cy = size / 2 + 4
  const r = size / 2 - 14
  const startAngle = -220
  const endAngle = 40
  const totalSweep = endAngle - startAngle
  const segSweep = totalSweep / ENDURANCE_ZONES.length
  const gap = 4

  const polar = (ang: number): [number, number] => {
    const rad = (ang * Math.PI) / 180
    return [cx + r * Math.cos(rad), cy + r * Math.sin(rad)]
  }
  const arcSeg = (a0: number, a1: number) => {
    const [x0, y0] = polar(a0)
    const [x1, y1] = polar(a1)
    const large = a1 - a0 > 180 ? 1 : 0
    return `M ${x0.toFixed(2)} ${y0.toFixed(2)} A ${r} ${r} 0 ${large} 1 ${x1.toFixed(2)} ${y1.toFixed(2)}`
  }

  // Marker placement must use *segment-aware* math, not a linear `score /
  // ENDURANCE_MAX`. The arc is split into 5 equal-width segments (52°), but
  // the zones cover unequal score ranges (1000 for Maintaining/Productive,
  // 1500 for Recovering/Peaking, 3000 for Detrained). A linear marker for
  // 5491 lands at angle −41.5° — visually inside the green Productive
  // segment, even though zoneFor(5491) is Maintaining. Place the marker
  // inside the zone's own segment, proportional to the score's position
  // within the zone's range.
  const zone = zoneFor(score)
  const zoneIndex = ENDURANCE_ZONES.indexOf(zone)
  const zoneMax = ENDURANCE_ZONES[zoneIndex + 1]?.min ?? ENDURANCE_MAX
  const zoneSpan = Math.max(1, zoneMax - zone.min)
  const withinZone = Math.max(0, Math.min(1, (score - zone.min) / zoneSpan))
  const markerAngle =
    startAngle + zoneIndex * segSweep + gap / 2 + withinZone * (segSweep - gap)
  const [mx, my] = polar(markerAngle)

  // Height of the SVG box — large enough to fit the arc's lowest point + the
  // marker radius + a few px of breathing room. Without this the gauge clips.
  const arcBottom = cy + r * Math.sin((endAngle * Math.PI) / 180)
  const boxH = arcBottom + 14

  return (
    <svg width={size} height={boxH} viewBox={`0 0 ${size} ${boxH}`} style={{ display: 'block' }}>
      {ENDURANCE_ZONES.map((zn, i) => {
        const a0 = startAngle + i * segSweep + gap / 2
        const a1 = startAngle + (i + 1) * segSweep - gap / 2
        return <path key={zn.id} d={arcSeg(a0, a1)} fill="none" stroke={zn.color} strokeWidth="11" strokeLinecap="round" />
      })}
      <circle cx={mx} cy={my} r="9" fill={zone.color} stroke="#fff" strokeWidth="3" />
      <text
        x={cx}
        y={cy + 2}
        textAnchor="middle"
        fontSize="42"
        fontWeight="600"
        fill="var(--color-ink)"
        letterSpacing="-1.5"
      >
        {score.toLocaleString('en-US').replace(/,/g, ' ')}
      </text>
      <text x={cx} y={cy + 28} textAnchor="middle" fontSize="13" fill="var(--color-ink-dim)" fontWeight="500">
        {t(`load.endurance.zone.${zone.id}`)}
      </text>
    </svg>
  )
}

// Tinted milestone-badge plate under the gauge. Rendered only when the API
// returns a non-null `badge`. Plate background is a soft tint of the current
// zone color (8% alpha) — matches spec §3.9 designer-note styling.
export function BadgePlate({ icon, label, zoneColor }: { icon: string; label: string; zoneColor: string }) {
  return (
    <div
      className="mx-auto mt-2 flex items-center gap-2 rounded-pill px-3 py-1.5 text-[12px] font-semibold"
      style={{
        background: `${zoneColor}14`,
        border: `1px solid ${zoneColor}40`,
        color: 'var(--color-ink)',
        width: 'fit-content',
      }}
    >
      <span>{icon}</span>
      <span>{label}</span>
    </div>
  )
}

// Card placed on Wellness home between Recovery hero and Training load.
// Tap → /wellness/endurance for the full-screen detail (zone bands + trend).
// Owns its own fetch — period `3m` is arbitrary (the card ignores `trend`).
export function EnduranceScoreCard() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [tipOpen, setTipOpen] = useState(false)
  const { data, error } = useApi<EnduranceScoreResponse>('/api/endurance-score?period=3m')

  if (error) {
    return (
      <div className={CARD}>
        <div className={EYEBROW}>{t('load.endurance.title')}</div>
        <div className="mt-3"><ErrorMessage message={error} /></div>
      </div>
    )
  }
  if (!data) {
    return (
      <div className={CARD}>
        <div className={EYEBROW}>{t('load.endurance.title')}</div>
        <div className="mt-3 flex h-[120px] items-center justify-center">
          <LoadingSpinner />
        </div>
      </div>
    )
  }

  const { current } = data
  const zone = zoneFor(current.score)
  const delta = current.delta_vs_week_ago
  const deltaSign = delta >= 0 ? '+' : ''
  const goDetail = () => navigate('/wellness/endurance')

  if (current.insufficient_data) {
    return (
      <div className={CARD}>
        <div className={EYEBROW}>{t('load.endurance.title')}</div>
        <div className="mt-4 px-2 py-8 text-center text-[13px] text-halo-ink-dim">
          {t('load.endurance.insufficient')}
        </div>
      </div>
    )
  }

  return (
    <div className={`${CARD} pt-[14px]`}>
      <div className="flex items-center">
        <span className={EYEBROW}>{t('load.endurance.title')}</span>
        <InfoIcon open={tipOpen} onClick={() => setTipOpen(v => !v)} />
        <span aria-hidden="true" className="ml-auto text-[15px] leading-none text-halo-ink-dimmer">›</span>
      </div>
      {tipOpen && <InfoPanel>{t('load.endurance.tooltip')}</InfoPanel>}
      <div
        className="mt-2 cursor-pointer"
        onClick={goDetail}
        role="link"
        tabIndex={0}
        aria-label={t('load.endurance.title')}
        onKeyDown={e => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            goDetail()
          }
        }}
      >
        <div className="flex justify-center">
          <EnduranceGauge score={current.score} size={220} />
        </div>
        {current.badge && (
          <div className="flex justify-center">
            <BadgePlate icon={current.badge.icon} label={current.badge.label} zoneColor={zone.color} />
          </div>
        )}
        {!current.badge && (
          <div className="mt-1 text-center text-[12px] text-halo-ink-dim">
            {t('load.endurance.delta_vs_week', { sign: deltaSign, delta })}
          </div>
        )}
        {/* Per-sport breakdown — horizontal stacked bar + inline legend,
            same form (and ordering: Swim → Ride → Run → Other) as Training
            load below. Full %-by-sport grid lives on the detail screen
            (BySportCard). Direction-b-halo.jsx:3591-3609. */}
        <div className="mt-3 border-t border-halo-border pt-[14px]">
          <StackedBar
            segments={sortPerSport(current.per_sport).map(s => ({ flex: s.pct, color: SPORT_COLOR[s.name] }))}
          />
          <div className="mt-1.5 flex flex-wrap items-center justify-between gap-x-2 gap-y-1 text-[10px] font-medium text-halo-ink-dim">
            {sortPerSport(current.per_sport).map(s => (
              <span key={s.name} className="inline-flex items-center gap-1">
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: SPORT_COLOR[s.name] }} />
                {ENDURANCE_SPORT_LABEL[s.name] ?? s.name} {s.pct.toFixed(1)}%
              </span>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}
