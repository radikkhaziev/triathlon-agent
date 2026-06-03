// Training Strain — Foster monotony/strain + ACWR. A responsive read on «how
// hard is the current build, and is it sustainable» — complements the (sticky)
// Endurance Score and CTL/ATL/TSB by surfacing day-to-day load *variation*.
//
// Lives on Wellness home (under Training load), with a detail route at
// `/wellness/strain`. Backend: data/training_strain.py, GET /api/training-strain.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import LoadingSpinner from '../LoadingSpinner'
import ErrorMessage from '../ErrorMessage'
import { InfoIcon, InfoPanel } from './InfoTip'
import { useApi } from '../../hooks/useApi'
import type { AcwrStatus, StrainZoneId, TrainingStrainResponse } from '../../api/types'

// Zone colors — green → amber → red. Single source of truth on the FE; the
// zone *ids* + selection logic live on the backend (data/training_strain.py
// STRAIN_ZONES + classify_strain). Keep ids in sync; colors are FE-only.
export const STRAIN_ZONE_COLOR: Record<StrainZoneId, string> = {
  calm: '#22c55e',
  building: '#eab308',
  overload: '#ef4444',
}

// ACWR accent — same green/amber/red family, low (detraining) reads neutral.
export const ACWR_STATUS_COLOR: Record<AcwrStatus, string> = {
  low: 'var(--color-ink-dim)',
  sweet: '#22c55e',
  caution: '#eab308',
  danger: '#ef4444',
}

const CARD = 'rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card'
const EYEBROW = 'text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim'

// Monotony fallback scale (when there isn't enough history for percentile
// strain bands) — caution 1.5, danger 2.0, axis tops at the module's cap 2.5.
// Mirrors `data/training_strain.py` MONOTONY_CAUTION/DANGER/CAP. Exported so
// StrainDetail reuses the same constants instead of re-declaring them.
export const MONOTONY_CAUTION = 1.5
export const MONOTONY_DANGER = 2.0
export const MONOTONY_MAX = 2.5

/**
 * Horizontal banded track: green [0..lo] / amber [lo..hi] / red [hi..max],
 * with the current value as a tick in `tickColor`. Mirrors MiniRangeGauge's
 * inline-SVG form, extended with colored zone bands (like the TSB chart).
 */
export function BandGauge({
  value,
  lo,
  hi,
  max,
  tickColor,
}: {
  value: number
  lo: number
  hi: number
  max: number
  tickColor: string
}) {
  const W = 140
  const clamp = (n: number) => Math.max(0, Math.min(1, n))
  const x = (v: number) => clamp(v / max) * W
  const loX = x(lo)
  const hiX = x(hi)
  const tickX = x(value)
  return (
    <div className="relative mt-3 h-7">
      <svg width="100%" height="20" viewBox={`0 0 ${W} 20`} preserveAspectRatio="none">
        <rect x="0" y="7" width={loX} height="6" rx="3" fill={STRAIN_ZONE_COLOR.calm} opacity="0.55" />
        <rect x={loX} y="7" width={Math.max(0, hiX - loX)} height="6" fill={STRAIN_ZONE_COLOR.building} opacity="0.55" />
        <rect x={hiX} y="7" width={Math.max(0, W - hiX)} height="6" rx="3" fill={STRAIN_ZONE_COLOR.overload} opacity="0.55" />
        <rect x={Math.min(W - 2, Math.max(0, tickX - 1))} y="2" width="2.5" height="16" rx="1" fill={tickColor} />
      </svg>
    </div>
  )
}

// Card on Wellness home. Tap → /wellness/strain detail.
export function TrainingStrainCard() {
  const { t } = useTranslation()
  const navigate = useNavigate()
  const [tipOpen, setTipOpen] = useState(false)
  // Smallest period — the card only reads `current` (bands are derived from
  // full-year history server-side regardless of period), so the shortest trend
  // window minimises payload on the Wellness home screen.
  const { data, error } = useApi<TrainingStrainResponse>('/api/training-strain?period=1m')

  if (error) {
    return (
      <div className={CARD}>
        <div className={EYEBROW}>{t('load.strain.title')}</div>
        <div className="mt-3"><ErrorMessage message={error} /></div>
      </div>
    )
  }
  if (!data) {
    return (
      <div className={CARD}>
        <div className={EYEBROW}>{t('load.strain.title')}</div>
        <div className="mt-3 flex h-[120px] items-center justify-center"><LoadingSpinner /></div>
      </div>
    )
  }

  const { current } = data
  const zoneColor = STRAIN_ZONE_COLOR[current.zone]
  const goDetail = () => navigate('/wellness/strain')

  if (current.insufficient_data) {
    return (
      <div className={CARD}>
        <div className={EYEBROW}>{t('load.strain.title')}</div>
        <div className="mt-4 px-2 py-8 text-center text-[13px] text-halo-ink-dim">
          {t('load.strain.insufficient')}
        </div>
      </div>
    )
  }

  // Percentile mode → band the strain value against personal calm/hard
  // thresholds. Fallback mode (thin history) → band the monotony value against
  // the literature thresholds, since there are no personal strain bands yet.
  const percentile = current.bands.source === 'percentile'
  const gauge = percentile
    ? { value: current.strain, lo: current.bands.calm_max, hi: current.bands.hard_min, max: Math.max(current.bands.hard_min * 1.25, current.strain * 1.1, 1) }
    : { value: current.monotony, lo: MONOTONY_CAUTION, hi: MONOTONY_DANGER, max: MONOTONY_MAX }

  const deltaSign = current.weekly_load_delta >= 0 ? '+' : ''
  const acwrColor = current.acwr_status ? ACWR_STATUS_COLOR[current.acwr_status] : 'var(--color-ink-dim)'

  return (
    <div
      className={`${CARD} cursor-pointer pt-[14px] transition-colors hover:bg-halo-surface-2`}
      onClick={goDetail}
      role="link"
      tabIndex={0}
      aria-label={t('load.strain.title')}
      onKeyDown={e => {
        // role="link" → activate on Enter only; Space is button semantics and
        // should keep its default page-scroll behaviour.
        if (e.key === 'Enter' && e.target === e.currentTarget) {
          e.preventDefault()
          goDetail()
        }
      }}
    >
      <div className="flex items-center">
        <span className={EYEBROW}>{t('load.strain.title')}</span>
        {/* The «i» toggles the tooltip — stop propagation so it doesn't also
            navigate to the detail route via the card-wide click handler. */}
        <span onClick={e => e.stopPropagation()}>
          <InfoIcon open={tipOpen} onClick={() => setTipOpen(v => !v)} />
        </span>
        <span aria-hidden="true" className="ml-auto text-[15px] leading-none text-halo-ink-dimmer">›</span>
      </div>
      {tipOpen && <InfoPanel>{t('load.strain.tooltip')}</InfoPanel>}
      <div className="mt-2">
        <div className="flex items-baseline justify-between">
          <span className="text-[22px] font-semibold leading-none" style={{ color: zoneColor }}>
            {t(`load.strain.zone.${current.zone}`)}
          </span>
          <span className="text-[13px] text-halo-ink-dim">
            {t('load.strain.strain_label')} <span className="font-semibold text-halo-ink">{Math.round(current.strain)}</span>
          </span>
        </div>

        <BandGauge {...gauge} tickColor={zoneColor} />
        {!percentile && (
          <div className="mt-0.5 text-[10px] text-halo-ink-dimmer">{t('load.strain.baseline_note')}</div>
        )}

        <div className="mt-3 flex flex-wrap items-center justify-between gap-x-3 gap-y-1 border-t border-halo-border pt-[12px] text-[11px] text-halo-ink-dim">
          <span>
            {t('load.strain.week_load')} <span className="font-semibold text-halo-ink">{Math.round(current.weekly_load)}</span>
            <span className="ml-1 text-halo-ink-dimmer">
              {t('load.strain.week_delta', { sign: deltaSign, delta: Math.round(current.weekly_load_delta) })}
            </span>
          </span>
          <span>
            {t('load.strain.monotony')} <span className="font-semibold text-halo-ink">{current.monotony.toFixed(2)}</span>
          </span>
          {current.acwr != null && (
            <span>
              {t('load.strain.acwr')} <span className="font-semibold" style={{ color: acwrColor }}>{current.acwr.toFixed(2)}</span>
            </span>
          )}
        </div>
      </div>
    </div>
  )
}
