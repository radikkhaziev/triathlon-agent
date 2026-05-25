// Endurance Score — composite "where am I in my training cycle" headline
// (5 zones: Detrained → Recovering → Maintaining → Productive → Peaking).
//
// Lives on Wellness home (between Recovery and Training load), with a
// dedicated detail route at `/wellness/endurance`. Spec:
// docs/ENDURANCE_SCORE_SPEC.md. Halo design: direction-b-halo.jsx:3414-3635.
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import LoadingSpinner from '../LoadingSpinner'
import ErrorMessage from '../ErrorMessage'
import { InfoIcon, InfoPanel } from './InfoTip'
import { apiFetch } from '../../api/client'
import { CHART_COLORS } from '../../lib/constants'
import type { EnduranceScoreResponse, EnduranceZoneId } from '../../api/types'

export const ENDURANCE_MAX = 8000

export type EnduranceZoneDef = {
  id: EnduranceZoneId
  labelRu: string
  min: number
  color: string
}

// Zone thresholds — spec §3.8. Colors mirror Halo prototype (red → blue).
export const ENDURANCE_ZONES: EnduranceZoneDef[] = [
  { id: 'detrained',    labelRu: 'Растренирован',    min: 0,    color: '#ef4444' },
  { id: 'recovering',   labelRu: 'Восстанавливаюсь', min: 3000, color: '#f97316' },
  { id: 'maintaining',  labelRu: 'Поддерживаю',      min: 4500, color: '#eab308' },
  { id: 'productive',   labelRu: 'Развиваюсь',       min: 5500, color: '#22c55e' },
  { id: 'peaking',      labelRu: 'На пике',          min: 6500, color: '#3b82f6' },
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

const CARD = 'rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card'
const EYEBROW = 'text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim'

// 5-segment colored arc gauge. Sweep ~260° (open at the bottom for the big
// score label). Port of `EnduranceGauge` in `direction-b-halo.jsx:3481`,
// retuned for 5 zones + ENDURANCE_MAX=8000.
export function EnduranceGauge({ score, size = 220 }: { score: number; size?: number }) {
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

  const t = Math.max(0, Math.min(1, score / ENDURANCE_MAX))
  const markerAngle = startAngle + totalSweep * t
  const [mx, my] = polar(markerAngle)
  const zone = zoneFor(score)

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
        {zone.labelRu}
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
  const [data, setData] = useState<EnduranceScoreResponse | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    apiFetch<EnduranceScoreResponse>('/api/endurance-score?period=3m')
      .then(d => {
        if (!cancelled) setData(d)
      })
      .catch((e: Error) => {
        if (!cancelled) setError(e.message)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (error) {
    return (
      <div className={CARD}>
        <div className={EYEBROW}>Endurance Score</div>
        <div className="mt-3"><ErrorMessage message={error} /></div>
      </div>
    )
  }
  if (!data) {
    return (
      <div className={CARD}>
        <div className={EYEBROW}>Endurance Score</div>
        <div className="mt-3 flex h-[120px] items-center justify-center">
          <LoadingSpinner />
        </div>
      </div>
    )
  }

  const { current } = data
  const zone = zoneFor(current.score)
  const delta = current.delta_vs_week_ago
  const deltaSign = delta > 0 ? '+' : ''

  return (
    <div className={`${CARD} pt-[14px]`}>
      <div className="flex items-center">
        <span className={EYEBROW}>Endurance Score</span>
        <InfoIcon open={tipOpen} onClick={() => setTipOpen(v => !v)} />
        <span className="ml-auto text-[11px] font-medium text-halo-ink-dim">{t('load.endurance.tap_for_trend')}</span>
      </div>
      {tipOpen && <InfoPanel>{t('load.endurance.tooltip')}</InfoPanel>}
      <div
        className="mt-2 cursor-pointer"
        onClick={() => navigate('/wellness/endurance')}
        role="link"
        tabIndex={0}
        onKeyDown={e => {
          if (e.key === 'Enter') navigate('/wellness/endurance')
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
        <div className="mt-3 grid grid-cols-2 gap-x-4 gap-y-2.5 border-t border-halo-border pt-[14px]">
          {current.per_sport.map(s => (
            <div key={s.name}>
              <div className="text-[20px] font-semibold tracking-[-0.6px]">{s.pct.toFixed(1)}%</div>
              <div className="mt-0.5 flex items-center gap-1.5">
                <span className="h-2 w-2 rounded-full" style={{ background: SPORT_COLOR[s.name] }} />
                <span className="text-[12px] font-medium text-halo-ink-dim">{t(`load.endurance.sport.${s.name}`)}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
