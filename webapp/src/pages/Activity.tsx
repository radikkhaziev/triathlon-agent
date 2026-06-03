import { useState } from 'react'
import { Link, useParams } from 'react-router-dom'
import { useTranslation } from 'react-i18next'
import Layout from '../components/Layout'
import { TopBar, Donut, ESSScale, InfoIcon, InfoPanel, PhotoStrip } from '../components/halo'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import ZoneBar from '../components/ZoneBar'
import { useApi } from '../hooks/useApi'
import { sportTone } from '../lib/constants'
import {
  fmtDateShort,
  fmtDuration,
  fmtPace,
  fmtSpeed,
  normalizePaceSecPerKm,
  sportLabel,
  stripWorkoutPrefix,
} from '../lib/formatters'
import type {
  ActivityDetailsResponse,
  ActivityDetails,
  ActivityWeatherInfo,
  RaceInfo,
} from '../api/types'

const ZONE_DONUT_COLORS = [
  'var(--color-ink-dimmer)',
  'var(--color-brand)',
  'var(--color-amber)',
  'var(--color-coral)',
  'var(--color-status-red)',
  'var(--color-brand-dark)',
  'var(--color-status-green)',
]

export default function Activity() {
  const { t, i18n } = useTranslation()
  const { id } = useParams<{ id: string }>()
  const { data, loading, error } = useApi<ActivityDetailsResponse>(
    id && /^i\d+$/.test(id) ? `/api/activity/${id}/details` : null
  )

  if (!id || !/^i\d+$/.test(id)) {
    return <Layout backTo="/calendar" backLabel={t('activities.back_to_week')} hideBottomTabs><ErrorMessage message="Invalid or missing activity ID." /></Layout>
  }

  if (loading) return <Layout backTo="/calendar" backLabel={t('activities.back_to_week')} hideBottomTabs><LoadingSpinner /></Layout>
  if (error || !data) return <Layout backTo="/calendar" backLabel={t('activities.back_to_week')} hideBottomTabs><ErrorMessage message={t('activities.load_activity_error')} /></Layout>

  const d = data.details
  const hrv = data.hrv
  const isBike = data.type === 'Ride'
  const isRun = data.type === 'Run'
  const isSwim = data.type === 'Swim'
  const hrZones = d?.hr_zone_times ?? d?.hr_zones
  const powerZones = d?.power_zone_times ?? d?.power_zones
  const paceZones = d?.pace_zone_times ?? d?.pace_zones

  // Pace derivation (issue #44):
  //
  // Primary path — compute from `moving_time / distance`. This is unit-safe
  // because both fields have documented units (sec, meters) and Intervals.icu
  // returns them reliably for runs/swims.
  //
  // Fallback — normalize `d.pace` via `normalizePaceSecPerKm`. That field's
  // unit is ambiguous across activity types (sometimes sec/km, sometimes m/s
  // — same value as `average_speed`), so the normalizer auto-detects by
  // magnitude. Used when `distance` is missing in historical or edge-case
  // data so the Pace card still renders.
  const distanceMeters = d?.distance ?? null
  const derivedRunPaceSecPerKm =
    data.moving_time && distanceMeters && distanceMeters > 0
      ? data.moving_time / (distanceMeters / 1000)
      : null
  const runPaceSecPerKm = derivedRunPaceSecPerKm ?? normalizePaceSecPerKm(d?.pace)
  const swimPaceSecPerKm =
    data.moving_time && distanceMeters && distanceMeters > 0
      ? data.moving_time / (distanceMeters / 1000)
      : normalizePaceSecPerKm(d?.pace)
  const swimPaceSecPer100m = swimPaceSecPerKm ? swimPaceSecPerKm / 10 : null
  const gapSecPerKm = normalizePaceSecPerKm(d?.gap)

  return (
    <Layout backTo="/calendar" backLabel={t('activities.back_to_week')} hideBottomTabs>
      <div className="-mx-4 -mt-4 -mb-8 min-h-screen bg-halo-bg px-4 md:px-9 font-sans text-halo-ink">
        <TopBar title={t('activities.title')} right={fmtDateShort(data.date, i18n.language)} />

        <div className="flex flex-col gap-3.5 pb-4">
          {/* Plan breadcrumb pill — links to the planned workout this activity
              executed. Design `BActivityWorkout` (direction-b-halo.jsx:2081):
              `[PLAN | <workout name> ›]`. Self-anchored top-left, with the
              workout name visible so the destination is explicit (replaces
              the previous «View planned workout →» link, which hid the name
              behind generic copy). Hidden when no pairing or when paired
              workout was deleted (`paired_workout` resolves to null). */}
          {data.paired_workout && (
            <Link
              to={`/workout/${data.paired_workout.id}`}
              aria-label={t('activities.open_planned_workout')}
              className="inline-flex items-center gap-2.5 self-start rounded-pill border border-halo-border bg-halo-surface px-3 py-1.5 text-[13px] text-halo-ink no-underline shadow-card hover:bg-halo-surface-2"
            >
              <span className="text-[9px] font-bold uppercase tracking-[0.7px] text-halo-ink-dimmer">
                {t('activities.plan_breadcrumb')}
              </span>
              <span className="h-3 w-px bg-halo-border" />
              <span className="font-semibold tracking-[-0.1px]">
                {stripWorkoutPrefix(data.paired_workout.name)}
              </span>
              <span aria-hidden="true" className="ml-0.5 leading-none text-halo-ink-dim">›</span>
            </Link>
          )}

          {data.is_race && data.race ? (
            <RaceHero race={data.race} det={d} runPace={runPaceSecPerKm} />
          ) : (
            <ActivityHero data={data} det={d} runPace={runPaceSecPerKm} swimPace={swimPaceSecPer100m} />
          )}

          {/* Plan vs Actual mini-table — only when paired workout has a planned
              duration or TSS we can compare against. Per-step diff lives in
              the design but the backend doesn't expose aligned per-step actuals
              yet (only planned steps on /workout + raw intervals on activity);
              the row-level comparison is the honest superset of what we have. */}
          {data.paired_workout && (
            <PlanVsActualMini
              actual={data}
              planned={data.paired_workout}
              t={t}
            />
          )}

          {!d ? (
            <ErrorMessage message={t('activities.no_details')} />
          ) : (
            <>
              {/* HR-zone donut (prototype BActivityRace) */}
              {hrZones && hrZones.some(v => v > 0) && <HrZoneDonut zones={hrZones} t={t} />}

              {/* Key metrics — prototype 2-col + featured ESS row */}
              <KeyMetrics d={d} avgHr={data.average_hr} t={t} />

              {/* Sport-specific detail (current-only superset — restyled) */}
              <div className="grid grid-cols-2 gap-2">
                {isBike && (d.avg_power || d.normalized_power) && (
                  <Card label="Power" value={`${d.normalized_power || d.avg_power}W`}
                    sub={[d.avg_power && d.normalized_power ? `Avg ${d.avg_power}W` : null, d.intensity_factor ? `IF ${d.intensity_factor.toFixed(2)}` : null, d.variability_index ? `VI ${d.variability_index.toFixed(2)}` : null, d.power_hr ? `P:HR ${d.power_hr.toFixed(2)}` : null].filter(Boolean).join(' · ')} />
                )}
                {isBike && d.avg_speed && (
                  <Card label="Speed" value={`${fmtSpeed(d.avg_speed)} km/h`}
                    sub={d.max_speed ? `Max ${fmtSpeed(d.max_speed)}` : undefined} />
                )}
                {isRun && runPaceSecPerKm && (
                  <Card label="Pace" value={`${fmtPace(runPaceSecPerKm)}/km`}
                    sub={gapSecPerKm ? `GAP ${fmtPace(gapSecPerKm)}/km` : undefined} />
                )}
                {isSwim && swimPaceSecPer100m && (
                  <Card label="Pace" value={`${fmtPace(swimPaceSecPer100m)}/100m`} />
                )}
                {data.rpe != null && (
                  <Card label={t('rpe.label')} value={`${data.rpe}/10`} sub={t(`rpe.scale.${data.rpe}`)} />
                )}
                {/* Compliance moved into the Plan vs Actual mini-table's
                    header pill (when paired_workout exists). For unpaired
                    activities (compliance == null per backend contract)
                    there's no Card to render either way — the previous Card
                    only ever rendered when compliance was set, which only
                    happens for paired runs. */}
                {d.avg_cadence && (
                  <Card label="Cadence"
                    value={isRun ? `${Math.round(d.avg_cadence * 2)} spm` : `${Math.round(d.avg_cadence)} rpm`}
                    sub={isRun && d.avg_stride ? `Stride ${d.avg_stride.toFixed(2)}m` : undefined} />
                )}
                {(d.distance || d.elevation_gain || d.calories) && (() => {
                  const ps = [d.distance ? `${(d.distance / 1000).toFixed(1)} km` : null, d.elevation_gain ? `⬆ ${Math.round(d.elevation_gain)}m` : null, d.calories ? `${d.calories} kcal` : null, d.trimp ? `TRIMP ${Math.round(d.trimp)}` : null].filter(Boolean) as string[]
                  return <Card label="Distance & More" value={ps[0]} sub={ps.slice(1).join(' · ')} />
                })()}
              </div>

              {/* Power / pace zone distribution — current-only superset (no
                  prototype slot; HR is the donut above). Restyled, kept. */}
              {((powerZones && powerZones.some(v => v > 0)) || (paceZones && paceZones.some(v => v > 0))) && (
                <div className="rounded-card border border-halo-border bg-halo-surface px-3.5 py-3 shadow-card">
                  {powerZones && powerZones.some(v => v > 0) && <ZoneBar zones={powerZones} label="Power Zones" size="detail" />}
                  {paceZones && paceZones.some(v => v > 0) && <ZoneBar zones={paceZones} label="Pace Zones" size="detail" />}
                </div>
              )}

              {/* Outdoor weather — present when ACTIVITY_UPLOADED webhook had has_weather=True */}
              {data.weather && <WeatherCard weather={data.weather} t={t} />}

              {/* Intervals */}
              {d.intervals && d.intervals.length > 0 && (
                <div className="rounded-card border border-halo-border bg-halo-surface px-3.5 py-3 shadow-card">
                  <div className="mb-2 border-b border-halo-border pb-1 text-sm font-semibold">Intervals</div>
                  <div className="overflow-x-auto">
                    <table className="w-full border-collapse text-xs">
                      <thead>
                        <tr>
                          <th className="border-b border-halo-border px-2 py-1.5 text-left text-[11px] font-semibold uppercase text-halo-ink-dim">#</th>
                          <th className="border-b border-halo-border px-2 py-1.5 text-left text-[11px] font-semibold uppercase text-halo-ink-dim">Duration</th>
                          {isBike && <th className="border-b border-halo-border px-2 py-1.5 text-left text-[11px] font-semibold uppercase text-halo-ink-dim">Power</th>}
                          {isRun && <th className="border-b border-halo-border px-2 py-1.5 text-left text-[11px] font-semibold uppercase text-halo-ink-dim">Pace</th>}
                          <th className="border-b border-halo-border px-2 py-1.5 text-left text-[11px] font-semibold uppercase text-halo-ink-dim">HR</th>
                          {!isSwim && <th className="border-b border-halo-border px-2 py-1.5 text-left text-[11px] font-semibold uppercase text-halo-ink-dim">Cadence</th>}
                        </tr>
                      </thead>
                      <tbody>
                        {d.intervals.map((iv, i) => (
                          <tr key={i}>
                            <td className="border-b border-halo-border px-2 py-1.5 last:border-b-0">{i + 1}</td>
                            <td className="border-b border-halo-border px-2 py-1.5 last:border-b-0">{fmtDuration(iv.moving_time || iv.elapsed_time)}</td>
                            {isBike && <td className="border-b border-halo-border px-2 py-1.5 last:border-b-0">{iv.average_watts || iv.weighted_average_watts || '-'}W</td>}
                            {isRun && <td className="border-b border-halo-border px-2 py-1.5 last:border-b-0">{(() => { const p = iv.gap || (iv.average_speed && iv.average_speed > 0 ? 1000 / iv.average_speed : null); return p ? fmtPace(p) : '-' })()}</td>}
                            <td className="border-b border-halo-border px-2 py-1.5 last:border-b-0">{iv.average_heartrate ? Math.round(iv.average_heartrate) : '-'}</td>
                            {!isSwim && <td className="border-b border-halo-border px-2 py-1.5 last:border-b-0">{iv.average_cadence ? Math.round(iv.average_cadence) : '-'}</td>}
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}

              {/* DFA Alpha 1 */}
              {hrv && <DfaCard hrv={hrv} t={t} />}

              {data.is_race && data.race?.notes && (
                <div className="rounded-card bg-halo-brand-light p-4">
                  <div className="text-[11px] font-bold uppercase tracking-[0.5px] text-halo-brand-dark">
                    {t('activities.your_notes')}
                  </div>
                  <div className="mt-1.5 text-sm italic leading-relaxed text-halo-ink">{data.race.notes}</div>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </Layout>
  )
}

function fmtHMS(sec: number | null): string | null {
  if (!sec) return null
  const h = Math.floor(sec / 3600)
  const m = Math.floor((sec % 3600) / 60)
  const s = sec % 60
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}` : `${m}:${String(s).padStart(2, '0')}`
}

function fmtPaceKm(sec: number | null): string | null {
  if (!sec || sec <= 0) return null
  const m = Math.floor(sec / 60)
  const s = Math.round(sec % 60)
  return `${m}:${String(s).padStart(2, '0')}/km`
}

type TFn = (k: string, o?: Record<string, unknown>) => string

// Ink race hero (prototype BActivityRace) — two big numerals Finish/Place,
// race pill + "surface · dist · weather" line, photo-strip with real
// pace/cadence/RPE. Race-day fitness snapshot preserved (RaceSection gating).
function RaceHero({
  race,
  det,
  runPace,
}: {
  race: RaceInfo
  det: ActivityDetails | null
  runPace: number | null
}) {
  const { t } = useTranslation()
  const finish = fmtHMS(race.finish_time_sec)
  const goal = fmtHMS(race.goal_time_sec)
  const delta =
    race.finish_time_sec != null && race.goal_time_sec != null
      ? race.finish_time_sec - race.goal_time_sec
      : null
  const pace = fmtPaceKm(race.avg_pace_sec_km ?? runPace)
  const cadence = det?.avg_cadence != null ? `cadence ${Math.round(det.avg_cadence)}` : null
  const rpe = race.rpe != null ? `RPE ${race.rpe}/10` : null
  const strip = [pace ? `pace ${pace}` : null, cadence, rpe].filter(Boolean) as string[]
  const sub = [race.surface, race.distance_km != null ? `${race.distance_km} km` : null, race.weather]
    .filter(Boolean)
    .join(' · ')
  const snapshot =
    race.race_day_ctl != null || race.race_day_tsb != null || race.race_day_recovery_score != null
      ? [
          race.race_day_ctl != null ? `CTL ${race.race_day_ctl.toFixed(0)}` : null,
          race.race_day_tsb != null ? `TSB ${race.race_day_tsb > 0 ? '+' : ''}${race.race_day_tsb.toFixed(0)}` : null,
          race.race_day_recovery_score != null ? `Recovery ${race.race_day_recovery_score.toFixed(0)}` : null,
          race.race_day_hrv_status ? `HRV ${race.race_day_hrv_status}` : null,
        ]
          .filter(Boolean)
          .join(' · ')
      : null

  return (
    <div className="overflow-hidden rounded-card bg-halo-ink text-white">
      <div className="p-5 pb-3.5">
        <div className="flex items-center gap-2">
          <span className="rounded-pill bg-halo-coral px-2.5 py-[3px] text-[10px] font-bold uppercase tracking-[0.6px]">
            {t('activities.race')}
          </span>
          {sub && <span className="text-xs text-white/60">{sub}</span>}
        </div>
        <div className="mt-2 text-[22px] font-semibold tracking-[-0.5px]">{race.name}</div>
        <div className="mt-[18px] flex gap-6">
          {finish && (
            <div>
              <div className="text-[10px] uppercase tracking-[0.6px] text-white/60">{t('activities.finish')}</div>
              <div className="mt-1.5 text-[44px] font-semibold leading-none tracking-[-2px]">{finish}</div>
              {goal && delta != null && (
                <div className="mt-1 text-xs font-semibold text-halo-amber">
                  {delta > 0 ? '+' : ''}{delta}s {t('activities.vs')} {goal}
                </div>
              )}
            </div>
          )}
          {race.placement ? (
            <div>
              <div className="text-[10px] uppercase tracking-[0.6px] text-white/60">{t('activities.place')}</div>
              <div className="mt-1.5 text-[44px] font-semibold leading-none tracking-[-2px]">{race.placement}</div>
              <div className="mt-1 text-xs text-white/70">
                {race.placement_total ? `${t('activities.of')} ${race.placement_total}` : ''}
                {race.placement_ag ? ` · AG ${race.placement_ag}` : ''}
              </div>
            </div>
          ) : null}
        </div>
        {snapshot && <div className="mt-3.5 border-t border-white/15 pt-3 text-[12px] text-white/70">{snapshot}</div>}
      </div>
      {strip.length > 0 && <PhotoStrip items={strip} />}
    </div>
  )
}

function HrZoneDonut({ zones, t }: { zones: number[]; t: TFn }) {
  const total = zones.reduce((a, b) => a + b, 0) || 1
  const [open, setOpen] = useState(false)
  return (
    <div className="rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card">
      <div className="flex items-center text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
        <span>{t('activities.hr_zones_label')}</span>
        <InfoIcon open={open} onClick={() => setOpen(v => !v)} />
      </div>
      {open && <InfoPanel>{t('activities.tip.hr_zones')}</InfoPanel>}
      <div className="mt-3 flex items-center gap-4">
        <Donut values={zones} colors={ZONE_DONUT_COLORS} size={100} r={38} strokeWidth={14} />
        <div className="flex flex-1 flex-col gap-1.5">
          {zones.map((sec, i) => (
            <div key={i} className={`flex items-center gap-2 text-xs ${sec ? '' : 'opacity-40'}`}>
              <span className="h-2.5 w-2.5 rounded-sm" style={{ background: ZONE_DONUT_COLORS[i % ZONE_DONUT_COLORS.length] }} />
              <span className="flex-1 text-halo-ink-dim">Z{i + 1}</span>
              <span className="font-semibold text-halo-ink">{Math.round((sec / total) * 100)}%</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}

function KeyMetrics({ d, avgHr, t }: { d: ActivityDetails; avgHr: number | null; t: TFn }) {
  // Single-key tip state — one panel open at a time across all metrics in
  // the card so the layout never stacks dark panels. Click the same icon to
  // close. Same pattern as MetricDetail.tsx (Halo InfoIcon/InfoPanel).
  type Tip = 'ef' | 'decoupling' | 'if' | 'avg_hr' | 'ess'
  const [openTip, setOpenTip] = useState<Tip | null>(null)
  const toggle = (k: Tip) => setOpenTip(prev => (prev === k ? null : k))

  const cells: { k: string; tip: Tip; v: string; sub: string; c: string }[] = [
    { k: 'EF', tip: 'ef', v: d.efficiency_factor != null ? d.efficiency_factor.toFixed(2) : '—', sub: 'efficiency factor', c: 'var(--color-brand)' },
    { k: 'Decoupling', tip: 'decoupling', v: d.decoupling != null ? `${d.decoupling.toFixed(1)}%` : '—', sub: 'aerobic stability', c: 'var(--color-brand)' },
    { k: 'IF', tip: 'if', v: d.intensity_factor != null ? d.intensity_factor.toFixed(2) : '—', sub: 'intensity factor', c: 'var(--color-amber)' },
    { k: 'Avg HR', tip: 'avg_hr', v: avgHr != null ? `${avgHr}` : '—', sub: d.max_hr != null ? t('activities.peak_hr_inline', { bpm: d.max_hr }) : 'bpm', c: 'var(--color-coral)' },
  ]
  const ess = d.trimp
  return (
    <div className="rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card">
      <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
        {t('activities.key_metrics')}
      </div>
      {/* 2×2 grid rendered as two row-pairs so the InfoPanel can drop in
          right under the row containing the clicked icon (not at the bottom
          of the whole grid — the previous layout made the Decoupling tip
          appear under Avg HR which read as «wrong cell»). Panel still spans
          card width via `col-span-2`. */}
      {[cells.slice(0, 2), cells.slice(2, 4)].map((row, ri) => {
        const rowOpenTip = row.find(c => c.tip === openTip)?.tip
        return (
          <div key={ri} className="mt-3.5 grid grid-cols-2 gap-x-3.5 gap-y-2.5">
            {row.map(c => (
              <div key={c.k}>
                <div className="flex items-center text-[11px] font-semibold text-halo-ink-dim">
                  <span>{c.k}</span>
                  <InfoIcon open={openTip === c.tip} onClick={() => toggle(c.tip)} />
                </div>
                <div className="mt-0.5 text-2xl font-semibold tracking-[-0.4px] text-halo-ink">{c.v}</div>
                <div className="mt-px text-[11px]" style={{ color: c.c }}>{c.sub}</div>
              </div>
            ))}
            {rowOpenTip && (
              <div className="col-span-2">
                <InfoPanel>{t(`activities.tip.${rowOpenTip}`)}</InfoPanel>
              </div>
            )}
          </div>
        )
      })}
      {ess != null && (
        <div className="mt-3.5 flex items-end gap-3.5 border-t border-halo-border pt-3.5">
          <div className="flex-1">
            <div className="flex items-center text-[11px] font-semibold text-halo-ink-dim">
              <span>{t('activities.ess')}</span>
              <InfoIcon open={openTip === 'ess'} onClick={() => toggle('ess')} />
            </div>
            <div className="mt-0.5 flex items-baseline gap-1.5">
              <span className="text-[30px] font-semibold tracking-[-0.6px] text-halo-ink">{Math.round(ess)}</span>
              <span className="text-xs font-medium text-halo-ink-dim">{t('activities.trimp_eq')}</span>
            </div>
            <div className="mt-0.5 text-[11px] leading-snug text-halo-ink-dim">{t('activities.ess_hint')}</div>
          </div>
          <ESSScale value={ess} />
        </div>
      )}
      {openTip === 'ess' && <InfoPanel>{t('activities.tip.ess')}</InfoPanel>}
    </div>
  )
}

function Card({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="rounded-card border border-halo-border bg-halo-surface px-3.5 py-3 shadow-card">
      <div className="text-[11px] uppercase tracking-wide text-halo-ink-dim">{label}</div>
      <div className="mt-0.5 text-lg font-semibold">{value}</div>
      {sub && <div className="mt-px text-[11px] text-halo-ink-dim">{sub}</div>}
    </div>
  )
}

// DFA Alpha 1 card — 3×2 grid of metrics with per-cell InfoIcon → InfoPanel
// tooltips. Single-open state across all six items so the layout never stacks
// dark panels. Panel placement: row-pair aware (same fix as KeyMetrics 2×2)
// so the tip drops in right under the clicked cell, not at the bottom.
type DfaTip = 'ra' | 'da' | 'hrvt1' | 'hrvt2' | 'dfa_mean' | 'quality'

function DfaCard({ hrv, t }: { hrv: NonNullable<ActivityDetailsResponse['hrv']>; t: TFn }) {
  const [openTip, setOpenTip] = useState<DfaTip | null>(null)
  const toggle = (k: DfaTip) => setOpenTip(prev => (prev === k ? null : k))

  const items: { label: string; tip: DfaTip; value: number | string | null; pct?: boolean }[] = [
    { label: 'Readiness (Ra)', tip: 'ra', value: hrv.ra_pct, pct: true },
    { label: 'Durability (Da)', tip: 'da', value: hrv.da_pct, pct: true },
    {
      label: 'HRVT1',
      tip: 'hrvt1',
      value: hrv.hrvt1_hr
        ? `${Math.round(hrv.hrvt1_hr)} bpm${hrv.hrvt1_power ? ` / ${Math.round(hrv.hrvt1_power)}W` : ''}${hrv.hrvt1_pace ? ` / ${hrv.hrvt1_pace}` : ''}`
        : null,
    },
    { label: 'HRVT2', tip: 'hrvt2', value: hrv.hrvt2_hr ? `${Math.round(hrv.hrvt2_hr)} bpm` : null },
    { label: 'DFA a1 Mean', tip: 'dfa_mean', value: hrv.dfa_a1_mean != null ? hrv.dfa_a1_mean.toFixed(2) : null },
    { label: 'Quality', tip: 'quality', value: hrv.hrv_quality },
  ]

  return (
    <div className="rounded-card border border-halo-border bg-halo-surface px-3.5 py-3 shadow-card">
      <div className="mb-2 border-b border-halo-border pb-1 text-sm font-semibold">DFA Alpha 1</div>
      {[items.slice(0, 2), items.slice(2, 4), items.slice(4, 6)].map((row, ri) => {
        const rowOpenTip = row.find(it => it.tip === openTip)?.tip
        return (
          <div key={ri} className={`grid grid-cols-2 gap-2 ${ri > 0 ? 'mt-2' : ''}`}>
            {row.map(it => (
              <DfaItem
                key={it.tip}
                label={it.label}
                value={it.value}
                pct={it.pct}
                tipOpen={openTip === it.tip}
                onTipToggle={() => toggle(it.tip)}
              />
            ))}
            {rowOpenTip && (
              <div className="col-span-2">
                <InfoPanel>{t(`activities.tip.dfa.${rowOpenTip}`)}</InfoPanel>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}

function DfaItem({
  label,
  value,
  pct,
  tipOpen,
  onTipToggle,
}: {
  label: string
  value: number | string | null
  pct?: boolean
  tipOpen: boolean
  onTipToggle: () => void
}) {
  let display = '—'
  let colorCls = ''

  if (pct && typeof value === 'number') {
    display = `${value > 0 ? '+' : ''}${value.toFixed(1)}%`
    colorCls = value > 5 ? 'text-halo-status-green' : value < -5 ? 'text-halo-status-red' : 'text-halo-amber'
  } else if (value != null) {
    display = String(value)
  }

  return (
    <div className="rounded-chip border border-halo-border bg-halo-surface px-3 py-2.5">
      <div className="flex items-center text-[11px] text-halo-ink-dim">
        <span>{label}</span>
        <InfoIcon open={tipOpen} onClick={onTipToggle} />
      </div>
      <div className={`mt-px text-base font-semibold ${colorCls}`}>{display}</div>
    </div>
  )
}

// 8-point compass — render `prevailing_wind_deg` (0°=N, clockwise) as a short
// locale-neutral abbreviation. Falls back to '' when degree is missing so the
// caller can drop the suffix cleanly.
function windOctant(deg: number | null | undefined): string {
  if (deg == null) return ''
  const octants = ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW']
  return octants[Math.round((((deg % 360) + 360) % 360) / 45) % 8]
}

function WeatherCard({ weather, t }: { weather: ActivityWeatherInfo; t: TFn }) {
  // Temp: "18°C feels 17" when delta ≥1°C, else just "18°C". Mirrors the
  // Telegram formatter's logic so both surfaces agree on feels-like.
  const temp = weather.avg_temp_c
  const feels = weather.avg_feels_like_c
  const tempStr =
    temp != null
      ? feels != null && Math.abs(feels - temp) >= 1
        ? `${Math.round(temp)}°C · feels ${Math.round(feels)}°`
        : `${Math.round(temp)}°C`
      : null

  // Wind: m/s → km/h, hide weak winds (<0.5 m/s = ~1.8 km/h).
  const wind = weather.avg_wind_speed_mps
  const windStr =
    wind != null && wind >= 0.5
      ? `${Math.round(wind * 3.6)} km/h ${windOctant(weather.prevailing_wind_deg)}`.trim()
      : null
  const headwindStr =
    weather.headwind_pct != null && weather.headwind_pct >= 25
      ? `headwind ${Math.round(weather.headwind_pct)}%`
      : null

  // Precipitation — only render when actually wet.
  const rainStr =
    weather.max_rain_mm != null && weather.max_rain_mm > 0 ? `${weather.max_rain_mm.toFixed(1)} mm rain` : null
  const snowStr =
    weather.max_snow_mm != null && weather.max_snow_mm > 0 ? `${weather.max_snow_mm.toFixed(1)} mm snow` : null
  const cloudStr = weather.avg_clouds != null ? `${Math.round(weather.avg_clouds)}% cloud cover` : null

  const line1 = [feels != null && temp != null && Math.abs(feels - temp) >= 1 ? `feels ${Math.round(feels)}°` : null, cloudStr]
    .filter(Boolean)
    .join(' · ')
  const line2 = [windStr, headwindStr, rainStr, snowStr].filter(Boolean).join(' · ')

  if (!tempStr && !line2) return null
  return (
    <div className="rounded-card border border-halo-border bg-halo-surface p-3.5 shadow-card">
      <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
        {t('activities.conditions')}
      </div>
      <div className="mt-2.5 flex items-center gap-3.5">
        {temp != null && <div className="text-[32px] font-semibold tracking-[-1px]">{Math.round(temp)}°</div>}
        <div className="text-xs leading-relaxed text-halo-ink-dim">
          {line1 && <>{line1}<br /></>}
          {line2}
        </div>
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Activity hero (non-race) — design `BActivityWorkout` (direction-b-halo.jsx:
// 2101-2123). Neutral surface (NOT cobalt; only the race hero uses the dark
// fill, that hierarchy is intentional). Sport pill + sport·distance·weather
// subtitle, big workout name, 3-col stats: Duration · NP/Pace · Intensity.
// ─────────────────────────────────────────────────────────────────────────────
function ActivityHero({
  data,
  det,
  runPace,
  swimPace,
}: {
  data: ActivityDetailsResponse
  det: ActivityDetails | null
  runPace: number | null
  swimPace: number | null
}) {
  const { t } = useTranslation()
  // Hero pill — stronger 12% mix (cf. 10% default used by Week-tab + Wellness
  // Today day-card pills); the hero is larger so the wash needs more weight.
  const tone = sportTone(data.type, 12)
  const isBike = data.type === 'Ride'
  const isRun = data.type === 'Run'
  const isSwim = data.type === 'Swim'

  // Subtitle bits: distance · brief weather tag (e.g. «18° clear»). Skip
  // anything we don't have; the row collapses gracefully.
  const distanceKm = det?.distance != null ? det.distance / 1000 : null
  const weatherBrief = (() => {
    const w = data.weather
    if (!w?.avg_temp_c) return null
    const clouds = w.avg_clouds ?? null
    const label = clouds == null
      ? ''
      : clouds < 40 ? ' clear' : clouds < 70 ? ' part. cloudy' : ' cloudy'
    return `${Math.round(w.avg_temp_c)}°${label}`
  })()
  const subParts = [
    distanceKm != null ? `${distanceKm.toFixed(1)} km` : null,
    weatherBrief,
  ].filter(Boolean) as string[]

  // 3-col stat block — values depend on the sport.
  //   Duration   — always present
  //   Mid stat   — Ride: NP (W) · Run: Pace · Swim: Pace · else: blank
  //   Intensity  — IF, already a percent from Intervals (~76 = endurance) — no *100
  const midStat = (() => {
    if (isBike && det?.normalized_power) return { k: 'NP', v: `${Math.round(det.normalized_power)}`, unit: ' W' }
    if (isRun && runPace) return { k: t('plan.target_pace'), v: fmtPace(runPace) ?? '—', unit: '/km' }
    if (isSwim && swimPace) return { k: t('plan.target_pace'), v: fmtPace(swimPace) ?? '—', unit: '/100m' }
    if (data.average_hr != null) return { k: t('plan.target_hr'), v: String(data.average_hr), unit: ' bpm' }
    return null
  })()
  const intensityPct = det?.intensity_factor != null ? Math.round(det.intensity_factor) : null

  return (
    <div className="rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card">
      <div className="flex items-center gap-2 flex-wrap">
        <span
          className="rounded-pill px-2.5 py-[3px] text-[10px] font-bold uppercase tracking-[0.6px]"
          style={{ background: tone.bg, color: tone.fg }}
        >
          {sportLabel(data.type)}
        </span>
        {subParts.length > 0 && (
          <span className="text-[12px] text-halo-ink-dim">{subParts.join(' · ')}</span>
        )}
      </div>
      <h1 className="mt-2 text-[22px] font-semibold tracking-[-0.5px] text-halo-ink">
        {data.paired_workout?.name
          ? stripWorkoutPrefix(data.paired_workout.name)
          : sportLabel(data.type)}
      </h1>
      <div className="mt-[18px] grid grid-cols-3 gap-3.5 border-t border-halo-border pt-3.5">
        <div>
          <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
            {t('plan.stat_duration')}
          </div>
          <div className="mt-0.5 text-[24px] font-semibold tracking-[-0.5px] text-halo-ink">
            {data.duration ?? '—'}
          </div>
        </div>
        {midStat && (
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
              {midStat.k}
            </div>
            <div className="mt-0.5 text-[24px] font-semibold tracking-[-0.5px] text-halo-ink">
              {midStat.v}
              <span className="text-[12px] font-medium text-halo-ink-dim">{midStat.unit}</span>
            </div>
          </div>
        )}
        {intensityPct != null && (
          <div>
            <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
              {t('plan.stat_intensity')}
            </div>
            <div className="mt-0.5 text-[24px] font-semibold tracking-[-0.5px] text-halo-ink">
              {intensityPct}
              <span className="text-[12px] font-medium text-halo-ink-dim"> %</span>
            </div>
          </div>
        )}
      </div>
    </div>
  )
}

// ─────────────────────────────────────────────────────────────────────────────
// Plan vs Actual mini-table — design `BActivityWorkout` (lines 2125-2153).
// Compliance pill top-right + per-row Plan / Actual / Δ. We render only
// Duration and Load (the two fields we have planned values for); HR-zone
// target and IF target aren't surfaced by the backend yet, so those rows
// are skipped instead of fabricated. Tone bands match the design: ±3% green,
// ±10% amber, otherwise coral.
// ─────────────────────────────────────────────────────────────────────────────
function PlanVsActualMini({
  actual,
  planned,
  t,
}: {
  actual: ActivityDetailsResponse
  planned: { duration_secs: number | null; icu_training_load: number | null }
  t: (k: string) => string
}) {
  const rows: { label: string; plan: string; actual: string; delta: string; tone: string; tip?: string }[] = []
  const toneOf = (pct: number): string => {
    const a = Math.abs(pct)
    if (a <= 3) return 'var(--color-status-green)'
    if (a <= 10) return 'var(--color-amber)'
    return 'var(--color-coral)'
  }
  const fmtMS = (sec: number): string => {
    const m = Math.floor(Math.abs(sec) / 60)
    const s = Math.abs(sec) % 60
    return `${m}:${String(s).padStart(2, '0')}`
  }
  const fmtHM = (sec: number): string => {
    const h = Math.floor(sec / 3600)
    const m = Math.round((sec % 3600) / 60)
    return h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m}m`
  }
  if (planned.duration_secs && actual.moving_time) {
    const delta = actual.moving_time - planned.duration_secs
    const pct = (delta / planned.duration_secs) * 100
    rows.push({
      label: t('plan.stat_duration'),
      plan: fmtHM(planned.duration_secs),
      actual: actual.duration ?? '—',
      delta: `${delta >= 0 ? '+' : '−'}${fmtMS(delta)}`,
      tone: toneOf(pct),
    })
  }
  if (planned.icu_training_load != null && actual.icu_training_load != null) {
    const delta = actual.icu_training_load - planned.icu_training_load
    const pct = planned.icu_training_load ? (delta / planned.icu_training_load) * 100 : 0
    rows.push({
      label: t('activities.load_row'),
      plan: `${Math.round(planned.icu_training_load)} TSS`,
      actual: `${Math.round(actual.icu_training_load)} TSS`,
      delta: `${delta >= 0 ? '+' : ''}${Math.round(delta)}`,
      tone: toneOf(pct),
      tip: 'tss',
    })
  }
  if (rows.length === 0) return null

  const compliance = actual.compliance
  const compTone =
    compliance == null
      ? 'var(--color-ink-dim)'
      : compliance >= 95
        ? 'var(--color-status-green)'
        : compliance >= 80
          ? 'var(--color-amber)'
          : 'var(--color-coral)'

  return <PlanVsActualMiniBody rows={rows} compliance={compliance} compTone={compTone} t={t} />
}

function PlanVsActualMiniBody({
  rows,
  compliance,
  compTone,
  t,
}: {
  rows: { label: string; plan: string; actual: string; delta: string; tone: string; tip?: string }[]
  compliance: number | null | undefined
  compTone: string
  t: (k: string) => string
}) {
  // Single-key tip — one panel open at a time across the card title and each
  // per-row tooltip; click the same icon to close. Card-level key is 'card';
  // per-row keys come from `row.tip` (e.g. 'tss' for the Нагрузка row).
  const [openTip, setOpenTip] = useState<string | null>(null)
  const toggle = (k: string) => setOpenTip(prev => (prev === k ? null : k))
  return (
    <div className="rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card">
      <div className="flex items-center justify-between gap-3">
        <div className="flex items-center text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
          <span>{t('activities.plan_vs_actual')}</span>
          <InfoIcon open={openTip === 'card'} onClick={() => toggle('card')} />
        </div>
        {compliance != null && (
          <span
            className="rounded-pill px-2.5 py-[3px] text-[11px] font-bold tracking-[0.3px]"
            style={{ background: `color-mix(in srgb, ${compTone} 14%, transparent)`, color: compTone }}
          >
            {Math.round(compliance)}% {t('activities.compliance_short')}
          </span>
        )}
      </div>
      {openTip === 'card' && <InfoPanel>{t('activities.tip.plan_vs_actual')}</InfoPanel>}
      <div className="mt-3 flex items-center border-b border-halo-border pb-1.5">
        <div className="flex-1" />
        <div className="min-w-[78px] text-right text-[9px] font-bold uppercase tracking-[0.6px] text-halo-ink-dimmer">
          {t('activities.col_plan')}
        </div>
        <div className="min-w-[78px] text-right text-[9px] font-bold uppercase tracking-[0.6px] text-halo-ink-dimmer">
          {t('activities.col_actual')}
        </div>
        <div className="min-w-[54px] text-right text-[9px] font-bold uppercase tracking-[0.6px] text-halo-ink-dimmer">
          Δ
        </div>
      </div>
      {rows.map((r, i) => (
        <div
          key={r.label}
          className={i < rows.length - 1 ? 'border-b border-halo-border' : undefined}
        >
          <div className="flex items-center py-2.5">
            <div className="flex flex-1 items-center text-[13px] font-medium text-halo-ink-dim">
              <span>{r.label}</span>
              {r.tip && <InfoIcon open={openTip === r.tip} onClick={() => toggle(r.tip!)} />}
            </div>
            <div className="min-w-[78px] text-right text-[14px] font-medium text-halo-ink-dim tabular-nums">
              {r.plan}
            </div>
            <div className="min-w-[78px] text-right text-[14px] font-semibold tabular-nums tracking-[-0.1px]">
              {r.actual}
            </div>
            <div
              className="min-w-[54px] text-right text-[12px] font-bold tabular-nums"
              style={{ color: r.tone }}
            >
              {r.delta}
            </div>
          </div>
          {r.tip && openTip === r.tip && (
            <div className="pb-2.5">
              <InfoPanel>{t(`activities.tip.${r.tip}`)}</InfoPanel>
            </div>
          )}
        </div>
      ))}
    </div>
  )
}
