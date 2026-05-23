import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import Layout from '../components/Layout'
import { Card, ChartScrubLine, fmtScrubDate, PeriodFilter, useChartScrubber, type ScrubItem } from '../components/halo'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import { useApi } from '../hooks/useApi'
import { fmtDateYmd, num } from '../lib/formatters'
import { classifyRecovery, computeRecoveryMeaningStat, RECOVERY_CHIP } from '../utils/recovery'
import type { RecoveryTrendSeries, WellnessResponse } from '../api/types'

/**
 * Recovery trend detail (prototype `BRecoveryDetail` / `BRecoveryTrendChart`,
 * direction-b-halo.jsx:2398-2672). Reached from the "Trend" pill on the
 * Wellness recovery hero. Period filter (1m/3m/6m) → dual-axis line chart:
 * Recovery score (0-100, left axis, tinted area) + HRV/RHR (right axis,
 * lines), each toggleable via the legend.
 *
 * Series come from `/api/recovery-trend` — the same endpoint the Dashboard
 * Load tab already used, extended with `rhr` and a 180-day cap. Today's
 * headline numbers + the HRV/RHR deltas come from `/api/wellness-day`.
 */

type Range = '1m' | '3m' | '6m' | '1y'
const RANGE_DAYS: Record<Range, number> = { '1m': 30, '3m': 90, '6m': 180, '1y': 365 }

// Chart series colours. Recovery violet is deliberately outside the sport
// palette (swim amber / ride cobalt / run coral); HRV amber + RHR cobalt
// echo the metric tiles on /wellness.
const SERIES_COLOR = {
  recovery: '#8b5cf6',
  hrv: 'var(--color-amber)',
  rhr: 'var(--color-brand)',
} as const

type SeriesKey = keyof typeof SERIES_COLOR

const fmtPct = (n: number) => (n >= 0 ? '+' : '') + num(n) + '%'
const fmtDelta = (n: number) => (n >= 0 ? '+' : '') + num(n)

// Last non-null value in a series — the freshest measured point, used for the
// headline snapshot and the chart's "today" endpoint dot.
function lastValid(arr: (number | null)[] | undefined): number | null {
  if (!arr) return null
  for (let i = arr.length - 1; i >= 0; i--) {
    if (arr[i] != null) return arr[i]
  }
  return null
}

export default function RecoveryTrend() {
  const { t, i18n } = useTranslation()
  const lang = i18n.language === 'en' ? 'en' : 'ru'
  const [range, setRange] = useState<Range>('3m')
  // Three independent series toggles. At least one must stay on so the chart
  // never collapses to an empty frame. Recovery score is the primary surface
  // here (это страница «Recovery trend» — HRV/RHR живут на собственных
  // detail-экранах, тут они опциональный наложенный контекст).
  const [vis, setVis] = useState<Record<SeriesKey, boolean>>({ recovery: true, hrv: false, rhr: false })
  const toggle = (k: SeriesKey) =>
    setVis(v => {
      if (v[k] && Object.values(v).filter(Boolean).length === 1) return v
      return { ...v, [k]: !v[k] }
    })

  const pastDays = RANGE_DAYS[range]
  const { data: series, loading, error } = useApi<RecoveryTrendSeries>(`/api/recovery-trend?days=${pastDays}`)
  // Today's snapshot — deltas + updated_at the series doesn't carry. Errors
  // here are non-fatal: the chart is the screen, the headline degrades.
  const today = fmtDateYmd(new Date())
  const { data: wellness } = useApi<WellnessResponse>(`/api/wellness-day?date=${today}`)
  const w = wellness?.has_data ? wellness : null

  const recoveryToday = lastValid(series?.recovery)
  const hrvToday = lastValid(series?.hrv)
  const rhrToday = lastValid(series?.rhr)
  const cat = recoveryToday != null ? classifyRecovery(recoveryToday) : null
  const catLabel = cat ? RECOVERY_CHIP[lang][cat].label : null
  const hrvDelta = w?.hrv?.delta_pct ?? null
  const rhrDelta = w?.rhr?.delta_30d ?? null
  const updatedTime = w?.updated_at
    ? new Date(w.updated_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    : null

  const headline: { key: SeriesKey; label: string; value: string; unit: string; sub: string | null }[] = [
    {
      key: 'recovery',
      label: 'Recovery',
      value: recoveryToday != null ? String(Math.round(recoveryToday)) : '—',
      unit: '/100',
      sub: catLabel,
    },
    {
      key: 'hrv',
      label: 'HRV',
      value: hrvToday != null ? num(hrvToday, 1) : '—',
      unit: ' ms',
      sub: hrvDelta != null ? `${fmtPct(hrvDelta)} · 7d` : null,
    },
    {
      key: 'rhr',
      label: 'RHR',
      value: rhrToday != null ? String(Math.round(rhrToday)) : '—',
      unit: ' bpm',
      sub: rhrDelta != null ? `${fmtDelta(rhrDelta)} · 30d` : null,
    },
  ]

  return (
    <Layout maxWidth="480px">
      <div className="-mx-4 -mt-4 md:-mb-8 min-h-screen bg-halo-bg px-4 md:px-9 font-sans text-halo-ink">
        <header className="flex items-center justify-between px-1 pt-[18px] pb-2.5">
          <Link
            to="/wellness"
            className="inline-flex items-center gap-1.5 py-1.5 pl-1 pr-2.5 text-sm font-medium text-halo-ink-dim no-underline"
          >
            <span className="text-lg leading-none">‹</span> {t('nav.today')}
          </Link>
          {updatedTime && (
            <span className="pr-1 text-xs text-halo-ink-dim">{t('recovery_trend.updated', { time: updatedTime })}</span>
          )}
        </header>

        {loading && !series && <LoadingSpinner />}
        {error && !series && <ErrorMessage message={t('wellness.load_error')} />}

        {series && (
          <div className="flex flex-col gap-3.5 pb-6">
            <div>
              <div className="text-[22px] font-semibold tracking-[-0.4px]">{t('recovery_trend.title')}</div>
              <div className="mt-0.5 text-[13px] text-halo-ink-dim">
                {t('recovery_trend.subtitle', { days: pastDays })}
              </div>
            </div>

            {/* Headline — today's snapshot, colour-keyed to the chart series. */}
            <Card>
              <div className="grid grid-cols-3 gap-3">
                {headline.map(m => (
                  <div key={m.key}>
                    <div className="inline-flex items-center gap-1.5">
                      <span className="h-2 w-2 rounded-sm" style={{ background: SERIES_COLOR[m.key] }} />
                      <span className="text-[11px] font-semibold text-halo-ink-dim">{m.label}</span>
                    </div>
                    <div className="mt-1 text-[24px] font-semibold tracking-[-0.5px] text-halo-ink">
                      {m.value}
                      <span className="text-[11px] font-medium text-halo-ink-dim">{m.unit}</span>
                    </div>
                    <div className="mt-px h-3 text-[9px] font-semibold uppercase tracking-[0.4px] text-halo-ink-dimmer">
                      {m.sub ?? ''}
                    </div>
                  </div>
                ))}
              </div>
            </Card>

            {/* Period filter */}
            <PeriodFilter value={range} onChange={setRange} />

            {/* Trend chart — Recovery area on the left axis, HRV/RHR lines on
                the right. The legend below toggles each series. */}
            <Card>
              <div className="mb-1.5 text-center text-[13px] font-semibold text-halo-ink">Recovery, HRV &amp; RHR</div>
              {series.dates.length === 0 ? (
                <div className="py-12 text-center text-[13px] text-halo-ink-dim">{t('recovery_trend.no_data')}</div>
              ) : (
                <>
                  <RecoveryTrendChart series={series} show={vis} />
                  <div className="mt-2 flex flex-wrap justify-center gap-1.5">
                    <LegendToggle on={vis.recovery} color={SERIES_COLOR.recovery} label="Recovery score" onClick={() => toggle('recovery')} />
                    <LegendToggle on={vis.hrv} color={SERIES_COLOR.hrv} label="HRV" onClick={() => toggle('hrv')} />
                    <LegendToggle on={vis.rhr} color={SERIES_COLOR.rhr} label="RHR" onClick={() => toggle('rhr')} />
                  </div>
                </>
              )}
            </Card>

            {/* «Что это значит» — period-aware factual interpretation.
                Frontend-only (series-data уже есть, новый endpoint не нужен);
                same lavender chrome as MetricDetail's HRV/RHR meaning card
                for visual consistency. Template selection by today's recovery
                category (fallback на `no_today` если последний non-null
                выпадает не на сегодня, но статы по периоду показываем всё
                равно). Hidden целиком когда период пустой (cold-start). */}
            <RecoveryMeaningCard series={series.recovery} />
          </div>
        )}
      </div>
    </Layout>
  )
}

// Legend chip — line swatch + label, struck through when the series is off.
function LegendToggle({
  on,
  color,
  label,
  onClick,
}: {
  on: boolean
  color: string
  label: string
  onClick: () => void
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      aria-pressed={on}
      className={`inline-flex items-center gap-1.5 rounded-pill border px-2.5 py-1 text-[12px] font-semibold transition-colors ${
        on ? 'border-halo-border bg-halo-surface-2 text-halo-ink' : 'border-transparent text-halo-ink-dimmer'
      }`}
    >
      <span
        className="h-0.5 w-3.5 rounded-sm"
        style={{ background: on ? color : 'var(--color-ink-dimmer)', opacity: on ? 1 : 0.4 }}
      />
      <span className={on ? '' : 'line-through'}>{label}</span>
    </button>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// «Что это значит» card — deterministic period summary, lavender chrome
// matches the HRV/RHR meaning card on `/wellness/:metric` for visual
// consistency. Template by today's recovery category × period stats from
// `computeRecoveryMeaningStat`; falls back to `no_today` when the period
// has data but today is missing (gap-on-the-edge). Hidden when the whole
// period is empty (cold-start / first-day-after-onboarding).
// ─────────────────────────────────────────────────────────────────────────────
function RecoveryMeaningCard({ series }: { series: readonly (number | null)[] }) {
  const { t } = useTranslation()
  const stat = computeRecoveryMeaningStat(series)
  if (!stat) return null
  const key = stat.todayCategory ?? 'no_today'
  const text = t(`recovery_trend.meaning.${key}`, {
    days: stat.days,
    goodPct: stat.goodPct,
    lowPct: stat.lowPct,
    avg: stat.avg,
  })
  return (
    <div className="rounded-card border border-halo-border bg-halo-brand-light p-[18px]">
      <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-brand-dark">
        {t('recovery_trend.meaning_title')}
      </div>
      <p className="mt-2 text-[15px] leading-[1.5] text-halo-ink">{text}</p>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Dual-axis line chart — Recovery score (left, 0-100, tinted area) + HRV/RHR
// (right, auto-fit, lines). Hand-rolled inline SVG (prototype
// `BRecoveryTrendChart`); `preserveAspectRatio="none"` lets it stretch to the
// card width, matching the design. Null points are skipped and the line spans
// the gap — a single missing wellness day reads as a glitch, not a real break.
// ─────────────────────────────────────────────────────────────────────────────
function RecoveryTrendChart({
  series,
  show,
}: {
  series: RecoveryTrendSeries
  show: Record<SeriesKey, boolean>
}) {
  const W = 320
  const H = 220
  const pad = { l: 30, r: 30, t: 12, b: 22 }
  const innerW = W - pad.l - pad.r
  const innerH = H - pad.t - pad.b
  const N = series.dates.length

  // Left axis — Recovery 0..100, fixed (stable across periods, readable ticks).
  const lMin = 0
  const lMax = 100
  // Right axis — HRV/RHR, fit to the visible data, padded; defaults to 30..80
  // when both right-side series are hidden so the frame still has structure.
  const rightVals: number[] = []
  if (show.hrv) for (const v of series.hrv) if (v != null) rightVals.push(v)
  if (show.rhr) for (const v of series.rhr) if (v != null) rightVals.push(v)
  let rMin = 30
  let rMax = 80
  if (rightVals.length) {
    rMin = Math.max(20, Math.floor(Math.min(...rightVals) / 5) * 5 - 5)
    rMax = Math.min(120, Math.ceil(Math.max(...rightVals) / 5) * 5 + 5)
    if (rMax - rMin < 25) rMax = rMin + 25
  }

  const xOf = (i: number) => pad.l + (N <= 1 ? innerW / 2 : (i / (N - 1)) * innerW)
  const yL = (v: number) => pad.t + innerH - ((v - lMin) / (lMax - lMin)) * innerH
  const yR = (v: number) => pad.t + innerH - ((v - rMin) / (rMax - rMin)) * innerH

  // Build a polyline through non-null points only; tracks first/last valid
  // index for the area baseline and the endpoint dot.
  const buildLine = (arr: (number | null)[], mapY: (v: number) => number) => {
    let d = ''
    let first = -1
    let last = -1
    arr.forEach((v, i) => {
      if (v == null) return
      d += (first < 0 ? 'M ' : ' L ') + xOf(i).toFixed(1) + ' ' + mapY(v).toFixed(1)
      if (first < 0) first = i
      last = i
    })
    return { d, first, last }
  }

  const rec = buildLine(series.recovery, yL)
  const hrvLine = buildLine(series.hrv, yR)
  const rhrLine = buildLine(series.rhr, yR)
  const recArea =
    rec.first >= 0
      ? `${rec.d} L ${xOf(rec.last).toFixed(1)} ${yL(0).toFixed(1)} L ${xOf(rec.first).toFixed(1)} ${yL(0).toFixed(1)} Z`
      : ''

  const leftTicks = [0, 25, 50, 75, 100]
  const rightTicks = [0, 1, 2, 3, 4].map(i => rMin + (i * (rMax - rMin)) / 4)

  // Sparse x labels — up to 5, evenly spaced.
  const xLabels: { i: number; label: string }[] = []
  if (N > 0) {
    const cnt = Math.min(5, N)
    for (let k = 0; k < cnt; k++) {
      const idx = cnt === 1 ? 0 : Math.round((k * (N - 1)) / (cnt - 1))
      const p = series.dates[idx].split('-')
      xLabels.push({ i: idx, label: `${p[1]}/${p[2]}` })
    }
  }

  const showRight = show.hrv || show.rhr
  const dot = (cx: number, cy: number, color: string) => (
    <circle cx={cx} cy={cy} r="4" fill="#fff" stroke={color} strokeWidth="1.8" />
  )

  // Hover/touch scrubber — vertical rule + Recovery/HRV/RHR callout.
  const { svgRef, idx: scrubIdx, handlers } = useChartScrubber(N, pad.l, innerW)
  const scrubItems: ScrubItem[] =
    scrubIdx == null
      ? []
      : [
          ...(show.recovery && series.recovery[scrubIdx] != null
            ? [{ label: 'Recovery', value: Math.round(series.recovery[scrubIdx] as number), color: SERIES_COLOR.recovery }]
            : []),
          ...(show.hrv && series.hrv[scrubIdx] != null
            ? [{ label: 'HRV', value: `${num(series.hrv[scrubIdx] as number, 1)} ms`, color: SERIES_COLOR.hrv }]
            : []),
          ...(show.rhr && series.rhr[scrubIdx] != null
            ? [{ label: 'RHR', value: `${Math.round(series.rhr[scrubIdx] as number)} bpm`, color: SERIES_COLOR.rhr }]
            : []),
        ]

  return (
    <svg
      ref={svgRef}
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      height={H}
      preserveAspectRatio="none"
      className="block overflow-visible"
      {...handlers}
    >
      {/* horizontal gridlines, aligned to the left ticks */}
      {leftTicks.map(tick => (
        <line
          key={`g${tick}`}
          x1={pad.l}
          y1={yL(tick)}
          x2={pad.l + innerW}
          y2={yL(tick)}
          stroke="var(--color-border)"
          strokeWidth="1"
          strokeDasharray={tick === 0 ? undefined : '2 3'}
          opacity={tick === 0 ? 0.55 : 0.45}
        />
      ))}

      {/* left axis labels — Recovery score (dim when toggled off) */}
      {leftTicks.map(tick => (
        <text
          key={`lt${tick}`}
          x={pad.l - 6}
          y={yL(tick) + 3}
          fontSize="9"
          textAnchor="end"
          fill={show.recovery ? SERIES_COLOR.recovery : 'var(--color-ink-dimmer)'}
          opacity={show.recovery ? 0.9 : 0.5}
        >
          {tick}
        </text>
      ))}

      {/* right axis labels — HRV/RHR (only when either is visible) */}
      {showRight &&
        rightTicks.map((tick, i) => (
          <text
            key={`rt${i}`}
            x={pad.l + innerW + 6}
            y={yR(tick) + 3}
            fontSize="9"
            textAnchor="start"
            fill="var(--color-ink-dim)"
            opacity="0.9"
          >
            {Math.round(tick)}
          </text>
        ))}

      {/* Recovery — tinted area + line */}
      {show.recovery && rec.first >= 0 && (
        <>
          <path d={recArea} fill={SERIES_COLOR.recovery} fillOpacity="0.12" />
          <path d={rec.d} fill="none" stroke={SERIES_COLOR.recovery} strokeWidth="2" strokeLinejoin="round" strokeLinecap="round" />
          {dot(xOf(rec.last), yL(series.recovery[rec.last] as number), SERIES_COLOR.recovery)}
        </>
      )}

      {/* HRV */}
      {show.hrv && hrvLine.first >= 0 && (
        <>
          <path d={hrvLine.d} fill="none" stroke={SERIES_COLOR.hrv} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
          {dot(xOf(hrvLine.last), yR(series.hrv[hrvLine.last] as number), SERIES_COLOR.hrv)}
        </>
      )}

      {/* RHR */}
      {show.rhr && rhrLine.first >= 0 && (
        <>
          <path d={rhrLine.d} fill="none" stroke={SERIES_COLOR.rhr} strokeWidth="1.6" strokeLinejoin="round" strokeLinecap="round" />
          {dot(xOf(rhrLine.last), yR(series.rhr[rhrLine.last] as number), SERIES_COLOR.rhr)}
        </>
      )}

      {/* axis unit captions */}
      <text
        x={pad.l - 6}
        y={pad.t - 2}
        fontSize="8"
        fontWeight="700"
        textAnchor="end"
        letterSpacing="0.4"
        fill={show.recovery ? SERIES_COLOR.recovery : 'var(--color-ink-dimmer)'}
        opacity={show.recovery ? 0.9 : 0.5}
      >
        SCORE
      </text>
      {showRight && (
        <text
          x={pad.l + innerW + 6}
          y={pad.t - 2}
          fontSize="8"
          fontWeight="700"
          textAnchor="start"
          letterSpacing="0.4"
          fill="var(--color-ink-dim)"
        >
          {show.hrv && show.rhr ? 'MS · BPM' : show.hrv ? 'MS' : 'BPM'}
        </text>
      )}

      {/* x labels */}
      {xLabels.map((l, i) => (
        <text
          key={`x${i}`}
          x={xOf(l.i)}
          y={H - pad.b + 12}
          fontSize="9"
          fill="var(--color-ink-dim)"
          textAnchor={i === 0 ? 'start' : i === xLabels.length - 1 ? 'end' : 'middle'}
        >
          {l.label}
        </text>
      ))}

      {/* Scrubber — invisible hit target + crosshair callout. */}
      <rect x={pad.l} y={pad.t} width={innerW} height={innerH} fill="transparent" style={{ cursor: 'crosshair' }} />
      <ChartScrubLine
        idx={scrubIdx}
        dateLabel={fmtScrubDate(series.dates[scrubIdx ?? 0])}
        items={scrubItems}
        x={xOf}
        padT={pad.t}
        innerH={innerH}
        W={W}
        padR={pad.r}
      />
    </svg>
  )
}
