// Endurance Score — full-screen detail. Tap from the Wellness home card.
//
// Port of `BEnduranceScoreDetail` (direction-b-halo.jsx:3641-3845): gauge
// restate (260px), per-zone-coloured trend with zone bands + zone-boundary
// y-axis ticks (k-format), 5-row zone legend with the current zone tinted.
//
// Backend trend is daily (not weekly as in the Halo mock). 1m/3m/6m/1y maps
// to 30/90/180/365 daily points. Dots render only on the 1m view (≤40 pts);
// longer windows mark only the latest point to keep the line readable.
import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import {
  Card,
  PeriodFilter,
  type PeriodRange,
  EnduranceGauge,
  EnduranceBadgePlate,
  ENDURANCE_ZONES,
  ENDURANCE_MAX,
  enduranceZoneFor,
} from '../components/halo'
import { apiFetch } from '../api/client'
import type { EnduranceScoreResponse } from '../api/types'

const W = 348
const H = 240
const PAD_L = 38
const PAD_R = 14
const PAD_T = 16
const PAD_B = 32
const INNER_W = W - PAD_L - PAD_R
const INNER_H = H - PAD_T - PAD_B

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']
function fmtMD(iso: string): string {
  const d = new Date(iso)
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`
}

// k-format ticks (3500 → "3.5k", 8000 → "8.0k"). Sub-1000 values keep raw.
function fmtTick(v: number): string {
  return v >= 1000 ? `${(v / 1000).toFixed(1)}k` : String(v)
}

// Build runs of same-zone-colored line segments so the polyline can recolour
// at zone boundaries. Overlap each run by one point so neighbouring segments
// meet cleanly. Mirrors direction-b-halo.jsx:3674-3685 (also the TSB chart
// in LoadDetail.tsx uses the same trick).
function buildRuns(scores: number[]) {
  const runs: { zoneId: string; color: string; from: number; to: number }[] = []
  let cur: (typeof runs)[number] | null = null
  for (let i = 0; i < scores.length; i++) {
    const z = enduranceZoneFor(scores[i])
    if (!cur) {
      cur = { zoneId: z.id, color: z.color, from: i, to: i }
    } else if (cur.zoneId === z.id) {
      cur.to = i
    } else {
      runs.push(cur)
      cur = { zoneId: z.id, color: z.color, from: i - 1, to: i }
    }
  }
  if (cur) runs.push(cur)
  return runs
}

export default function EnduranceDetail() {
  const { t } = useTranslation()
  const [range, setRange] = useState<PeriodRange>('3m')
  const [data, setData] = useState<EnduranceScoreResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    apiFetch<EnduranceScoreResponse>(`/api/endurance-score?period=${range}`)
      .then(d => {
        if (cancelled) return
        setData(d)
        setLoading(false)
      })
      .catch((e: Error) => {
        if (cancelled) return
        setError(e.message)
        setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [range])

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
          <span className="pr-1 text-[15px] font-semibold tracking-[-0.2px]">Endurance Score</span>
        </header>

        {loading && !data && <LoadingSpinner />}
        {error && !data && <ErrorMessage message={error} />}

        {data && <EnduranceDetailBody data={data} range={range} onRangeChange={setRange} />}
      </div>
    </Layout>
  )
}

function EnduranceDetailBody({
  data,
  range,
  onRangeChange,
}: {
  data: EnduranceScoreResponse
  range: PeriodRange
  onRangeChange: (p: PeriodRange) => void
}) {
  const { t } = useTranslation()
  const score = data.current.score
  const zone = enduranceZoneFor(score)
  const delta = data.current.delta_vs_week_ago
  const deltaSign = delta >= 0 ? '+' : ''

  const trend = data.trend
  const N = trend.length
  const vals = trend.map(p => p.score)

  // Y-axis snaps to zone boundaries that bracket the data — chart reads as
  // "here's how I moved through the zones". Same logic as direction-b-halo
  // .jsx:3655-3665 (boundaries + 80-unit padding so close points don't sit
  // glued to the band edge).
  const boundaries = [...ENDURANCE_ZONES.map(z => z.min), ENDURANCE_MAX]
  const rawMin = vals.length ? Math.min(...vals) : 0
  const rawMax = vals.length ? Math.max(...vals) : ENDURANCE_MAX
  let yMin = 0
  let yMax = ENDURANCE_MAX
  for (let i = boundaries.length - 1; i >= 0; i--) {
    if (boundaries[i] <= rawMin - 80) {
      yMin = boundaries[i]
      break
    }
  }
  for (let i = 0; i < boundaries.length; i++) {
    if (boundaries[i] >= rawMax + 80) {
      yMax = boundaries[i]
      break
    }
  }

  const x = (i: number) => PAD_L + (N === 1 ? INNER_W / 2 : (i / (N - 1)) * INNER_W)
  const y = (v: number) => PAD_T + INNER_H - ((v - yMin) / (yMax - yMin)) * INNER_H

  const runs = buildRuns(vals)
  const yTicks = boundaries.filter(b => b >= yMin && b <= yMax)
  const xLabelCount = range === '1m' ? 4 : range === '3m' ? 4 : range === '6m' ? 5 : 6
  const labelIdx = N > 1
    ? Array.from({ length: xLabelCount }, (_, i) => Math.round((i * (N - 1)) / (xLabelCount - 1)))
    : [0]
  // Daily snapshots: only render per-point dots on the 1m view (~30 pts).
  // 3m+ windows show only the latest point so the line stays readable.
  const showDots = N <= 40

  return (
    <div className="flex flex-col gap-3.5 pb-6">
      {/* Gauge restate — larger artwork (260px), badge + Δ-vs-week below. */}
      <Card>
        <div className="flex justify-center">
          <EnduranceGauge score={score} size={260} />
        </div>
        {data.current.badge && (
          <div className="flex justify-center">
            <EnduranceBadgePlate
              icon={data.current.badge.icon}
              label={data.current.badge.label}
              zoneColor={zone.color}
            />
          </div>
        )}
        <div className="mt-0.5 text-center text-[13px] text-halo-ink-dim">
          {t('load.endurance.delta_vs_week', { sign: deltaSign, delta })}
        </div>
      </Card>

      {/* Trend chart — zone bands behind a zone-coloured polyline. Y-axis
          ticks snap to zone boundaries (3.0k / 4.5k / 5.5k / 6.5k) so the
          line is read as "here's where the score sat in the zone ladder". */}
      <Card>
        <div className="flex items-center justify-between gap-2">
          <div className="text-[15px] font-semibold tracking-[-0.2px]">
            {t('load.endurance.trend_title')}
          </div>
          <PeriodFilter value={range} onChange={onRangeChange} />
        </div>
        {N === 0 ? (
          <div className="py-12 text-center text-[13px] text-halo-ink-dim">
            {t('load.endurance.no_data', { defaultValue: 'No history yet — come back tomorrow.' })}
          </div>
        ) : (
          <svg width={W} height={H} className="mt-2.5 block">
            {/* Zone bands — horizontal stripes. Current zone gets stronger
                opacity (0.12 vs 0.06) so the user reads "I'm in THIS band
                right now". Direction-b-halo.jsx:3770-3780. */}
            {ENDURANCE_ZONES.map((zn, i) => {
              const next = ENDURANCE_ZONES[i + 1]
              const zoneLo = Math.max(zn.min, yMin)
              const zoneHi = Math.min(next ? next.min : ENDURANCE_MAX, yMax)
              if (zoneHi <= zoneLo) return null
              return (
                <rect
                  key={zn.id}
                  x={PAD_L}
                  y={y(zoneHi)}
                  width={INNER_W}
                  height={y(zoneLo) - y(zoneHi)}
                  fill={zn.color}
                  opacity={zn.id === zone.id ? 0.12 : 0.06}
                />
              )
            })}
            {/* Y axis — boundary ticks at zone thresholds (+ visible band
                edges). Labels in k-format so the scale reads compactly. */}
            {yTicks.map(v => (
              <g key={v}>
                <line x1={PAD_L} y1={y(v)} x2={W - PAD_R} y2={y(v)} stroke="rgba(10,13,24,0.08)" strokeWidth="0.6" />
                <text x={PAD_L - 6} y={y(v) + 3} fontSize="10" fill="var(--color-ink-dim)" textAnchor="end">
                  {fmtTick(v)}
                </text>
              </g>
            ))}
            {/* Zone-coloured line runs */}
            {runs.map((run, ri) => {
              let d = ''
              for (let i = run.from; i <= run.to; i++) {
                d += (i === run.from ? 'M ' : ' L ') + x(i).toFixed(1) + ' ' + y(vals[i]).toFixed(1)
              }
              return (
                <path
                  key={ri}
                  d={d}
                  fill="none"
                  stroke={run.color}
                  strokeWidth="2.4"
                  strokeLinejoin="round"
                  strokeLinecap="round"
                />
              )
            })}
            {/* Dots — colored by the zone of each individual point */}
            {showDots &&
              vals.map((v, i) => (
                <circle
                  key={i}
                  cx={x(i)}
                  cy={y(v)}
                  r="3.6"
                  fill={enduranceZoneFor(v).color}
                  stroke="#fff"
                  strokeWidth="1.5"
                />
              ))}
            {/* Always mark the latest point */}
            {!showDots && N > 0 && (
              <circle
                cx={x(N - 1)}
                cy={y(vals[N - 1])}
                r="4"
                fill={enduranceZoneFor(vals[N - 1]).color}
                stroke="#fff"
                strokeWidth="1.8"
              />
            )}
            {labelIdx.map(i => (
              <text
                key={i}
                x={x(i)}
                y={H - 8}
                fontSize="10"
                fill="var(--color-ink-dim)"
                textAnchor={i === 0 ? 'start' : i === N - 1 ? 'end' : 'middle'}
                fontWeight="500"
              >
                {fmtMD(trend[i].date)}
              </text>
            ))}
          </svg>
        )}
      </Card>

      {/* Zone legend — 5 rows, current zone tinted + bolded. Range label
          matches what the score histogram shows on the home card. */}
      <Card>
        <div className="text-[15px] font-semibold tracking-[-0.2px]">{t('load.endurance.zones_title')}</div>
        <div className="mt-3 flex flex-col gap-2">
          {ENDURANCE_ZONES.map((zn, i) => {
            const isCurrent = zn.id === zone.id
            const next = ENDURANCE_ZONES[i + 1]
            const rangeLabel = next
              ? `${zn.min.toLocaleString('en-US').replace(/,/g, ' ')} – ${(next.min - 1)
                  .toLocaleString('en-US')
                  .replace(/,/g, ' ')}`
              : `${zn.min.toLocaleString('en-US').replace(/,/g, ' ')}+`
            return (
              <div
                key={zn.id}
                className="flex items-center justify-between rounded-chip"
                style={{
                  padding: '8px 10px',
                  background: isCurrent ? `${zn.color}14` : 'transparent',
                  border: isCurrent ? `1px solid ${zn.color}40` : '1px solid transparent',
                }}
              >
                <div className="flex items-center gap-2.5">
                  <span className="h-2.5 w-2.5 rounded-full" style={{ background: zn.color }} />
                  <span
                    className="text-[13px]"
                    style={{
                      fontWeight: isCurrent ? 700 : 500,
                      color: isCurrent ? 'var(--color-ink)' : 'var(--color-ink-dim)',
                    }}
                  >
                    {zn.labelRu}
                  </span>
                </div>
                <span className="font-mono text-[12px] font-medium text-halo-ink-dim">{rangeLabel}</span>
              </div>
            )
          })}
        </div>
      </Card>
    </div>
  )
}
