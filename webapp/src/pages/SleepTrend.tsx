import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import Layout from '../components/Layout'
import { Card, ChartScrubLine, fmtScrubDate, PeriodFilter, useChartScrubber, type ScrubItem } from '../components/halo'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import { useApi } from '../hooks/useApi'
import { useMeasuredWidth } from '../hooks/useMeasuredWidth'
import { fmtDateYmd } from '../lib/formatters'
import { SLEEP_ZONES, sleepZoneOf } from '../utils/recovery'
import type { SleepTrendSeries, WellnessResponse } from '../api/types'

/**
 * Sleep trend detail (prototype `BSleepDetail` / `BMiniBarChart` /
 * `BSleepScoreChart`, direction-b-halo.jsx:2735-3214). Reached by tapping the
 * Sleep card on /wellness. Period filter (1m/3m/6m) → two charts:
 *  · Duration — bars in minutes vs an 8h goal line, each bar coloured by that
 *    night's score zone (quantity = height, quality = colour).
 *  · Score — a zoned line over 4 bands (poor/fair/good/excellent), line colour
 *    follows the band the point sits in.
 *
 * Series come from the new `/api/sleep-trend` endpoint; `updated_at` for the
 * header comes from `/api/wellness-day` (same as the Recovery trend screen).
 */

type Range = '1m' | '3m' | '6m' | '1y'
const RANGE_DAYS: Record<Range, number> = { '1m': 30, '3m': 90, '6m': 180, '1y': 365 }

// Duration series colour (design `SLEEP_DURATION_COLOR = B.sage`) — the bar
// fallback when a bar has no score to colour it by.
const DURATION_COLOR = 'var(--color-brand)'
const SLEEP_GOAL_MIN = 480 // 8h

const fmtHM = (min: number) => `${Math.floor(min / 60)}h ${String(Math.round(min % 60)).padStart(2, '0')}m`

// Last non-null value — the freshest measured night.
function lastValid(arr: (number | null)[] | undefined): number | null {
  if (!arr) return null
  for (let i = arr.length - 1; i >= 0; i--) {
    if (arr[i] != null) return arr[i]
  }
  return null
}

// Mean of the non-null entries, or null if the window is empty / absent.
function avgValid(arr: (number | null)[] | undefined): number | null {
  if (!arr) return null
  let sum = 0
  let n = 0
  for (const v of arr) {
    if (v != null) {
      sum += v
      n++
    }
  }
  return n ? sum / n : null
}

export default function SleepTrend() {
  const { t } = useTranslation()
  const [range, setRange] = useState<Range>('3m')

  const pastDays = RANGE_DAYS[range]
  const { data: series, loading, error } = useApi<SleepTrendSeries>(`/api/sleep-trend?days=${pastDays}`)
  // Today's snapshot carries `updated_at` the series doesn't — non-fatal.
  const today = fmtDateYmd(new Date())
  const { data: wellness } = useApi<WellnessResponse>(`/api/wellness-day?date=${today}`)
  const w = wellness?.has_data ? wellness : null
  const updatedTime = w?.updated_at
    ? new Date(w.updated_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    : null

  const durToday = lastValid(series?.duration_min)
  const scoreToday = lastValid(series?.score)
  const durAvg = avgValid(series?.duration_min)
  const scoreAvg = avgValid(series?.score)
  const scoreZone = scoreToday != null ? sleepZoneOf(scoreToday) : null

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
            <span className="pr-1 text-xs text-halo-ink-dim">{t('sleep_trend.updated', { time: updatedTime })}</span>
          )}
        </header>

        {loading && !series && <LoadingSpinner />}
        {error && !series && <ErrorMessage message={t('wellness.load_error')} />}

        {series && (
          <div className="flex flex-col gap-3.5 pb-6">
            <div>
              <div className="text-[22px] font-semibold tracking-[-0.4px]">{t('sleep_trend.title')}</div>
              <div className="mt-0.5 text-[13px] text-halo-ink-dim">
                {t('sleep_trend.subtitle', { days: pastDays })}
              </div>
            </div>

            {/* Headline — today + window average for both axes */}
            <Card>
              <div className="grid grid-cols-2 gap-3.5">
                <div>
                  <div className="inline-flex items-center gap-1.5">
                    <span className="h-2 w-2 rounded-sm" style={{ background: DURATION_COLOR }} />
                    <span className="text-[11px] font-semibold text-halo-ink-dim">Duration</span>
                  </div>
                  <div className="mt-1 text-[26px] font-semibold tracking-[-0.5px] text-halo-ink">
                    {durToday != null ? fmtHM(durToday) : '—'}
                  </div>
                  <div className="mt-px text-[9px] font-semibold uppercase tracking-[0.4px] text-halo-ink-dimmer">
                    {durAvg != null ? `Avg ${fmtHM(durAvg)} · ${pastDays}d` : ''}
                  </div>
                </div>
                <div>
                  <div className="inline-flex items-center gap-1.5">
                    <span
                      className="h-2 w-2 rounded-sm"
                      style={{ background: scoreZone?.line ?? 'var(--color-ink-dimmer)' }}
                    />
                    <span className="text-[11px] font-semibold text-halo-ink-dim">Score</span>
                    {scoreZone && (
                      <span
                        className="ml-0.5 rounded-pill px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.5px]"
                        style={{ color: scoreZone.line, background: scoreZone.fill }}
                      >
                        {scoreZone.label}
                      </span>
                    )}
                  </div>
                  <div className="mt-1 text-[26px] font-semibold tracking-[-0.5px] text-halo-ink">
                    {scoreToday != null ? Math.round(scoreToday) : '—'}
                    <span className="text-xs font-medium text-halo-ink-dim"> /100</span>
                  </div>
                  <div className="mt-px text-[9px] font-semibold uppercase tracking-[0.4px] text-halo-ink-dimmer">
                    {scoreAvg != null ? `Avg ${Math.round(scoreAvg)} · ${pastDays}d` : ''}
                  </div>
                </div>
              </div>
            </Card>

            {/* Period filter */}
            <PeriodFilter value={range} onChange={setRange} />

            {series.dates.length === 0 ? (
              <Card>
                <div className="py-10 text-center text-[13px] text-halo-ink-dim">{t('sleep_trend.no_data')}</div>
              </Card>
            ) : (
              <>
                {/* Duration — bars in minutes vs the 8h goal, coloured by score zone. */}
                <Card>
                  <div className="mb-1.5 flex items-baseline justify-between">
                    <div className="text-[13px] font-semibold text-halo-ink">Sleep duration</div>
                    <div className="text-[10px] font-semibold tracking-[0.3px] text-halo-ink-dimmer">
                      Bar color = score zone
                    </div>
                  </div>
                  <SleepDurationChart
                    dates={series.dates}
                    durationMin={series.duration_min}
                    score={series.score}
                  />
                </Card>

                {/* Score — zoned line over 4 bands. */}
                <Card>
                  <div className="mb-1.5 flex items-baseline justify-between">
                    <div className="text-[13px] font-semibold text-halo-ink">Sleep score</div>
                    {scoreZone && (
                      <div
                        className="inline-flex items-center gap-1.5 text-[11px] font-semibold"
                        style={{ color: scoreZone.line }}
                      >
                        <span className="h-2 w-2 rounded-sm" style={{ background: scoreZone.line }} />
                        Today · {scoreZone.label.toLowerCase()}
                      </div>
                    )}
                  </div>
                  <SleepScoreChart dates={series.dates} score={series.score} />
                  {/* Zone legend — excellent → poor, top-to-bottom mirrors the
                      band stack on the chart above. */}
                  <div className="mt-2 grid grid-cols-4 gap-1">
                    {[...SLEEP_ZONES].reverse().map(z => {
                      const caption =
                        z.lo === 0 ? `< ${z.hi}` : z.hi === Infinity ? `≥ ${z.lo}` : `${z.lo}–${z.hi - 1}`
                      return (
                        <div key={z.id} className="flex flex-col items-center gap-0.5">
                          <span className="h-1 w-full rounded-sm" style={{ background: z.line, opacity: 0.85 }} />
                          <span className="text-[10px] font-semibold leading-tight" style={{ color: z.line }}>
                            {z.label}
                          </span>
                          <span className="text-[9px] font-medium leading-none text-halo-ink-dimmer">{caption}</span>
                        </div>
                      )
                    })}
                  </div>
                </Card>
              </>
            )}
          </div>
        )}
      </div>
    </Layout>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Sleep duration bar chart (prototype `BMiniBarChart`). Bars in minutes; past
// ~45 days the window auto-aggregates to weekly bars so they stay readable.
// Each bar is coloured by its (or the week's average) score zone; an 8h goal
// rule sits across the chart. viewBox W is measured per-frame so 1 viewBox
// unit == 1 CSS pixel — no horizontal stretch on desktop.
// ─────────────────────────────────────────────────────────────────────────────
function SleepDurationChart({
  dates,
  durationMin,
  score,
}: {
  dates: string[]
  durationMin: (number | null)[]
  score: (number | null)[]
}) {
  const [wrapRef, W] = useMeasuredWidth<HTMLDivElement>(320)
  const H = 140
  const pad = { l: 34, r: 8, t: 10, b: 20 }
  const innerW = W - pad.l - pad.r
  const innerH = H - pad.t - pad.b
  const N = dates.length

  // Aggregate to weekly bars past ~45 days; weekly value/colour average only
  // the non-null nights in the chunk.
  type Bar = { date: string; v: number | null; cv: number | null }
  const bars: Bar[] = []
  if (N > 45) {
    for (let i = 0; i < N; i += 7) {
      const end = Math.min(i + 6, N - 1)
      let ds = 0
      let dn = 0
      let cs = 0
      let cn = 0
      for (let k = i; k <= end; k++) {
        const dv = durationMin[k]
        const cv = score[k]
        if (dv != null) {
          ds += dv
          dn++
        }
        if (cv != null) {
          cs += cv
          cn++
        }
      }
      bars.push({ date: dates[i], v: dn ? ds / dn : null, cv: cn ? cs / cn : null })
    }
  } else {
    for (let i = 0; i < N; i++) bars.push({ date: dates[i], v: durationMin[i], cv: score[i] })
  }
  const M = bars.length

  // Floor the y-axis at the 8h goal so the dashed goal-rule always stays in
  // view — without this, the 3m/6m view (weekly averages, which usually sit
  // below 8h) clipped the goal line out of frame.
  const maxV = Math.max(SLEEP_GOAL_MIN, ...bars.map(b => b.v ?? 0))
  const niceStep =
    maxV > 1500 ? 500 : maxV > 600 ? 200 : maxV > 200 ? 50 : maxV > 50 ? 20 : 10
  const yMax = Math.ceil(maxV / niceStep) * niceStep || niceStep

  const slotW = innerW / M
  const barW = Math.max(2, slotW * (N > 45 ? 0.78 : 0.72))
  const xOf = (i: number) => pad.l + i * slotW + (slotW - barW) / 2
  const yOf = (v: number) => pad.t + innerH - (v / yMax) * innerH

  const yTicks = [0, yMax / 2, yMax]
  const xCount = Math.min(4, M)
  const xLabels: number[] = []
  for (let i = 0; i < xCount; i++) xLabels.push(xCount === 1 ? 0 : Math.round((i * (M - 1)) / (xCount - 1)))
  const fmtMd = (ymd: string) => {
    const p = ymd.split('-')
    return `${p[1]}/${p[2]}`
  }
  const fmtH = (m: number) => `${(m / 60).toFixed(1)}h`

  // Hover/touch scrubber — vertical rule + duration callout for the touched bar.
  const { svgRef, idx: scrubIdx, handlers } = useChartScrubber(M, pad.l, innerW)
  const scrubBar = scrubIdx == null ? null : bars[scrubIdx]
  const scrubItems: ScrubItem[] =
    scrubBar == null || scrubBar.v == null
      ? []
      : [
          {
            label: '',
            value: fmtHM(scrubBar.v),
            color: scrubBar.cv != null ? sleepZoneOf(scrubBar.cv).line : DURATION_COLOR,
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
      {yTicks.map((tick, i) => (
        <line
          key={`g${i}`}
          x1={pad.l}
          y1={yOf(tick)}
          x2={pad.l + innerW}
          y2={yOf(tick)}
          stroke="var(--color-border)"
          strokeWidth="1"
          strokeDasharray={tick === 0 ? undefined : '2 3'}
          opacity={tick === 0 ? 0.6 : 0.45}
        />
      ))}
      {yTicks.map((tick, i) => (
        <text key={`yt${i}`} x={pad.l - 6} y={yOf(tick) + 3} fontSize="9" fill="var(--color-ink-dim)" textAnchor="end">
          {fmtH(tick)}
        </text>
      ))}
      {/* Bars — today's (rightmost) at full opacity, history dimmed. */}
      {bars.map((b, i) => {
        if (b.v == null) return null
        const h = Math.max(1, yOf(0) - yOf(b.v))
        const fill = b.cv != null ? sleepZoneOf(b.cv).line : DURATION_COLOR
        return (
          <rect
            key={i}
            x={xOf(i)}
            y={yOf(b.v)}
            width={barW}
            height={h}
            rx="1.5"
            fill={fill}
            opacity={i === M - 1 ? 1 : 0.7}
          />
        )
      })}
      {/* 8h goal rule — yMax is floored at SLEEP_GOAL_MIN above, so the line
          is always in frame regardless of period (1m/3m/6m/1y). */}
      <line
        x1={pad.l}
        y1={yOf(SLEEP_GOAL_MIN)}
        x2={pad.l + innerW}
        y2={yOf(SLEEP_GOAL_MIN)}
        stroke="var(--color-ink-dim)"
        strokeWidth="1"
        strokeDasharray="4 3"
        opacity="0.55"
      />
      <text
        x={pad.l + innerW - 4}
        y={yOf(SLEEP_GOAL_MIN) - 4}
        fontSize="9"
        fill="var(--color-ink-dim)"
        textAnchor="end"
        fontWeight="600"
      >
        8h goal
      </text>
      {xLabels.map((idx, i) => (
        <text
          key={`x${i}`}
          x={xOf(idx) + barW / 2}
          y={H - pad.b + 12}
          fontSize="9"
          fill="var(--color-ink-dim)"
          textAnchor={i === 0 ? 'start' : i === xLabels.length - 1 ? 'end' : 'middle'}
        >
          {fmtMd(bars[idx].date)}
        </text>
      ))}

      {/* Scrubber — invisible hit target + crosshair callout. */}
      <rect x={pad.l} y={pad.t} width={innerW} height={innerH} fill="transparent" style={{ cursor: 'crosshair' }} />
      <ChartScrubLine
        idx={scrubIdx}
        dateLabel={fmtScrubDate(scrubBar?.date) + (N > 45 ? ' (wk)' : '')}
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

// ─────────────────────────────────────────────────────────────────────────────
// Sleep score chart (prototype `BSleepScoreChart`). Fixed 0-100 with 4 zone
// bands; the line is split into runs and each run is drawn in its band's
// colour. Null scores are skipped, the line spans the gap.
// ─────────────────────────────────────────────────────────────────────────────
function SleepScoreChart({ dates, score }: { dates: string[]; score: (number | null)[] }) {
  const [wrapRef, W] = useMeasuredWidth<HTMLDivElement>(320)
  const H = 160
  const pad = { l: 30, r: 8, t: 8, b: 22 }
  const innerW = W - pad.l - pad.r
  const innerH = H - pad.t - pad.b
  const N = dates.length

  const yMin = 0
  const yMax = 100
  const xOf = (i: number) => pad.l + (N <= 1 ? innerW / 2 : (i / (N - 1)) * innerW)
  const yOf = (v: number) => pad.t + innerH - ((v - yMin) / (yMax - yMin)) * innerH

  // Non-null points, carrying their index in the full date array (shared x).
  const pts = score
    .map((v, i) => ({ i, v }))
    .filter((p): p is { i: number; v: number } => p.v != null)

  // Runs of consecutive same-zone points; a zone change starts a new run from
  // the previous point so the segments join.
  const runs: { zone: number; from: number; to: number }[] = []
  let cur: { zone: number; from: number; to: number } | null = null
  for (let k = 0; k < pts.length; k++) {
    const z = SLEEP_ZONES.findIndex(zz => zz.id === sleepZoneOf(pts[k].v).id)
    if (!cur) cur = { zone: z, from: k, to: k }
    else if (cur.zone === z) cur.to = k
    else {
      runs.push(cur)
      cur = { zone: z, from: k - 1, to: k }
    }
  }
  if (cur) runs.push(cur)

  const pathOf = (from: number, to: number) => {
    let d = ''
    for (let k = from; k <= to; k++) {
      d += (k === from ? 'M ' : ' L ') + xOf(pts[k].i).toFixed(1) + ' ' + yOf(pts[k].v).toFixed(1)
    }
    return d
  }

  const yLabels = [0, 50, 70, 90, 100]
  const xCount = Math.min(5, N)
  const xLabels: number[] = []
  for (let i = 0; i < xCount; i++) xLabels.push(xCount === 1 ? 0 : Math.round((i * (N - 1)) / (xCount - 1)))
  const fmtMd = (ymd: string) => {
    const p = ymd.split('-')
    return `${p[1]}/${p[2]}`
  }
  const todayZone = pts.length ? sleepZoneOf(pts[pts.length - 1].v) : null

  // Hover/touch scrubber — vertical rule + score + zone callout.
  const { svgRef, idx: scrubIdx, handlers } = useChartScrubber(N, pad.l, innerW)
  const scrubScore = scrubIdx == null ? null : score[scrubIdx]
  const scrubItems: ScrubItem[] =
    scrubScore == null
      ? []
      : [
          { label: 'Score', value: Math.round(scrubScore), color: sleepZoneOf(scrubScore).line },
          { label: '', value: sleepZoneOf(scrubScore).label, color: sleepZoneOf(scrubScore).line },
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
      {/* Zone bands — poor at the bottom, excellent at the top. */}
      {SLEEP_ZONES.map(z => {
        const lo = z.lo
        const hi = Math.min(z.hi, yMax)
        if (hi <= lo) return null
        return <rect key={z.id} x={pad.l} y={yOf(hi)} width={innerW} height={yOf(lo) - yOf(hi)} fill={z.fill} />
      })}
      {yLabels.map(v => (
        <text key={`y${v}`} x={pad.l - 6} y={yOf(v) + 3} fontSize="9" fill="var(--color-ink-dim)" textAnchor="end">
          {v}
        </text>
      ))}
      {runs.map((r, ri) => (
        <path
          key={`r${ri}`}
          d={pathOf(r.from, r.to)}
          fill="none"
          stroke={SLEEP_ZONES[r.zone].line}
          strokeWidth="1.8"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      ))}
      {todayZone && pts.length > 0 && (
        <circle
          cx={xOf(pts[pts.length - 1].i)}
          cy={yOf(pts[pts.length - 1].v)}
          r="4"
          fill="#fff"
          stroke={todayZone.line}
          strokeWidth="1.8"
        />
      )}
      {xLabels.map((idx, i) => (
        <text
          key={`x${i}`}
          x={xOf(idx)}
          y={H - pad.b + 12}
          fontSize="9"
          fill="var(--color-ink-dim)"
          textAnchor={i === 0 ? 'start' : i === xLabels.length - 1 ? 'end' : 'middle'}
        >
          {fmtMd(dates[idx])}
        </text>
      ))}

      {/* Scrubber — invisible hit target + crosshair callout. */}
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
