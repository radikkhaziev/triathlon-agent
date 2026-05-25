import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useParams, Navigate } from 'react-router-dom'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import {
  ChartScrubLine,
  fmtScrubDate,
  InfoIcon,
  InfoPanel,
  PeriodFilter,
  useChartScrubber,
  type PeriodRange,
  type ScrubItem,
} from '../components/halo'
import { useApi } from '../hooks/useApi'
import { useMeasuredWidth } from '../hooks/useMeasuredWidth'
import { fmtDateYmd, num } from '../lib/formatters'
import { STATUS_EMOJI, type RmssdStatus } from '../utils/recovery'
import type { WellnessResponse, HRVBlock, RHRBlock, RecoveryTrendSeries } from '../api/types'

/**
 * HRV / RHR metric detail (prototype `BMetricDetail`, direction-b-halo.jsx:758).
 * Reached by tapping the HRV or RHR tile on `/wellness`.
 *
 * Layout (top→bottom): Hero · Statistics · 60-day trend chart · «Что это
 * значит» card. Stats are above the chart so the user reads the numbers
 * first, then sees the shape they came from (per the design prototype,
 * `direction-b-halo.jsx:922`). Three sections carry a halo `InfoIcon` → `InfoPanel`
 * explainer (same pattern as the Trends/Load tab); copy is plain-language,
 * tuned for a rank-and-file athlete. Single tip open at a time so the screen
 * never stacks dark panels.
 *
 * Chart: configurable 1m/3m/6m/1y series via `PeriodFilter`
 * (`/api/recovery-trend?days=N`, single line — HRV `series.hrv` или RHR
 * `series.rhr`). Default `3m`, matching the prototype. Hand-rolled inline
 * SVG; pattern from `RecoveryTrend.tsx` but trimmed to one series and no
 * legend. Scrubber for hover/touch values.
 *
 * «What this means»: server-rendered `meaning` field (rule-based, status ×
 * streak — see `api/routers/wellness.py:_hrv_meaning`). Per-metric AI prose
 * was retired in the 2026-05-23 «G3=(b)» reversal — the lavender card +
 * `/coach` (one voice) is the contract.
 */
type Metric = 'hrv' | 'rhr'
type TipKey = 'hero' | 'trend' | 'stats'

// Chart colours mirror the Recovery trend page so the metric reads with a
// consistent identity across surfaces.
const SERIES_COLOR: Record<Metric, string> = {
  hrv: 'var(--color-amber)',
  rhr: 'var(--color-brand)',
}

// Canonical period-window → days mapping, shared with the other trend
// screens (RecoveryTrend / SleepTrend / BodyTrend / LoadDetail).
const RANGE_DAYS: Record<PeriodRange, number> = { '1m': 30, '3m': 90, '6m': 180, '1y': 365 }

export default function MetricDetail() {
  const { t } = useTranslation()
  const params = useParams<{ metric: string }>()
  const metric = params.metric as Metric | undefined
  const today = fmtDateYmd(new Date())
  const { data, loading, error } = useApi<WellnessResponse>(`/api/wellness-day?date=${today}`)
  // Trend series feeds the chart. `range` drives `?days=N` — same endpoint
  // Recovery-trend uses; we just pick `hrv` or `rhr` from the payload.
  // Failure here is non-fatal — the chart card hides and the rest of the
  // page renders. Default `3m` mirrors the design prototype.
  const [range, setRange] = useState<PeriodRange>('3m')
  const { data: series } = useApi<RecoveryTrendSeries>(`/api/recovery-trend?days=${RANGE_DAYS[range]}`)

  if (metric !== 'hrv' && metric !== 'rhr') {
    return <Navigate to="/wellness" replace />
  }

  const title = metric === 'hrv' ? t('metric_detail.hrv_title') : t('metric_detail.rhr_title')
  const unit = metric === 'hrv' ? t('wellness.ms') : t('metric_detail.bpm')

  return (
    <Layout maxWidth="480px">
      <div className="-mx-4 -mt-4 md:-mb-8 min-h-screen bg-halo-bg px-4 md:px-9 font-sans text-halo-ink">
        <header className="flex items-center px-1 pt-[18px] pb-2.5">
          <Link
            to="/wellness"
            className="inline-flex items-center gap-1.5 py-1.5 pl-1 pr-2.5 text-sm font-medium text-halo-ink-dim no-underline"
          >
            <span className="text-lg leading-none">‹</span> {t('nav.today')}
          </Link>
        </header>

        {loading && <LoadingSpinner />}
        {error && <ErrorMessage message={t('wellness.load_error')} />}

        {!loading && !error && data?.has_data && (
          metric === 'hrv'
            ? <Body block={data.hrv} title={title} unit={unit} t={t} kind="hrv" series={series} range={range} setRange={setRange} />
            : <Body block={data.rhr} title={title} unit={unit} t={t} kind="rhr" series={series} range={range} setRange={setRange} />
        )}

        {!loading && !error && data && !data.has_data && (
          <div className="px-4 py-10 text-center text-[15px] text-halo-ink-dim">
            {t('wellness.no_data')}
          </div>
        )}
      </div>
    </Layout>
  )
}

type TFn = (k: string, o?: Record<string, unknown>) => string

interface BodyProps {
  block: HRVBlock | RHRBlock
  title: string
  unit: string
  t: TFn
  kind: Metric
  series: RecoveryTrendSeries | null
  range: PeriodRange
  setRange: (r: PeriodRange) => void
}

function Body({ block, title, unit, t, kind, series, range, setRange }: BodyProps) {
  // Single-key tip state: one panel open at a time so the page never stacks
  // overlapping dark panels. Tap the same icon again to close.
  const [openTip, setOpenTip] = useState<TipKey | null>(null)
  const toggleTip = (k: TipKey) => setOpenTip(prev => (prev === k ? null : k))

  const status = block.status as RmssdStatus
  const statusEmoji = STATUS_EMOJI[status] ?? '⚪'
  const statusLabel = t(`status.${status}`)
  const statusColor = `var(--color-status-${status === 'insufficient_data' ? 'gray' : status})`
  const statusBg =
    status === 'green' ? 'var(--color-brand-light)'
    : status === 'yellow' ? '#f5e6c8'
    : status === 'red' ? '#fde6e6'
    : 'var(--color-surface-2)'

  const todayStr = block.today != null ? num(block.today, kind === 'rhr' ? 0 : 1) : '—'
  const deltaPretty = formatDelta(block, kind)

  // Rows: every field the backend actually exposes. Skip rows whose value is
  // null (cold-start / <14d-HRV) rather than render «—» soup.
  const rows: { label: string; value: string }[] = []
  if (block.mean_7d != null && block.sd_7d != null) {
    rows.push({
      label: t('metric_detail.mean_7d'),
      value: `${num(block.mean_7d, kind === 'rhr' ? 0 : 1)} ± ${num(block.sd_7d, 1)}`,
    })
  }
  if (kind === 'rhr') {
    const rhr = block as RHRBlock
    if (rhr.mean_30d != null && rhr.sd_30d != null) {
      rows.push({
        label: t('metric_detail.mean_30d'),
        value: `${num(rhr.mean_30d, 0)} ± ${num(rhr.sd_30d, 1)}`,
      })
    }
  }
  if (block.mean_60d != null && block.sd_60d != null) {
    rows.push({
      label: t('metric_detail.mean_60d'),
      value: `${num(block.mean_60d, kind === 'rhr' ? 0 : 1)} ± ${num(block.sd_60d, 1)}`,
    })
  }
  if (block.lower_bound != null && block.upper_bound != null) {
    rows.push({
      label: t('metric_detail.bounds'),
      value: `${num(block.lower_bound, kind === 'rhr' ? 0 : 1)} – ${num(block.upper_bound, kind === 'rhr' ? 0 : 1)} ${unit}`,
    })
  }
  if (block.cv_7d != null) {
    // `cv_verdict` is pre-localized by the backend per `user.language`
    // (api/routers/wellness.py) — render verbatim, no t() wrapper.
    const verdict = block.cv_verdict ? ` · ${block.cv_verdict}` : ''
    rows.push({ label: t('metric_detail.cv_7d'), value: `${num(block.cv_7d, 1)}%${verdict}` })
  }
  // SWC is HRV-only on the backend; `swc_verdict` is pre-localized.
  if (kind === 'hrv') {
    const hrv = block as HRVBlock
    if (hrv.swc_verdict) {
      rows.push({ label: t('metric_detail.swc'), value: hrv.swc_verdict })
    }
  }
  if (block.trend?.direction) {
    const r2 =
      block.trend.r_squared != null ? ` · r² ${num(block.trend.r_squared, 2)}` : ''
    rows.push({
      label: t('metric_detail.trend'),
      value: `${t(`metric_detail.trend_direction.${block.trend.direction}`, { defaultValue: block.trend.direction })}${r2}`,
    })
  }

  // Trend chart input — single series for this metric. `null` while loading;
  // `[]` if endpoint returned no rows. We hide the chart card entirely when
  // series isn't usable (per «data-honest» — don't paint an empty chart).
  const chartValues: (number | null)[] | null = !series
    ? null
    : (kind === 'hrv' ? series.hrv : series.rhr)
  const hasChartData = chartValues != null && chartValues.some(v => v != null)

  // Min/max range for the chart subtitle («32–58 ms» / «48–72 bpm»).
  let rangeLabel: string | null = null
  if (hasChartData) {
    const nums = chartValues!.filter((v): v is number => v != null)
    const lo = Math.min(...nums)
    const hi = Math.max(...nums)
    const fmt = (n: number) => num(n, kind === 'rhr' ? 0 : 1)
    rangeLabel = `${fmt(lo)}–${fmt(hi)} ${unit}`
  }

  return (
    <div className="flex flex-col gap-3.5 pb-6">
      {/* Hero: today + unit + status pill + delta vs baseline + InfoIcon */}
      <div className="rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card">
        <div className="flex items-center text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
          <span>{title}</span>
          <InfoIcon open={openTip === 'hero'} onClick={() => toggleTip('hero')} />
        </div>
        <div className="mt-2 flex items-end justify-between gap-3">
          <div>
            <div className="flex items-baseline gap-1.5">
              <span className="text-[44px] font-semibold leading-none tracking-[-1.5px] text-halo-ink">
                {todayStr}
              </span>
              <span className="text-sm text-halo-ink-dim">{unit}</span>
            </div>
            {deltaPretty && (
              <div className="mt-1 text-[13px] font-semibold" style={{ color: statusColor }}>
                {deltaPretty}{' '}
                <span className="font-medium text-halo-ink-dim">{t('metric_detail.vs_baseline')}</span>
              </div>
            )}
          </div>
          <span
            className="inline-flex items-center gap-1.5 rounded-pill px-2.5 py-1 text-[11px] font-bold uppercase tracking-[0.4px]"
            style={{ background: statusBg, color: statusColor }}
          >
            <span aria-hidden="true">{statusEmoji}</span>
            {statusLabel}
          </span>
        </div>
        {openTip === 'hero' && <InfoPanel>{t(`metric_detail.tip.${kind}.metric`)}</InfoPanel>}
      </div>

      {/* Stats — every backed field. Empty rows skipped. Placed above the
          chart so the user reads the numbers first, then sees the shape
          they came from (design: direction-b-halo.jsx:922). */}
      {rows.length > 0 && (
        <div className="rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card">
          <div className="flex items-center text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
            <span>{t('metric_detail.statistics')}</span>
            <InfoIcon open={openTip === 'stats'} onClick={() => toggleTip('stats')} />
          </div>
          {openTip === 'stats' && <InfoPanel>{t(`metric_detail.tip.${kind}.stats`)}</InfoPanel>}
          <div className="mt-1.5">
            {rows.map((r, i) => (
              <div
                key={r.label}
                className="flex items-center justify-between py-3"
                style={i === 0 ? undefined : { borderTop: '1px solid var(--color-border)' }}
              >
                <span className="text-[13px] text-halo-ink-dim">{r.label}</span>
                <span className="text-[13px] font-semibold tabular-nums text-halo-ink">{r.value}</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Trend chart. Hidden when the endpoint returned nothing plottable
          (cold-start / first-day-after-onboarding). Min-max shown in the
          subtitle as a quick «typical range» glance. The 1m/3m/6m/1y filter
          lives in its own row above the card (same chrome as Recovery /
          Sleep / Load / Body trends; design: direction-b-halo.jsx:944). */}
      {hasChartData && (
        <>
          <PeriodFilter value={range} onChange={setRange} />
          <div className="rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card">
            <div className="flex items-center text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
              <span>{t('metric_detail.trend_title')}</span>
              {/* InfoIcon immediately after the title — same anchoring as Hero
                  and Stats cards (consistency: «иконка прижата к титлу»). The
                  range label gets `ml-auto` and lands right-aligned. */}
              <InfoIcon open={openTip === 'trend'} onClick={() => toggleTip('trend')} />
              {rangeLabel && <span className="ml-auto text-halo-ink-dimmer">{rangeLabel}</span>}
            </div>
            <div className="mt-2">
              <MetricSparkline
                dates={series!.dates}
                values={chartValues!}
                kind={kind}
                unit={unit}
                color={SERIES_COLOR[kind]}
              />
            </div>
            {openTip === 'trend' && <InfoPanel>{t(`metric_detail.tip.${kind}.trend`)}</InfoPanel>}
          </div>
        </>
      )}

      {/* «What this means» — server-rendered, pre-localized, rule-based.
          One sentence of factual interpretation (status × streak), not AI
          prose. Lavender-tinted card to read as commentary rather than data;
          hidden when backend returned null (cold-start with no analysis row
          AND no status — exceedingly rare since `insufficient_data` produces
          its own message). */}
      {block.meaning && (
        <div className="rounded-card border border-halo-border bg-halo-brand-light p-[18px]">
          <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-brand-dark">
            {t('metric_detail.meaning_title')}
          </div>
          <p className="mt-2 text-[15px] leading-[1.5] text-halo-ink">
            {block.meaning}
          </p>
        </div>
      )}

      {/* Pointer-card to /coach убран по запросу пользователя 2026-05-23.
          Reasoning: factual «что это значит» card (server-rendered, выше)
          закрывает «зачем сюда зашёл» на per-metric экране; ссылка на общую
          AI-заметку дублирует тот же tap-target, который уже есть на Wellness
          home (HRV/RHR tiles → metric detail, отдельный coach teaser → /coach).
          One-voice rule сохраняется через /coach, просто без дубль-входа. */}
    </div>
  )
}

// Formatted delta string. HRV uses `delta_pct` (% vs 7d baseline per Flatt &
// Esco); RHR uses `delta_30d` (absolute bpm vs 30d baseline, inverted —
// negative is good). Falls back to null so the hero just omits the row.
function formatDelta(block: HRVBlock | RHRBlock, kind: Metric): string | null {
  if (kind === 'hrv') {
    const v = (block as HRVBlock).delta_pct
    if (v == null) return null
    const sign = v >= 0 ? '+' : ''
    return `${sign}${num(v, 1)}%`
  }
  const v = (block as RHRBlock).delta_30d
  if (v == null) return null
  const sign = v >= 0 ? '+' : ''
  return `${sign}${num(v, 1)}`
}

// ─────────────────────────────────────────────────────────────────────────────
// Single-series 60-day sparkline — line + faint area + endpoint dot, plus a
// scrubber for hover/touch values. Same hand-rolled SVG convention as the
// other Halo trend charts (RecoveryTrendChart / SleepTrendChart). Auto-fits
// the y-axis to the visible data (padded), with 5 horizontal gridlines and
// up to 5 evenly-spaced x date labels.
//
// Why not the bigger `RecoveryTrendChart`: that one juggles dual axes + two
// optional series + legend toggles. Here we always have exactly one series
// and no axis ambiguity, so the lighter geometry is easier to read.
// ─────────────────────────────────────────────────────────────────────────────
interface MetricSparklineProps {
  dates: string[]
  values: (number | null)[]
  kind: Metric
  unit: string
  color: string
}

function MetricSparkline({ dates, values, kind, unit, color }: MetricSparklineProps) {
  const [wrapRef, W] = useMeasuredWidth<HTMLDivElement>(320)
  const H = 180
  const pad = { l: 30, r: 12, t: 10, b: 22 }
  const innerW = W - pad.l - pad.r
  const innerH = H - pad.t - pad.b
  const N = dates.length

  // Auto-fit y axis to the visible data, with 5%-ish padding so the line
  // doesn't kiss the top/bottom edge. Empty-series fallback (≈ unreachable
  // due to parent's hasChartData guard) keeps a sensible 30..80 frame.
  const nums = values.filter((v): v is number => v != null)
  let yMin = 30
  let yMax = 80
  if (nums.length) {
    const lo = Math.min(...nums)
    const hi = Math.max(...nums)
    const span = Math.max(hi - lo, 1)
    yMin = Math.max(0, Math.floor((lo - span * 0.1) / 5) * 5)
    yMax = Math.ceil((hi + span * 0.1) / 5) * 5
    if (yMax - yMin < 10) yMax = yMin + 10
  }

  const xOf = (i: number) => pad.l + (N <= 1 ? innerW / 2 : (i / (N - 1)) * innerW)
  const yOf = (v: number) => pad.t + innerH - ((v - yMin) / (yMax - yMin)) * innerH

  // Build polyline through non-null points; track first/last for the area
  // baseline + endpoint dot.
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
      ? `${d} L ${xOf(last).toFixed(1)} ${yOf(yMin).toFixed(1)} L ${xOf(first).toFixed(1)} ${yOf(yMin).toFixed(1)} Z`
      : ''

  const yTicks = [0, 1, 2, 3, 4].map(i => yMin + (i * (yMax - yMin)) / 4)

  // Sparse x labels — up to 5 evenly-spaced.
  const xLabels: { i: number; label: string }[] = []
  if (N > 0) {
    const cnt = Math.min(5, N)
    for (let k = 0; k < cnt; k++) {
      const idx = cnt === 1 ? 0 : Math.round((k * (N - 1)) / (cnt - 1))
      const p = dates[idx].split('-')
      xLabels.push({ i: idx, label: `${p[1]}/${p[2]}` })
    }
  }

  // Scrubber — vertical rule + callout with the date's value.
  const { svgRef, idx: scrubIdx, handlers } = useChartScrubber(N, pad.l, innerW)
  const scrubItems: ScrubItem[] =
    scrubIdx == null || values[scrubIdx] == null
      ? []
      : [
          {
            label: kind === 'hrv' ? 'HRV' : 'RHR',
            value: `${num(values[scrubIdx] as number, kind === 'rhr' ? 0 : 1)} ${unit}`.trim(),
            color,
          },
        ]

  return (
    <div ref={wrapRef} className="w-full">
    <svg
      ref={svgRef}
      viewBox={`0 0 ${W} ${H}`}
      width="100%"
      height={H}
      className="block overflow-visible"
      {...handlers}
    >
      {/* gridlines */}
      {yTicks.map(tick => (
        <line
          key={`g${tick}`}
          x1={pad.l}
          y1={yOf(tick)}
          x2={pad.l + innerW}
          y2={yOf(tick)}
          stroke="var(--color-border)"
          strokeWidth="1"
          strokeDasharray={tick === yTicks[0] ? undefined : '2 3'}
          opacity={tick === yTicks[0] ? 0.55 : 0.45}
        />
      ))}

      {/* y axis labels */}
      {yTicks.map((tick, i) => (
        <text
          key={`yt${i}`}
          x={pad.l - 6}
          y={yOf(tick) + 3}
          fontSize="9"
          textAnchor="end"
          fill="var(--color-ink-dim)"
          opacity="0.9"
        >
          {kind === 'rhr' ? Math.round(tick) : num(tick, 0)}
        </text>
      ))}

      {/* tinted area + line */}
      {first >= 0 && (
        <>
          <path d={area} fill={color} fillOpacity="0.12" />
          <path
            d={d}
            fill="none"
            stroke={color}
            strokeWidth="1.8"
            strokeLinejoin="round"
            strokeLinecap="round"
          />
          <circle cx={xOf(last)} cy={yOf(values[last] as number)} r="4" fill="#fff" stroke={color} strokeWidth="1.8" />
        </>
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

      {/* hit target + scrubber callout */}
      <rect x={pad.l} y={pad.t} width={innerW} height={innerH} fill="transparent" style={{ cursor: 'crosshair' }} />
      <ChartScrubLine
        idx={scrubIdx}
        dateLabel={fmtScrubDate(dates[scrubIdx ?? 0])}
        items={scrubItems}
        x={xOf}
        padT={pad.t}
        innerH={innerH}
        W={W}
        padR={pad.r}
      />
    </svg>
    </div>
  )
}
