// Training Strain — full-screen detail. Tap from the Wellness home card.
//
// Single-column stack: summary (state word + band gauge + chips) → daily-load
// bar chart (the 7 bars that *explain* the monotony number) → strain trend with
// personal percentile bands + monotony danger line → zone legend.
//
// Backend: GET /api/training-strain?period=. See data/training_strain.py.
import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import {
  Card,
  ChartScrubLine,
  fmtScrubDate,
  InfoIcon,
  InfoPanel,
  PeriodFilter,
  useChartScrubber,
  type PeriodRange,
  type ScrubItem,
} from '../components/halo'
import {
  STRAIN_ZONE_COLOR,
  ACWR_STATUS_COLOR,
  BandGauge,
  MONOTONY_CAUTION,
  MONOTONY_DANGER,
  MONOTONY_MAX,
} from '../components/halo/TrainingStrain'
import { useApi } from '../hooks/useApi'
import { useMeasuredWidth } from '../hooks/useMeasuredWidth'
import type { StrainBands, StrainZoneId, TrainingStrainResponse } from '../api/types'

const ZONE_ORDER: StrainZoneId[] = ['overload', 'building', 'calm']

// Strain zone at a point — mirrors backend classify_strain
// (data/training_strain.py): personal percentiles when bands are derived from
// history, else monotony literature thresholds.
function strainZoneAt(strainVal: number, monotonyVal: number, bands: StrainBands): StrainZoneId {
  if (bands.source === 'percentile') {
    if (strainVal >= bands.hard_min) return 'overload'
    if (strainVal >= bands.calm_max) return 'building'
    return 'calm'
  }
  if (monotonyVal >= MONOTONY_DANGER) return 'overload'
  if (monotonyVal >= MONOTONY_CAUTION) return 'building'
  return 'calm'
}

// Local-TZ short date from `YYYY-MM-DD` (avoids the UTC-midnight day shift).
function fmtMD(iso: string, lang: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  if (!y || !m || !d) return iso
  const date = new Date(y, m - 1, d)
  return new Intl.DateTimeFormat(lang === 'ru' ? 'ru-RU' : 'en-US', { day: '2-digit', month: '2-digit' }).format(date)
}

// Weekday letter (S M T W…) for the daily-load bars.
function fmtDow(iso: string, lang: string): string {
  const [y, m, d] = iso.split('-').map(Number)
  if (!y || !m || !d) return ''
  const date = new Date(y, m - 1, d)
  return new Intl.DateTimeFormat(lang === 'ru' ? 'ru-RU' : 'en-US', { weekday: 'short' }).format(date)
}

export default function StrainDetail() {
  const { t } = useTranslation()
  // Default 1M — the most actionable window (current mesocycle); 3M+ is a
  // drill-down for trend questions, matching LoadDetail's default.
  const [range, setRange] = useState<PeriodRange>('1m')
  const { data, loading, error } = useApi<TrainingStrainResponse>(`/api/training-strain?period=${range}`)

  return (
    <Layout>
      <div className="-mx-4 -mt-4 md:-mb-8 min-h-screen bg-halo-bg px-4 md:px-9 font-sans text-halo-ink">
        <header className="flex items-center justify-between gap-3 px-1 pt-[18px] pb-3.5">
          <Link
            to="/wellness"
            className="inline-flex items-center gap-1.5 py-1.5 pl-1 pr-2.5 text-sm font-medium text-halo-ink-dim no-underline"
          >
            <span className="text-lg leading-none">‹</span> {t('nav.today')}
          </Link>
          <div className="min-w-0 flex-1 text-right md:text-left">
            <div className="truncate text-[15px] font-semibold tracking-[-0.2px] md:text-[20px]">{t('load.strain.title')}</div>
            <div className="hidden text-[13px] text-halo-ink-dim md:block">{t('load.strain.subtitle')}</div>
          </div>
        </header>

        {loading && !data && <LoadingSpinner />}
        {error && !data && <ErrorMessage message={error} />}
        {data && <DetailBody data={data} loading={loading} range={range} onRangeChange={setRange} />}
      </div>
    </Layout>
  )
}

function DetailBody({
  data,
  loading,
  range,
  onRangeChange,
}: {
  data: TrainingStrainResponse
  loading: boolean
  range: PeriodRange
  onRangeChange: (p: PeriodRange) => void
}) {
  const { t, i18n } = useTranslation()
  const lang = i18n.language.startsWith('ru') ? 'ru' : 'en'
  const { current } = data
  // One metric tooltip open at a time (LoadDetail pattern). Panel renders
  // full-width under the 3-col metric grid so it doesn't break the columns.
  const [openTip, setOpenTip] = useState<TipId | null>(null)
  const toggleTip = (k: TipId) => setOpenTip(v => (v === k ? null : k))

  if (current.insufficient_data) {
    return (
      <div className="flex flex-col gap-3.5 pb-6">
        <Card>
          <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">{t('load.strain.title')}</div>
          <div className="mt-4 px-2 py-8 text-center text-[13px] text-halo-ink-dim">{t('load.strain.insufficient')}</div>
        </Card>
      </div>
    )
  }

  const zoneColor = STRAIN_ZONE_COLOR[current.zone]
  const percentile = current.bands.source === 'percentile'
  const gauge = percentile
    ? { value: current.strain, lo: current.bands.calm_max, hi: current.bands.hard_min, max: Math.max(current.bands.hard_min * 1.25, current.strain * 1.1, 1) }
    : { value: current.monotony, lo: MONOTONY_CAUTION, hi: MONOTONY_DANGER, max: MONOTONY_MAX }
  const deltaSign = current.weekly_load_delta >= 0 ? '+' : ''
  const acwrColor = current.acwr_status ? ACWR_STATUS_COLOR[current.acwr_status] : 'var(--color-ink-dim)'

  return (
    <div className="flex flex-col gap-3.5 pb-6 md:gap-[18px]">
      {/* Summary */}
      <Card>
        <div className="flex items-baseline justify-between">
          <span className="text-[26px] font-semibold leading-none" style={{ color: zoneColor }}>
            {t(`load.strain.zone.${current.zone}`)}
          </span>
          <span className="text-[14px] text-halo-ink-dim">
            {t('load.strain.strain_label')} <span className="font-semibold text-halo-ink">{Math.round(current.strain)}</span>
          </span>
        </div>
        <BandGauge {...gauge} tickColor={zoneColor} />
        {!percentile && <div className="mt-0.5 text-[10px] text-halo-ink-dimmer">{t('load.strain.baseline_note')}</div>}
        <div className="mt-4 grid grid-cols-3 gap-3 border-t border-halo-border pt-[14px]">
          <Metric label={t('load.strain.week_load')} value={String(Math.round(current.weekly_load))}
            sub={t('load.strain.week_delta', { sign: deltaSign, delta: Math.round(current.weekly_load_delta) })}
            tipOpen={openTip === 'week_load'} onTip={() => toggleTip('week_load')} />
          <Metric label={t('load.strain.monotony')} value={current.monotony.toFixed(2)}
            tipOpen={openTip === 'monotony'} onTip={() => toggleTip('monotony')} />
          {current.acwr != null && (
            <Metric label={t('load.strain.acwr')} value={current.acwr.toFixed(2)} valueColor={acwrColor}
              sub={current.acwr_status ? t(`load.strain.acwr_status.${current.acwr_status}`) : undefined}
              tipOpen={openTip === 'acwr'} onTip={() => toggleTip('acwr')} />
          )}
        </div>
        {openTip && <InfoPanel>{t(`load.strain.tip.${openTip}`)}</InfoPanel>}
      </Card>

      {/* Daily load bars — the shape that explains monotony. */}
      <Card>
        <div className="text-[15px] font-semibold tracking-[-0.2px]">{t('load.strain.daily_load_title')}</div>
        <DailyLoadBars data={data} lang={lang} />
      </Card>

      {/* Strain trend with personal bands + monotony danger line. */}
      <Card>
        <div className="text-[15px] font-semibold tracking-[-0.2px]">{t('load.strain.trend_title')}</div>
        <div className="mt-2.5"><PeriodFilter value={range} onChange={onRangeChange} /></div>
        {loading ? (
          <div className="mt-3 flex h-[240px] items-center justify-center"><LoadingSpinner /></div>
        ) : (
          <StrainTrendChart data={data} lang={lang} />
        )}
      </Card>

      {/* Zone legend. */}
      <Card>
        <div className="text-[15px] font-semibold tracking-[-0.2px]">{t('load.strain.zones_title')}</div>
        <div className="mt-3 flex flex-col gap-1.5">
          {ZONE_ORDER.map(zid => {
            const isCurrent = zid === current.zone
            const color = STRAIN_ZONE_COLOR[zid]
            return (
              <div
                key={zid}
                className="grid grid-cols-[14px_1fr] items-center gap-3 rounded-chip px-3 py-2.5 md:grid-cols-[14px_160px_1fr] md:gap-5"
                style={{ background: isCurrent ? `${color}14` : 'transparent', border: `1px solid ${isCurrent ? `${color}40` : 'transparent'}` }}
              >
                <span className="h-2.5 w-2.5 rounded-full" style={{ background: color }} />
                <span className="text-[13px] md:text-[14px]" style={{ fontWeight: isCurrent ? 700 : 600, color: isCurrent ? 'var(--color-ink)' : 'var(--color-ink-dim)' }}>
                  {t(`load.strain.zone.${zid}`)}
                </span>
                <span className="hidden text-[13px] text-halo-ink-dim md:block">{t(`load.strain.zone_desc.${zid}`)}</span>
              </div>
            )
          })}
        </div>
      </Card>
    </div>
  )
}

type TipId = 'week_load' | 'monotony' | 'acwr'

function Metric({
  label,
  value,
  sub,
  valueColor,
  tipOpen,
  onTip,
}: {
  label: string
  value: string
  sub?: string
  valueColor?: string
  tipOpen?: boolean
  onTip?: () => void
}) {
  return (
    <div>
      <div className="flex items-center">
        <span className="text-[10px] font-semibold uppercase tracking-[0.4px] text-halo-ink-dim">{label}</span>
        {onTip && <InfoIcon open={!!tipOpen} onClick={onTip} />}
      </div>
      <div className="mt-1 text-[20px] font-semibold tracking-[-0.4px]" style={valueColor ? { color: valueColor } : undefined}>{value}</div>
      {sub && <div className="mt-0.5 text-[10px] text-halo-ink-dimmer">{sub}</div>}
    </div>
  )
}

// 7 daily-TSS SVG bars + weekday labels + value above each bar. Rest days draw
// no bar (just the weekday label) so the recovery «valleys» (what lowers
// monotony) read as gaps. Hover/touch → crosshair + that day's TSS callout.
function DailyLoadBars({ data, lang }: { data: TrainingStrainResponse; lang: string }) {
  const bars = data.daily_load_7d
  const [wrapRef, W] = useMeasuredWidth<HTMLDivElement>(320)
  const H = 150
  const pad = { l: 8, r: 8, t: 18, b: 20 }
  const innerW = Math.max(40, W - pad.l - pad.r)
  const innerH = H - pad.t - pad.b
  const M = bars.length
  const max = Math.max(1, ...bars.map(b => b.tss))
  const slotW = innerW / M
  const barW = Math.max(6, slotW * 0.56)
  const xOf = (i: number) => pad.l + i * slotW + (slotW - barW) / 2
  const yOf = (v: number) => pad.t + innerH - (v / max) * innerH

  const { svgRef, idx: scrubIdx, handlers } = useChartScrubber(M, pad.l, innerW)
  const scrubBar = scrubIdx == null ? null : bars[scrubIdx]
  const scrubItems: ScrubItem[] =
    scrubBar == null ? [] : [{ label: 'TSS', value: Math.round(scrubBar.tss), color: 'var(--color-brand)' }]

  return (
    <div ref={wrapRef} className="mt-3 w-full">
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" height={H} className="block overflow-visible" preserveAspectRatio="none" {...handlers}>
        <line x1={pad.l} y1={yOf(0)} x2={pad.l + innerW} y2={yOf(0)} stroke="var(--color-border)" strokeWidth="1" opacity="0.6" />
        {bars.map((b, i) =>
          b.tss > 0 ? (
            <rect
              key={`b${i}`}
              x={xOf(i)}
              y={yOf(b.tss)}
              width={barW}
              height={Math.max(2, yOf(0) - yOf(b.tss))}
              rx="2"
              fill="var(--color-brand)"
              opacity={i === M - 1 ? 1 : 0.8}
            />
          ) : null,
        )}
        {bars.map((b, i) =>
          b.tss > 0 ? (
            <text key={`v${i}`} x={xOf(i) + barW / 2} y={yOf(b.tss) - 5} fontSize="10" fill="var(--color-ink-dim)" textAnchor="middle" fontWeight="600">
              {Math.round(b.tss)}
            </text>
          ) : null,
        )}
        {bars.map((b, i) => (
          <text key={`d${i}`} x={xOf(i) + barW / 2} y={H - pad.b + 13} fontSize="9" fill="var(--color-ink-dimmer)" textAnchor="middle">
            {fmtDow(b.date, lang)}
          </text>
        ))}
        <rect x={pad.l} y={pad.t} width={innerW} height={innerH} fill="transparent" style={{ cursor: 'crosshair' }} />
        <ChartScrubLine
          idx={scrubIdx}
          dateLabel={fmtScrubDate(scrubBar?.date)}
          items={scrubItems}
          x={i => xOf(i) + barW / 2}
          padT={pad.t}
          innerH={innerH}
          W={W}
          padR={pad.r}
        />
      </svg>
    </div>
  )
}

// Strain line over the period: zone bands shaded behind, the line recoloured
// per-zone run (Sleep-score pattern), zone-boundary y-ticks, and a hover/touch
// scrubber (crosshair + strain/monotony callout).
function StrainTrendChart({ data, lang }: { data: TrainingStrainResponse; lang: string }) {
  const { t } = useTranslation()
  const trend = data.trend
  const N = trend.length
  const [wrapRef, W] = useMeasuredWidth<HTMLDivElement>(360)
  const H = 240
  const pad = { l: 38, r: 14, t: 14, b: 26 }
  const innerW = Math.max(40, W - pad.l - pad.r)
  const innerH = H - pad.t - pad.b

  if (N === 0) {
    return <div className="py-12 text-center text-[13px] text-halo-ink-dim">{t('load.strain.insufficient')}</div>
  }

  const vals = trend.map(p => p.strain)
  const { bands } = data.current
  const percentile = bands.source === 'percentile'
  const yMax = Math.max(...vals, percentile ? bands.hard_min : 0, 1) * 1.1
  const yMin = 0
  const x = (i: number) => pad.l + (N === 1 ? innerW / 2 : (i / (N - 1)) * innerW)
  const y = (v: number) => pad.t + innerH - ((v - yMin) / (yMax - yMin)) * innerH

  // Per-zone line runs (overlap by one point so segments join), like the Sleep
  // score chart.
  const runs: { zone: StrainZoneId; from: number; to: number }[] = []
  let cur: (typeof runs)[number] | null = null
  for (let i = 0; i < N; i++) {
    const z = strainZoneAt(trend[i].strain, trend[i].monotony, bands)
    if (!cur) cur = { zone: z, from: i, to: i }
    else if (cur.zone === z) cur.to = i
    else {
      runs.push(cur)
      cur = { zone: z, from: i - 1, to: i }
    }
  }
  if (cur) runs.push(cur)
  const pathOf = (from: number, to: number) => {
    let d = ''
    for (let i = from; i <= to; i++) d += (i === from ? 'M ' : ' L ') + x(i).toFixed(1) + ' ' + y(vals[i]).toFixed(1)
    return d
  }

  const yTicks = percentile ? [0, bands.calm_max, bands.hard_min] : [0, yMax / 2]
  const labelIdx = N > 2 ? [0, Math.floor((N - 1) / 2), N - 1] : N === 2 ? [0, 1] : [0]

  // Hover/touch scrubber — vertical rule + strain + monotony callout.
  const { svgRef, idx: scrubIdx, handlers } = useChartScrubber(N, pad.l, innerW)
  const sp = scrubIdx == null ? null : trend[scrubIdx]
  const scrubItems: ScrubItem[] =
    sp == null
      ? []
      : [
          {
            label: t('load.strain.strain_label'),
            value: Math.round(sp.strain),
            color: STRAIN_ZONE_COLOR[strainZoneAt(sp.strain, sp.monotony, bands)],
          },
          { label: t('load.strain.monotony'), value: sp.monotony.toFixed(2), color: 'var(--color-ink-dim)' },
        ]

  return (
    <div ref={wrapRef} className="mt-3 w-full">
      <svg ref={svgRef} viewBox={`0 0 ${W} ${H}`} width="100%" height={H} className="block overflow-visible" preserveAspectRatio="none" {...handlers}>
        {/* Personal percentile band shading (calm / building / overload). */}
        {percentile && (
          <>
            <rect x={pad.l} y={y(bands.calm_max)} width={innerW} height={Math.max(0, y(yMin) - y(bands.calm_max))} fill={STRAIN_ZONE_COLOR.calm} opacity="0.07" />
            <rect x={pad.l} y={y(bands.hard_min)} width={innerW} height={Math.max(0, y(bands.calm_max) - y(bands.hard_min))} fill={STRAIN_ZONE_COLOR.building} opacity="0.08" />
            <rect x={pad.l} y={y(yMax)} width={innerW} height={Math.max(0, y(bands.hard_min) - y(yMax))} fill={STRAIN_ZONE_COLOR.overload} opacity="0.08" />
            <line x1={pad.l} y1={y(bands.calm_max)} x2={pad.l + innerW} y2={y(bands.calm_max)} stroke={STRAIN_ZONE_COLOR.building} strokeWidth="1" opacity="0.4" strokeDasharray="3 3" />
            <line x1={pad.l} y1={y(bands.hard_min)} x2={pad.l + innerW} y2={y(bands.hard_min)} stroke={STRAIN_ZONE_COLOR.overload} strokeWidth="1" opacity="0.4" strokeDasharray="3 3" />
            {/* In-band zone labels (Endurance-detail pattern) — current zone
                stronger. Skip a label if its band is too short to fit. */}
            {([
              { id: 'overload', top: y(yMax), bottom: y(bands.hard_min) },
              { id: 'building', top: y(bands.hard_min), bottom: y(bands.calm_max) },
              { id: 'calm', top: y(bands.calm_max), bottom: y(yMin) },
            ] as { id: StrainZoneId; top: number; bottom: number }[]).map(b =>
              b.bottom - b.top >= 16 ? (
                <text
                  key={`zl${b.id}`}
                  x={pad.l + 6}
                  y={b.top + 12}
                  fontSize="10"
                  fontWeight={b.id === data.current.zone ? 700 : 600}
                  fill={STRAIN_ZONE_COLOR[b.id]}
                  opacity={b.id === data.current.zone ? 0.95 : 0.6}
                >
                  {t(`load.strain.zone.${b.id}`)}
                </text>
              ) : null,
            )}
          </>
        )}
        {yTicks.map((v, i) => (
          <text key={`y${i}`} x={pad.l - 6} y={y(v) + 3} fontSize="9" fill="var(--color-ink-dim)" textAnchor="end">
            {Math.round(v)}
          </text>
        ))}
        {runs.map((r, ri) => (
          <path
            key={`r${ri}`}
            d={pathOf(r.from, r.to)}
            fill="none"
            stroke={STRAIN_ZONE_COLOR[r.zone]}
            strokeWidth="2.2"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
        ))}
        <circle cx={x(N - 1)} cy={y(vals[N - 1])} r="4" fill={STRAIN_ZONE_COLOR[data.current.zone]} stroke="#fff" strokeWidth="1.8" />
        {labelIdx.map(i => (
          <text key={`x${i}`} x={x(i)} y={H - 8} fontSize="10" fill="var(--color-ink-dim)" textAnchor={i === 0 ? 'start' : i === N - 1 ? 'end' : 'middle'} fontWeight="500">
            {fmtMD(trend[i].date, lang)}
          </text>
        ))}
        <rect x={pad.l} y={pad.t} width={innerW} height={innerH} fill="transparent" style={{ cursor: 'crosshair' }} />
        <ChartScrubLine
          idx={scrubIdx}
          dateLabel={fmtScrubDate(trend[scrubIdx ?? 0]?.date)}
          items={scrubItems}
          x={x}
          padT={pad.t}
          innerH={innerH}
          W={W}
          padR={pad.r}
        />
      </svg>
    </div>
  )
}
