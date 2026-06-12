import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import Layout from '../components/Layout'
import { Card, ChartScrubLine, fmtScrubDate, InfoIcon, InfoPanel, PeriodFilter, useChartScrubber, type ScrubItem } from '../components/halo'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import { useApi } from '../hooks/useApi'
import { useMeasuredWidth } from '../hooks/useMeasuredWidth'
import { fmtDateYmd } from '../lib/formatters'
import { TSB_ZONES, tsbZoneOf } from '../lib/constants'
import type { ActivitiesSeries, TaperDailyTarget, TaperPlan, TrainingLoadSeries, WellnessResponse } from '../api/types'

/**
 * Training load detail (prototype `BLoadDetail` / `BLoadChart` /
 * `BTsbZoneChart` / `BSportTssChart`, direction-b-halo.jsx:1626-2395).
 * Reached by tapping the Training load card on /wellness. Period filter
 * (1m/3m/6m) → CTL/ATL line chart, a zoned Form (TSB) chart, daily TSS by
 * sport, and a collapsible per-sport CTL breakdown.
 *
 * **No forecast** — the prototype draws a 30-day dashed projection past
 * "today"; per the product decision the future side is not rendered (no
 * dashed tail, no planned bars, no forecast tint). The by-sport breakdown is
 * **CTL only** (ATL per discipline deliberately dropped for now).
 *
 * Metric vocabulary is literal English — consistent with the де-i18n'd
 * Training-load card on Wellness (CTL/ATL/TSB/Fitness/Fatigue/Form are
 * brand-standard, see Wellness.tsx `TrainingLoadCard`). Plain UI chrome that
 * is NOT metric vocabulary — the "Updated …" header and the empty state — is
 * translated (`load_detail.*`), matching the sibling Recovery/Sleep/Body
 * trend screens.
 */

type Range = '1m' | '3m' | '6m' | '1y'
const RANGE_DAYS: Record<Range, number> = { '1m': 30, '3m': 90, '6m': 180, '1y': 365 }

const LOAD_COLOR = { ctl: 'var(--color-brand)', atl: 'var(--color-coral)' }
const SPORT_COLOR = { swim: 'var(--color-amber)', ride: 'var(--color-brand)', run: 'var(--color-coral)' }
// Taper budget overlay — geometry (stepped line vs bars) + its own token keep
// "scheduled plan" and "recommended budget" visually distinct (spec Phase 4).
const TAPER_COLOR = 'var(--color-taper)'
// Single visibility gate for ALL taper surfaces (TSS overlay, legend chip,
// TSB race dot) — they must agree, or the legend points at nothing. Covers
// the 1m window (30d past + up to 28d forecast ≈ 58); when the overlay is
// visible SportTssChart stays in daily mode (a TSS budget averaged into
// weekly bars is noise). 3m+ axes (118+ slots) would render ~2px bars, so
// the overlay hides there entirely — consistently, on every surface.
const TAPER_AXIS_MAX_DAYS = 70

const fmtMd = (ymd: string) => {
  const p = ymd.split('-')
  return `${p[1]}/${p[2]}`
}
const fmtSigned = (v: number) => (v > 0 ? '+' : '') + v

export default function LoadDetail() {
  const { t } = useTranslation()
  // Default 1M — the most actionable window (current mesocycle + 28d forecast).
  // 3M+ is a drill-down for trend questions, not the entry view.
  const [range, setRange] = useState<Range>('1m')
  // CTL/ATL toggles for the top chart; at least one stays on.
  const [vis, setVis] = useState({ ctl: true, atl: true })
  const toggle = (k: 'ctl' | 'atl') =>
    setVis(v => {
      if (v[k] && Object.values(v).filter(Boolean).length === 1) return v
      return { ...v, [k]: !v[k] }
    })
  // Swim/Ride/Run toggles for the stacked-TSS chart; at least one stays on.
  const [sportVis, setSportVis] = useState({ swim: true, ride: true, run: true })
  const toggleSport = (k: 'swim' | 'ride' | 'run') =>
    setSportVis(v => {
      if (v[k] && Object.values(v).filter(Boolean).length === 1) return v
      return { ...v, [k]: !v[k] }
    })
  const [bySportOpen, setBySportOpen] = useState(false)
  // One info-panel open at a time; click the same icon to close.
  const [openTip, setOpenTip] = useState<'ctl_atl' | 'tsb' | null>(null)
  const toggleTip = (k: 'ctl_atl' | 'tsb') => setOpenTip(v => (v === k ? null : k))

  const pastDays = RANGE_DAYS[range]
  const { data: load, loading, error } = useApi<TrainingLoadSeries>(`/api/training-load?days=${pastDays}`)
  const { data: acts } = useApi<ActivitiesSeries>(`/api/activities?days=${pastDays}`)
  // Taper budget overlay (spec Phase 4). Hidden unless a plan with daily
  // targets exists — `available: false` and early mode (empty targets) both
  // degrade to "no overlay", so the chart never depends on this fetch.
  const { data: taperPlan } = useApi<TaperPlan>('/api/taper-plan')
  const taperTargets: TaperDailyTarget[] | null =
    taperPlan?.available && taperPlan.daily_targets && taperPlan.daily_targets.length > 0
      ? taperPlan.daily_targets
      : null
  // The one gate every taper surface shares (see TAPER_AXIS_MAX_DAYS).
  const taperOnChart = taperTargets != null && load != null && load.dates.length <= TAPER_AXIS_MAX_DAYS
  const today = fmtDateYmd(new Date())
  const { data: wellness } = useApi<WellnessResponse>(`/api/wellness-day?date=${today}`)
  const w = wellness?.has_data ? wellness : null
  const updatedTime = w?.updated_at
    ? new Date(w.updated_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })
    : null

  // Daily TSS per sport, indexed onto the training-load date axis so the bar
  // chart shares an x-window with the line charts. Days without an activity
  // stay 0. Past entries come from `activities` (completed Activity rows);
  // future entries come from `planned` (ScheduledWorkout rows past today) so
  // the chart can render forecast bars distinctly.
  const tssByDate: Record<string, { swim: number; ride: number; run: number }> = {}
  const sportKey = (s: string): 'swim' | 'ride' | 'run' | null =>
    s === 'swimming' ? 'swim' : s === 'cycling' ? 'ride' : s === 'running' ? 'run' : null
  for (const a of acts?.activities ?? []) {
    const k = sportKey(a.sport)
    if (!k) continue
    const bucket = (tssByDate[a.date] ??= { swim: 0, ride: 0, run: 0 })
    bucket[k] += a.tss
  }
  for (const p of acts?.planned ?? []) {
    const k = sportKey(p.sport)
    if (!k) continue
    const bucket = (tssByDate[p.date] ??= { swim: 0, ride: 0, run: 0 })
    bucket[k] += p.tss
  }

  // Indexed lookup, not `lastValid` — `/api/training-load` returns past
  // values + forecast extension up to the planned horizon, so the array's
  // last entry is end-of-forecast, not today. The headline must read today.
  const todayIdx = load ? load.dates.indexOf(load.today_date) : -1
  const ctlToday = load && todayIdx >= 0 ? load.ctl[todayIdx] : null
  const atlToday = load && todayIdx >= 0 ? load.atl[todayIdx] : null
  const tsbToday = load && todayIdx >= 0 ? load.tsb[todayIdx] : null

  // Form's chip + value share the active TSB zone colour (risk/optimal/gray/
  // fresh/transition — same gradation as the zoned chart below and the
  // Wellness Training-load card).
  const tsbColor = tsbToday != null ? tsbZoneOf(tsbToday).line : 'var(--color-ink-dim)'
  const headline: { k: string; sub: string; val: number | null; color: string; signed?: boolean }[] = [
    { k: 'Fitness', sub: 'CTL', val: ctlToday, color: LOAD_COLOR.ctl },
    { k: 'Fatigue', sub: 'ATL', val: atlToday, color: LOAD_COLOR.atl },
    { k: 'Form', sub: 'TSB', val: tsbToday, color: tsbColor, signed: true },
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
            <span className="pr-1 text-xs text-halo-ink-dim">{t('load_detail.updated', { time: updatedTime })}</span>
          )}
        </header>

        {loading && !load && <LoadingSpinner />}
        {error && !load && <ErrorMessage message={t('wellness.load_error')} />}

        {load && (
          <div className="flex flex-col gap-3.5 pb-6">
            <div>
              <div className="text-[22px] font-semibold tracking-[-0.4px]">Training load</div>
              <div className="mt-0.5 text-[13px] text-halo-ink-dim">CTL / ATL / TSB · last {pastDays} days</div>
            </div>

            {/* Headline — CTL/ATL/TSB today, colour-keyed to the charts. */}
            <Card>
              <div className="grid grid-cols-3 gap-3">
                {headline.map(m => (
                  <div key={m.sub}>
                    <div className="inline-flex items-center gap-1.5">
                      <span className="h-2 w-2 rounded-sm" style={{ background: m.color }} />
                      <span className="text-[11px] font-semibold text-halo-ink-dim">{m.k}</span>
                    </div>
                    <div
                      className="mt-1 text-[26px] font-semibold tracking-[-0.5px]"
                      style={{ color: m.signed ? m.color : 'var(--color-ink)' }}
                    >
                      {m.val == null ? '—' : m.signed ? fmtSigned(m.val) : m.val}
                    </div>
                    <div className="mt-px text-[9px] font-bold uppercase tracking-[0.6px] text-halo-ink-dimmer">
                      {m.sub}
                    </div>
                  </div>
                ))}
              </div>
            </Card>

            {/* Period filter */}
            <PeriodFilter value={range} onChange={setRange} />

            {load.dates.length === 0 ? (
              <Card>
                <div className="py-10 text-center text-[13px] text-halo-ink-dim">{t('load_detail.no_data')}</div>
              </Card>
            ) : (
              <>
                {/* Fitness & fatigue — CTL + ATL lines, toggleable. */}
                <Card>
                  <div className="mb-1 flex items-center justify-center text-[13px] font-semibold text-halo-ink">
                    {/* Invisible spacer mirrors the InfoIcon width (h-5 + ml-1.5) so
                        the title stays optically centered in the card. */}
                    <span aria-hidden className="mr-1.5 inline-block h-5 w-5" />
                    <span>Fitness &amp; fatigue</span>
                    <InfoIcon open={openTip === 'ctl_atl'} onClick={() => toggleTip('ctl_atl')} />
                  </div>
                  {openTip === 'ctl_atl' && <InfoPanel>{t('load_detail.tip.ctl_atl')}</InfoPanel>}
                  <LoadLineChart
                    dates={load.dates}
                    lines={[
                      ...(vis.ctl ? [{ label: 'CTL', values: load.ctl, color: LOAD_COLOR.ctl }] : []),
                      ...(vis.atl ? [{ label: 'ATL', values: load.atl, color: LOAD_COLOR.atl }] : []),
                    ]}
                    height={200}
                    todayIdx={load.dates.indexOf(load.today_date)}
                  />
                  <div className="mt-2 flex flex-wrap justify-center gap-1.5">
                    <LegendToggle on={vis.ctl} color={LOAD_COLOR.ctl} label="CTL · Fitness" onClick={() => toggle('ctl')} />
                    <LegendToggle on={vis.atl} color={LOAD_COLOR.atl} label="ATL · Fatigue" onClick={() => toggle('atl')} />
                  </div>
                </Card>

                {/* Form (TSB) — zoned chart, always visible. */}
                <Card>
                  <div className="mb-1.5 flex items-baseline justify-between">
                    <div className="flex items-center text-[13px] font-semibold text-halo-ink">
                      <span>Form (TSB)</span>
                      <InfoIcon open={openTip === 'tsb'} onClick={() => toggleTip('tsb')} />
                    </div>
                    {tsbToday != null &&
                      (() => {
                        const z = tsbZoneOf(tsbToday)
                        return (
                          <div
                            className="inline-flex items-center gap-1.5 text-[11px] font-semibold"
                            style={{ color: z.line }}
                          >
                            <span className="h-2 w-2 rounded-sm" style={{ background: z.line }} />
                            Today · {z.label.toLowerCase()}
                          </div>
                        )
                      })()}
                  </div>
                  {openTip === 'tsb' && <InfoPanel>{t('load_detail.tip.tsb')}</InfoPanel>}
                  {/* TSB chart spans past + forecast — backend extends the
                      tsb array forward via overall CTL/ATL projection (see
                      api/routers/dashboard.py). We pair date+TSB and drop
                      interior nulls before passing in so the chart's index
                      math stays consistent. todayIdx tells the chart where
                      to split the line (solid actual → dashed forecast). */}
                  {(() => {
                    const paired = load.dates
                      .map((d, i) => ({ d, v: load.tsb[i] }))
                      .filter((p): p is { d: string; v: number } => p.v != null)
                    const todayIdxFull = paired.findIndex(p => p.d === load.today_date)
                    // Taper's projected race-day landing — only when the race
                    // date is inside the chart axis (it may sit past the
                    // forecast horizon on short windows; then no marker).
                    const raceIdx =
                      taperOnChart && taperPlan?.race_date && taperPlan.projected_race_day
                        ? paired.findIndex(p => p.d === taperPlan.race_date)
                        : -1
                    return (
                      <TsbZoneChart
                        dates={paired.map(p => p.d)}
                        tsb={paired.map(p => p.v)}
                        todayIdx={todayIdxFull >= 0 ? todayIdxFull : null}
                        raceDay={raceIdx >= 0 ? { idx: raceIdx, tsb: taperPlan!.projected_race_day!.tsb } : null}
                      />
                    )
                  })()}
                  {/* Zone legend — Transition → High risk, top-to-bottom mirrors
                      the band stack. */}
                  <div className="mt-2 grid grid-cols-5 gap-1">
                    {[...TSB_ZONES].reverse().map(z => (
                      <div key={z.id} className="flex flex-col items-center gap-0.5">
                        <span className="h-1 w-full rounded-sm" style={{ background: z.line, opacity: 0.85 }} />
                        <span
                          className="text-center text-[9.5px] font-semibold leading-tight"
                          style={{ color: z.line }}
                        >
                          {z.label}
                        </span>
                      </div>
                    ))}
                  </div>
                </Card>

                {/* Daily TSS by sport — stacked bars. */}
                <Card>
                  <div className="mb-1 text-center text-[13px] font-semibold text-halo-ink">Daily TSS by sport</div>
                  <SportTssChart
                    dates={load.dates}
                    swim={load.dates.map(d => tssByDate[d]?.swim ?? 0)}
                    ride={load.dates.map(d => tssByDate[d]?.ride ?? 0)}
                    run={load.dates.map(d => tssByDate[d]?.run ?? 0)}
                    show={sportVis}
                    todayIdx={load.dates.indexOf(load.today_date)}
                    taper={taperOnChart ? taperTargets : null}
                  />
                  <div className="mt-2 flex flex-wrap justify-center gap-1.5">
                    <LegendToggle on={sportVis.swim} color={SPORT_COLOR.swim} label="Swim" square onClick={() => toggleSport('swim')} />
                    <LegendToggle on={sportVis.ride} color={SPORT_COLOR.ride} label="Ride" square onClick={() => toggleSport('ride')} />
                    <LegendToggle on={sportVis.run} color={SPORT_COLOR.run} label="Run" square onClick={() => toggleSport('run')} />
                    {taperOnChart && (
                      <span className="inline-flex items-center gap-1.5 rounded-full px-2 py-0.5 text-[11px] text-halo-ink-dim">
                        <span className="h-0.5 w-3 rounded-sm" style={{ background: TAPER_COLOR }} />
                        Taper budget
                      </span>
                    )}
                  </div>
                </Card>

                {/* Per-sport CTL — today's snapshot, proportional bars across
                    disciplines so the user sees at a glance which sport
                    carries their fitness. Reads `sport_ctl` from today's
                    wellness row (NOT the time series), so it's invariant to
                    the period filter above — placed here, next to the longer
                    «By sport» trend drill-down, so the snapshot + series
                    pair reads as one «per-sport block». Design:
                    direction-b-halo.jsx:4984. */}
                <PerSportCtlCard sportCtl={w?.training_load.sport_ctl ?? null} totalCtl={ctlToday} />

                {/* By sport — collapsible per-discipline CTL trend. */}
                <button
                  type="button"
                  onClick={() => setBySportOpen(o => !o)}
                  aria-expanded={bySportOpen}
                  className="flex items-center justify-between rounded-card border border-halo-border bg-halo-surface px-4 py-3 shadow-card"
                >
                  <span className="inline-flex items-baseline gap-2">
                    <span className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">By sport</span>
                    <span className="text-[11px] text-halo-ink-dimmer">CTL + ATL per discipline</span>
                  </span>
                  <span
                    aria-hidden="true"
                    className={`text-base leading-none text-halo-ink-dim transition-transform ${bySportOpen ? 'rotate-90' : ''}`}
                  >
                    ›
                  </span>
                </button>

                {bySportOpen &&
                  ([
                    { key: 'swim', label: 'Swim', ctlValues: load.ctl_swim, atlValues: load.atl_swim, color: SPORT_COLOR.swim },
                    { key: 'ride', label: 'Ride', ctlValues: load.ctl_ride, atlValues: load.atl_ride, color: SPORT_COLOR.ride },
                    { key: 'run', label: 'Run', ctlValues: load.ctl_run, atlValues: load.atl_run, color: SPORT_COLOR.run },
                  ] as const).map(sp => {
                    // Same indexed-today lookup as headline — forecast tail
                    // would otherwise be shown as the per-sport "current".
                    const sportCtl = todayIdx >= 0 ? sp.ctlValues[todayIdx] : null
                    const sportAtl = todayIdx >= 0 ? sp.atlValues[todayIdx] : null
                    // Hide the whole card if a sport has no CTL trend at all —
                    // ATL is meaningless without it. Pre-Step-1.5 backfill rows
                    // carry only CTL → ATL stays absent on legacy days.
                    if (sportCtl == null) return null
                    return (
                      <Card key={sp.key}>
                        <div className="flex items-center justify-between">
                          <span className="inline-flex items-center gap-2">
                            <span className="h-2.5 w-2.5 rounded-[3px]" style={{ background: sp.color }} />
                            <span className="text-sm font-semibold">{sp.label}</span>
                          </span>
                          <span className="text-xs text-halo-ink-dim">
                            <span className="font-semibold" style={{ color: LOAD_COLOR.ctl }}>{sportCtl}</span>{' '}
                            <span className="text-[10px] text-halo-ink-dimmer">CTL</span>
                            {sportAtl != null && (
                              <>
                                <span className="mx-1 text-halo-ink-dimmer">·</span>
                                <span className="font-semibold" style={{ color: LOAD_COLOR.atl }}>{sportAtl}</span>{' '}
                                <span className="text-[10px] text-halo-ink-dimmer">ATL</span>
                              </>
                            )}
                          </span>
                        </div>
                        <div className="mt-2">
                          <LoadLineChart
                            dates={load.dates}
                            lines={[
                              { label: 'CTL', values: sp.ctlValues, color: LOAD_COLOR.ctl },
                              ...(sportAtl != null
                                ? [{ label: 'ATL', values: sp.atlValues, color: LOAD_COLOR.atl }]
                                : []),
                            ]}
                            height={120}
                            todayIdx={load.dates.indexOf(load.today_date)}
                          />
                        </div>
                      </Card>
                    )
                  })}
              </>
            )}
          </div>
        )}
      </div>
    </Layout>
  )
}

// Per-sport CTL widget — today's snapshot rendered as three proportional
// bars (Swim/Ride/Run). Bar length = sport_ctl / max(sport_ctls); the right-
// column `%` = sport_ctl / sum(sport_ctls). The header «total» on the right
// shows overall CTL (not the sum of per-sport CTLs — they don't necessarily
// sum to it). Returns `null` if today's wellness row is missing — caller
// stays clean of conditional rendering.
function PerSportCtlCard({
  sportCtl,
  totalCtl,
}: {
  sportCtl: { swim: number | null; ride: number | null; run: number | null } | null
  totalCtl: number | null
}) {
  if (!sportCtl) return null
  const sports: { k: 'swim' | 'ride' | 'run'; label: string; val: number }[] = [
    { k: 'swim', label: 'Swim', val: sportCtl.swim ?? 0 },
    { k: 'ride', label: 'Ride', val: sportCtl.ride ?? 0 },
    { k: 'run', label: 'Run', val: sportCtl.run ?? 0 },
  ]
  const sum = sports.reduce((a, s) => a + s.val, 0)
  const max = Math.max(...sports.map(s => s.val))
  if (sum <= 0) return null
  return (
    <div className="rounded-card border border-halo-border bg-halo-surface p-4 shadow-card">
      <div className="mb-3 flex items-baseline justify-between">
        <span className="text-[9px] font-bold uppercase tracking-[0.6px] text-halo-ink-dimmer">Per-sport CTL</span>
        <span className="text-[11px] text-halo-ink-dim">
          Today{totalCtl != null && <> · {totalCtl} total</>}
        </span>
      </div>
      <div className="flex flex-col gap-3">
        {sports.map(sp => {
          const pct = max > 0 ? (sp.val / max) * 100 : 0
          const share = sum > 0 ? Math.round((sp.val / sum) * 100) : 0
          const color = SPORT_COLOR[sp.k]
          return (
            <div key={sp.k} className="grid items-center gap-3" style={{ gridTemplateColumns: '56px 1fr auto' }}>
              <div className="inline-flex items-center gap-1.5">
                <span className="h-2 w-2 shrink-0 rounded-sm" style={{ background: color }} />
                <span className="text-[13px] font-medium text-halo-ink">{sp.label}</span>
              </div>
              <div className="relative h-1.5 overflow-hidden rounded-pill bg-halo-surface-2">
                <div
                  className="absolute inset-y-0 left-0 rounded-pill"
                  style={{ width: `${pct}%`, background: color }}
                />
              </div>
              <div className="inline-flex min-w-[64px] items-baseline justify-end gap-1.5">
                <span className="text-[15px] font-semibold tracking-[-0.3px] text-halo-ink tabular-nums">{sp.val}</span>
                <span className="text-[11px] font-medium text-halo-ink-dimmer tabular-nums">{share}%</span>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// Legend chip — line swatch (CTL/ATL) or square swatch (sports), struck
// through when the series is off.
function LegendToggle({
  on,
  color,
  label,
  square,
  onClick,
}: {
  on: boolean
  color: string
  label: string
  square?: boolean
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
        className={square ? 'h-2.5 w-2.5 rounded-[2px]' : 'h-0.5 w-3.5 rounded-sm'}
        style={{ background: on ? color : 'var(--color-ink-dimmer)', opacity: on ? 1 : 0.4 }}
      />
      <span className={on ? '' : 'line-through'}>{label}</span>
    </button>
  )
}

// Build an SVG polyline through non-null points only (spans gaps).
function linePath(
  values: (number | null)[],
  x: (i: number) => number,
  y: (v: number) => number,
  from = 0,
  to = values.length - 1,
): string {
  let d = ''
  let started = false
  for (let i = from; i <= to; i++) {
    const v = values[i]
    if (v == null) {
      started = false  // gap — let the next non-null start a fresh sub-path
      continue
    }
    d += (started ? ' L ' : 'M ') + x(i).toFixed(1) + ' ' + y(v).toFixed(1)
    started = true
  }
  return d
}

// ─────────────────────────────────────────────────────────────────────────────
// CTL/ATL (or per-sport CTL) line chart — auto-fit y range floored at 0.
// When `todayIdx` is provided and points to an interior index, the chart
// splits each line into solid (actual) + dashed (forecast) segments, tints
// the forecast region, and drops a "today" rule. Otherwise renders as a
// single solid line (legacy overall chart, past-only per-sport series).
// viewBox width is measured per-frame so 1 viewBox unit == 1 CSS pixel — no
// horizontal stretch even when the card is desktop-wide.
// ─────────────────────────────────────────────────────────────────────────────
function LoadLineChart({
  dates,
  lines,
  height = 200,
  todayIdx = null,
}: {
  dates: string[]
  lines: { label: string; values: (number | null)[]; color: string }[]
  height?: number
  todayIdx?: number | null
}) {
  const [wrapRef, W] = useMeasuredWidth<HTMLDivElement>(320)
  const H = height
  const pad = { l: 28, r: 10, t: 14, b: 22 }
  const innerW = W - pad.l - pad.r
  const innerH = H - pad.t - pad.b
  const N = dates.length

  const vals: number[] = []
  for (const l of lines) for (const v of l.values) if (v != null) vals.push(v)
  let yMin = vals.length ? Math.min(0, ...vals) : 0
  let yMax = vals.length ? Math.max(...vals) : 100
  const yPad = (yMax - yMin) * 0.08 || 1
  yMin = Math.floor((yMin - yPad) / 10) * 10
  yMax = Math.ceil((yMax + yPad) / 10) * 10
  if (yMax <= yMin) yMax = yMin + 10

  const x = (i: number) => pad.l + (N <= 1 ? innerW / 2 : (i / (N - 1)) * innerW)
  const y = (v: number) => pad.t + innerH - ((v - yMin) / (yMax - yMin)) * innerH

  const yTicks = [0, 1, 2, 3, 4].map(i => yMin + (i * (yMax - yMin)) / 4)
  const xCount = Math.min(5, N)
  const xLabels: number[] = []
  for (let i = 0; i < xCount; i++) xLabels.push(xCount === 1 ? 0 : Math.round((i * (N - 1)) / (xCount - 1)))

  const { svgRef, idx: scrubIdx, handlers } = useChartScrubber(N, pad.l, innerW)
  const scrubItems: ScrubItem[] =
    scrubIdx == null
      ? []
      : lines.flatMap(l =>
          l.values[scrubIdx] == null
            ? []
            : [{ label: l.label, value: Math.round(l.values[scrubIdx] as number), color: l.color }],
        )

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
          y1={y(tick)}
          x2={W - pad.r}
          y2={y(tick)}
          stroke="var(--color-border)"
          strokeWidth="1"
          strokeDasharray={tick === 0 ? undefined : '2 3'}
          opacity={tick === 0 ? 0.6 : 0.5}
        />
      ))}
      {yTicks.map((tick, i) => (
        <text key={`y${i}`} x={pad.l - 6} y={y(tick) + 3} fontSize="9" fill="var(--color-ink-dim)" textAnchor="end">
          {Math.round(tick)}
        </text>
      ))}
      {/* Forecast region — soft tint right of today_rule, before lines so
          the strokes paint on top. Only when today is strictly interior. */}
      {todayIdx != null && todayIdx >= 0 && todayIdx < N - 1 && (
        <rect
          x={x(todayIdx)}
          y={pad.t}
          width={x(N - 1) - x(todayIdx)}
          height={innerH}
          fill="var(--color-ink)"
          opacity="0.04"
        />
      )}
      {lines.map((l, i) => {
        const split = todayIdx != null && todayIdx >= 0 && todayIdx < N - 1
        if (!split) {
          return (
            <path
              key={i}
              d={linePath(l.values, x, y)}
              fill="none"
              stroke={l.color}
              strokeWidth="1.7"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
          )
        }
        return (
          <g key={i}>
            {/* Solid actual — overlaps the forecast start at todayIdx so the
                two segments meet seamlessly without a visible gap. */}
            <path
              d={linePath(l.values, x, y, 0, todayIdx)}
              fill="none"
              stroke={l.color}
              strokeWidth="1.7"
              strokeLinejoin="round"
              strokeLinecap="round"
            />
            <path
              d={linePath(l.values, x, y, todayIdx, N - 1)}
              fill="none"
              stroke={l.color}
              strokeWidth="1.7"
              strokeDasharray="4 3"
              strokeLinejoin="round"
              strokeLinecap="round"
              opacity="0.85"
            />
          </g>
        )
      })}
      {/* Today rule — a thin dashed vertical line where actual hands off
          to forecast. Drawn after lines so it stays visible at intersections.
          The "TODAY" badge anchors the eye on the split — when forecast tail
          is short on a 1M window it's the only signal that the dashed segment
          ahead is the future, not just a render artefact. */}
      {todayIdx != null && todayIdx >= 0 && todayIdx < N - 1 && (
        <>
          <line
            x1={x(todayIdx)}
            y1={pad.t}
            x2={x(todayIdx)}
            y2={H - pad.b}
            stroke="var(--color-ink-dim)"
            strokeWidth="1"
            strokeDasharray="3 3"
            opacity="0.55"
          />
          <text
            x={x(todayIdx) + 4}
            y={pad.t + 8}
            fontSize="9"
            fontWeight="600"
            letterSpacing="0.6"
            fill="var(--color-ink-dim)"
          >
            TODAY
          </text>
        </>
      )}
      {xLabels.map((idx, i) => (
        <text
          key={`x${i}`}
          x={x(idx)}
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

// ─────────────────────────────────────────────────────────────────────────────
// Form (TSB) chart — TSB over 5 fixed PMC-style zone bands; the line is split
// into runs and each run drawn in its band's colour. Fixed y −40…+35.
// When `todayIdx` points to an interior index, each per-zone run is further
// split into actual + forecast sub-paths — actual stays solid, forecast goes
// dashed over a soft tint band. Mirrors direction-b-halo.jsx BTsbZoneChart.
// ─────────────────────────────────────────────────────────────────────────────
function TsbZoneChart({
  dates,
  tsb,
  todayIdx = null,
  raceDay = null,
}: {
  dates: string[]
  tsb: number[]
  todayIdx?: number | null
  // Taper planner's projected race-day TSB landing (spec Phase 4): one dot at
  // the race date. Deliberately NOT a second dashed curve — two dash styles
  // in the same axes are unreadable (spec decisions log 2026-06-12).
  raceDay?: { idx: number; tsb: number } | null
}) {
  const [wrapRef, W] = useMeasuredWidth<HTMLDivElement>(320)
  const H = 160
  const pad = { l: 28, r: 10, t: 8, b: 22 }
  const innerW = W - pad.l - pad.r
  const innerH = H - pad.t - pad.b
  const N = dates.length

  const yMin = -40
  const yMax = 35
  const x = (i: number) => pad.l + (N <= 1 ? innerW / 2 : (i / (N - 1)) * innerW)
  const y = (v: number) => pad.t + innerH - ((v - yMin) / (yMax - yMin)) * innerH

  // Runs of consecutive same-zone points; a zone change starts the next run
  // from the previous point so segments join.
  const runs: { zone: number; from: number; to: number }[] = []
  let cur: { zone: number; from: number; to: number } | null = null
  for (let i = 0; i < N; i++) {
    const z = TSB_ZONES.indexOf(tsbZoneOf(tsb[i]))
    if (!cur) cur = { zone: z, from: i, to: i }
    else if (cur.zone === z) cur.to = i
    else {
      runs.push(cur)
      cur = { zone: z, from: i - 1, to: i }
    }
  }
  if (cur) runs.push(cur)
  const runPath = (from: number, to: number) => {
    let d = ''
    for (let i = from; i <= to; i++) d += (i === from ? 'M ' : ' L ') + x(i).toFixed(1) + ' ' + y(tsb[i]).toFixed(1)
    return d
  }

  const yLabels = [-30, -10, 0, 5, 25]
  const xCount = Math.min(5, N)
  const xLabels: number[] = []
  for (let i = 0; i < xCount; i++) xLabels.push(xCount === 1 ? 0 : Math.round((i * (N - 1)) / (xCount - 1)))

  const { svgRef, idx: scrubIdx, handlers } = useChartScrubber(N, pad.l, innerW)
  const scrubItems: ScrubItem[] =
    scrubIdx == null
      ? []
      : (() => {
          const v = tsb[scrubIdx]
          const z = tsbZoneOf(v)
          return [
            { label: 'TSB', value: `${v >= 0 ? '+' : ''}${Math.round(v)}`, color: z.line },
            { label: '', value: z.label, color: z.line },
          ]
        })()

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
      {/* Zone bands */}
      {TSB_ZONES.map(z => {
        const lo = Math.max(z.lo, yMin)
        const hi = Math.min(z.hi, yMax)
        if (hi <= lo) return null
        return <rect key={z.id} x={pad.l} y={y(hi)} width={innerW} height={y(lo) - y(hi)} fill={z.fill} />
      })}
      {/* Forecast tint — soft overlay right of today_rule. Painted BEFORE
          the colored line strokes so they still read on top. */}
      {todayIdx != null && todayIdx >= 0 && todayIdx < N - 1 && (
        <rect
          x={x(todayIdx)}
          y={pad.t}
          width={x(N - 1) - x(todayIdx)}
          height={innerH}
          fill="var(--color-ink)"
          opacity="0.04"
        />
      )}
      {/* Zero line */}
      <line x1={pad.l} y1={y(0)} x2={pad.l + innerW} y2={y(0)} stroke="var(--color-ink-dim)" strokeWidth="0.8" opacity="0.4" />
      {yLabels.map(v => (
        <text key={`y${v}`} x={pad.l - 6} y={y(v) + 3} fontSize="9" fill="var(--color-ink-dim)" textAnchor="end">
          {v}
        </text>
      ))}
      {/* TSB line — per-zone runs split at todayIdx into actual+forecast
          sub-paths. Forecast goes dashed; both keep the zone colour so the
          eye reads zone transitions identically on either side of today.
          Mirrors design's buildPaths (direction-b-halo.jsx:4703). */}
      {runs.flatMap((r, ri) => {
        const subs: { kind: 'actual' | 'forecast'; d: string }[] = []
        if (todayIdx == null || todayIdx >= N - 1) {
          subs.push({ kind: 'actual', d: runPath(r.from, r.to) })
        } else {
          const aTo = Math.min(r.to, todayIdx)
          const fFrom = Math.max(r.from, todayIdx)
          if (aTo >= r.from) subs.push({ kind: 'actual', d: runPath(r.from, aTo) })
          if (r.to > todayIdx) subs.push({ kind: 'forecast', d: runPath(fFrom, r.to) })
        }
        return subs
          .filter(s => s.d)
          .map((s, si) => (
            <path
              key={`r${ri}-${si}`}
              d={s.d}
              fill="none"
              stroke={TSB_ZONES[r.zone].line}
              strokeWidth="1.6"
              strokeLinejoin="round"
              strokeLinecap="round"
              strokeDasharray={s.kind === 'forecast' ? '4 3' : undefined}
              opacity={s.kind === 'forecast' ? 0.9 : 1}
            />
          ))
      })}
      {/* Race-day landing dot — the taper plan's projected TSB. Value can
          exceed the fixed y-domain (deep transition); clamp the dot but keep
          the real number in the label so nothing is silently misplaced. */}
      {raceDay && raceDay.idx >= 0 && raceDay.idx < N && (() => {
        const v = Math.max(yMin, Math.min(yMax, raceDay.tsb))
        const zone = tsbZoneOf(raceDay.tsb)
        return (
          <g>
            <circle cx={x(raceDay.idx)} cy={y(v)} r="4.5" fill={TAPER_COLOR} opacity="0.25" />
            <circle cx={x(raceDay.idx)} cy={y(v)} r="2.8" fill={zone.line} stroke="var(--color-surface)" strokeWidth="1.2" />
            <text x={x(raceDay.idx) - 5} y={y(v) - 6} fontSize="9" fontWeight="600" fill={zone.line} textAnchor="end">
              RACE {raceDay.tsb >= 0 ? '+' : ''}{Math.round(raceDay.tsb)}
            </text>
          </g>
        )
      })()}
      {/* Today rule + TODAY badge — dashed vertical line at the actual/forecast
          handoff, with a small uppercase label so the user reads the split. */}
      {todayIdx != null && todayIdx >= 0 && todayIdx < N - 1 && (
        <>
          <line
            x1={x(todayIdx)}
            y1={pad.t}
            x2={x(todayIdx)}
            y2={H - pad.b}
            stroke="var(--color-ink-dim)"
            strokeWidth="1"
            strokeDasharray="3 3"
            opacity="0.55"
          />
          <text
            x={x(todayIdx) + 4}
            y={pad.t + 8}
            fontSize="9"
            fontWeight="600"
            letterSpacing="0.6"
            fill="var(--color-ink-dim)"
          >
            TODAY
          </text>
        </>
      )}
      {xLabels.map((idx, i) => (
        <text
          key={`x${i}`}
          x={x(idx)}
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

// ─────────────────────────────────────────────────────────────────────────────
// Daily TSS by sport — stacked Swim/Ride/Run bars. Past ~45 slots the window
// auto-aggregates to weekly bars so bars stay readable — UNLESS the taper
// overlay is present (then daily, see `weekly`). Future bars render as
// forecast (hatched planned workouts); taper budget draws as a stepped line.
//
// `swim`/`ride`/`run` MUST be the same length as `dates` and index-aligned to
// it — the caller builds them via `dates.map(...)`, so a weekly chunk `[i, i+6]`
// indexes all three arrays safely. Weekly aggregation SUMS the chunk (TSS is
// additive — unlike the averaging in SleepTrend/BodyTrend's bar charts).
// ─────────────────────────────────────────────────────────────────────────────
function SportTssChart({
  dates,
  swim,
  ride,
  run,
  show,
  todayIdx = null,
  taper = null,
}: {
  dates: string[]
  swim: number[]
  ride: number[]
  run: number[]
  show: { swim: boolean; ride: boolean; run: boolean }
  // Bars at index > todayIdx render as forecast (planned workouts): opacity
  // 0.55 + diagonal-hatched pattern overlay so the eye reads them as "future"
  // distinct from the solid past actuals. Null → no split (legacy past-only).
  todayIdx?: number | null
  // Taper-budget daily targets (spec Phase 4): a stepped LINE over the future
  // bars — geometry separates "scheduled plan" (bars) from "recommended
  // budget" (line); a bar poking above the line = a day over budget. A
  // non-null prop forces DAILY mode (weekly aggregation would average the
  // budget into noise) — the caller guarantees the axis fits (gated by
  // `taperOnChart` / TAPER_AXIS_MAX_DAYS, the shared gate for all taper
  // surfaces). The last entry is race day (target 0).
  taper?: TaperDailyTarget[] | null
}) {
  const [wrapRef, W] = useMeasuredWidth<HTMLDivElement>(320)
  const H = 200
  const pad = { l: 28, r: 8, t: 10, b: 22 }
  const innerW = W - pad.l - pad.r
  const innerH = H - pad.t - pad.b
  const N = dates.length

  type Bar = { date: string; sw: number; ri: number; rn: number; isForecast: boolean }
  // Daily mode whenever the taper overlay is present — see the `taper` prop
  // doc. Without it the 1m window (30d past + forecast extension ≈ 58 slots)
  // would aggregate weekly and silently drop the overlay.
  const weekly = N > 45 && !taper
  const bars: Bar[] = []
  if (weekly) {
    // Weekly aggregation. A week is forecast only when ALL 7 days are past
    // todayIdx; mixed weeks (today falls inside) render as actual so we don't
    // accidentally mark "this week" — partially done — as forecast.
    for (let i = 0; i < N; i += 7) {
      const end = Math.min(i + 6, N - 1)
      let sw = 0
      let ri = 0
      let rn = 0
      let hasPast = false
      for (let k = i; k <= end; k++) {
        sw += swim[k]
        ri += ride[k]
        rn += run[k]
        if (todayIdx == null || k <= todayIdx) hasPast = true
      }
      bars.push({ date: dates[i], sw, ri, rn, isForecast: !hasPast })
    }
  } else {
    for (let i = 0; i < N; i++)
      bars.push({
        date: dates[i],
        sw: swim[i],
        ri: ride[i],
        rn: run[i],
        isForecast: todayIdx != null && i > todayIdx,
      })
  }
  const M = bars.length

  // Taper budget points mapped onto chart indices — a non-null `taper` means
  // daily mode (bar index === date index). Off-axis targets (race past the
  // forecast horizon) are silently dropped; the step line just ends at the edge.
  const taperPts = taper
    ? taper.map(t => ({ i: dates.indexOf(t.date), tss: t.target_tss })).filter(p => p.i >= 0)
    : []
  const taperByIdx = new Map(taperPts.map(p => [p.i, p.tss]))
  // Race day = the taper plan's last entry (target 0); marker only when on-axis.
  const raceIdx = taper && taper.length > 0 ? dates.indexOf(taper[taper.length - 1].date) : -1

  const stackTotal = bars.map(
    b => (show.swim ? b.sw : 0) + (show.ride ? b.ri : 0) + (show.run ? b.rn : 0),
  )
  // Budget line must fit the y-domain even when planned bars are small/zero —
  // otherwise the overlay renders off-scale above the plot.
  const rawMax = Math.max(60, ...stackTotal, ...taperPts.map(p => p.tss))
  const niceStep = rawMax > 800 ? 200 : rawMax > 400 ? 100 : rawMax > 200 ? 50 : rawMax > 100 ? 25 : 20
  const yMax = Math.ceil(rawMax / niceStep) * niceStep

  const slotW = innerW / M
  const barW = Math.max(2, slotW * (weekly ? 0.74 : 0.78))
  const xOf = (i: number) => pad.l + i * slotW + (slotW - barW) / 2
  const yOf = (v: number) => pad.t + innerH - (v / yMax) * innerH

  const yTicks: number[] = []
  for (let v = 0; v <= yMax; v += niceStep) yTicks.push(v)
  const xCount = Math.min(5, M)
  const xLabels: number[] = []
  for (let i = 0; i < xCount; i++) xLabels.push(xCount === 1 ? 0 : Math.round((i * (M - 1)) / (xCount - 1)))

  const { svgRef, idx: scrubIdx, handlers } = useChartScrubber(M, pad.l, innerW)
  const scrubBar = scrubIdx == null ? null : bars[scrubIdx]
  const scrubItems: ScrubItem[] =
    scrubBar == null
      ? []
      : [
          ...(show.swim ? [{ label: 'Swim', value: Math.round(scrubBar.sw), color: SPORT_COLOR.swim }] : []),
          ...(show.ride ? [{ label: 'Ride', value: Math.round(scrubBar.ri), color: SPORT_COLOR.ride }] : []),
          ...(show.run ? [{ label: 'Run', value: Math.round(scrubBar.rn), color: SPORT_COLOR.run }] : []),
          ...(scrubIdx != null && taperByIdx.has(scrubIdx)
            ? [{ label: 'Taper', value: Math.round(taperByIdx.get(scrubIdx)!), color: TAPER_COLOR }]
            : []),
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
          x2={W - pad.r}
          y2={yOf(tick)}
          stroke="var(--color-border)"
          strokeWidth="1"
          strokeDasharray={tick === 0 ? undefined : '2 3'}
          opacity={tick === 0 ? 0.6 : 0.45}
        />
      ))}
      {yTicks.map((tick, i) => (
        <text key={`yt${i}`} x={pad.l - 6} y={yOf(tick) + 3} fontSize="9" fill="var(--color-ink-dim)" textAnchor="end">
          {tick}
        </text>
      ))}
      {/* Diagonal-hatched pattern used to overlay forecast bars — mirrors
          the design's `patternId` pattern (direction-b-halo.jsx:4885). */}
      <defs>
        <pattern id="sport-tss-hatch" patternUnits="userSpaceOnUse" width="4" height="4" patternTransform="rotate(45)">
          <rect width="4" height="4" fill="rgba(255,255,255,0.55)" />
          <line x1="0" y1="0" x2="0" y2="4" stroke="rgba(0,0,0,0.18)" strokeWidth="1.6" />
        </pattern>
      </defs>
      {/* Forecast tint band right of today rule — soft overlay so the eye
          gets a continuous "future" cue even on toggled-off-everything bars. */}
      {todayIdx != null && todayIdx >= 0 && todayIdx < N - 1 && (() => {
        // For weekly mode todayIdx is in daily-index space, not bar-index
        // space. Place the rule by daily fraction so it stays anchored to
        // the actual today regardless of aggregation.
        const todayBarX = pad.l + ((todayIdx + 1) / N) * innerW
        return (
          <rect
            x={todayBarX}
            y={pad.t}
            width={Math.max(0, W - pad.r - todayBarX)}
            height={innerH}
            fill="var(--color-ink)"
            opacity="0.04"
          />
        )
      })()}
      {/* Taper window tint — painted under the bars so the budget region
          reads as a zone, not a layer hiding data. */}
      {taperPts.length > 0 && (() => {
        const x0 = pad.l + taperPts[0].i * slotW
        const x1 = pad.l + (taperPts[taperPts.length - 1].i + 1) * slotW
        return <rect x={x0} y={pad.t} width={Math.max(0, x1 - x0)} height={innerH} fill={TAPER_COLOR} opacity="0.05" />
      })()}
      {bars.map((b, i) => {
        const segs = [
          { v: show.swim ? b.sw : 0, c: SPORT_COLOR.swim },
          { v: show.ride ? b.ri : 0, c: SPORT_COLOR.ride },
          { v: show.run ? b.rn : 0, c: SPORT_COLOR.run },
        ]
        let cursor = yOf(0)
        return (
          <g key={i}>
            {segs.map((seg, si) => {
              if (seg.v <= 0) return null
              const h = yOf(0) - yOf(seg.v)
              cursor -= h
              const segY = cursor
              return (
                <g key={si}>
                  <rect
                    x={xOf(i)}
                    y={segY}
                    width={barW}
                    height={h}
                    rx="1.5"
                    fill={seg.c}
                    opacity={b.isForecast ? 0.55 : 1}
                  />
                  {b.isForecast && (
                    <rect x={xOf(i)} y={segY} width={barW} height={h} rx="1.5" fill="url(#sport-tss-hatch)" />
                  )}
                </g>
              )
            })}
          </g>
        )
      })}
      {/* Taper budget — stepped line across day slots, drawn OVER the bars:
          a bar poking above the line is a day over budget, which is the
          whole point of the overlay. */}
      {taperPts.length > 0 && (() => {
        let d = ''
        taperPts.forEach((p, k) => {
          const x0 = pad.l + p.i * slotW
          const x1 = pad.l + (p.i + 1) * slotW
          const yv = yOf(p.tss).toFixed(1)
          d += (k === 0 ? `M ${x0.toFixed(1)} ${yv}` : ` V ${yv}`) + ` H ${x1.toFixed(1)}`
        })
        return (
          <path d={d} fill="none" stroke={TAPER_COLOR} strokeWidth="1.8" strokeLinejoin="round" opacity="0.9" />
        )
      })()}
      {/* Race-day flag — anchors the right edge of the taper window. */}
      {raceIdx >= 0 && (() => {
        const rx = pad.l + (raceIdx + 0.5) * slotW
        return (
          <>
            <line x1={rx} y1={pad.t} x2={rx} y2={H - pad.b} stroke={TAPER_COLOR} strokeWidth="1" opacity="0.6" />
            <text
              x={rx - 4}
              y={pad.t + 8}
              fontSize="9"
              fontWeight="600"
              letterSpacing="0.6"
              fill={TAPER_COLOR}
              textAnchor="end"
            >
              RACE
            </text>
          </>
        )
      })()}
      {/* Today rule + TODAY badge — same treatment as LoadLineChart/TsbZoneChart
          so the actual/forecast handoff reads identically across all three. */}
      {todayIdx != null && todayIdx >= 0 && todayIdx < N - 1 && (() => {
        const todayBarX = pad.l + ((todayIdx + 1) / N) * innerW
        return (
          <>
            <line
              x1={todayBarX}
              y1={pad.t}
              x2={todayBarX}
              y2={H - pad.b}
              stroke="var(--color-ink-dim)"
              strokeWidth="1"
              strokeDasharray="3 3"
              opacity="0.55"
            />
            <text
              x={todayBarX + 4}
              y={pad.t + 8}
              fontSize="9"
              fontWeight="600"
              letterSpacing="0.6"
              fill="var(--color-ink-dim)"
            >
              TODAY
            </text>
          </>
        )
      })()}
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
        dateLabel={fmtScrubDate(scrubBar?.date) + (weekly ? ' (wk)' : '')}
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
