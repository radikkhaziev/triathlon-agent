import { useParams } from 'react-router-dom'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import ZoneChart from '../components/ZoneChart'
import { useApi } from '../hooks/useApi'
import { fmtDateShort, sportLabel, fmtPace, fmtSpeed, fmtDuration } from '../lib/formatters'
import { SPORT_ICONS } from '../lib/constants'
import type { ActivityDetailsResponse } from '../api/types'

export default function Activity() {
  const { id } = useParams<{ id: string }>()
  const { data, loading, error } = useApi<ActivityDetailsResponse>(
    id && /^i\d+$/.test(id) ? `/api/activity/${id}/details` : null
  )

  if (!id || !/^i\d+$/.test(id)) {
    return <Layout backTo="/activities" backLabel="Назад к активностям" hideBottomTabs><ErrorMessage message="Invalid or missing activity ID." /></Layout>
  }

  if (loading) return <Layout backTo="/activities" backLabel="Назад к активностям" hideBottomTabs><LoadingSpinner /></Layout>
  if (error || !data) return <Layout backTo="/activities" backLabel="Назад к активностям" hideBottomTabs><ErrorMessage message="Не удалось загрузить активность." /></Layout>

  const d = data.details
  const hrv = data.hrv
  const isBike = data.type === 'Ride'
  const isRun = data.type === 'Run'
  const isSwim = data.type === 'Swim'
  const hrZones = d?.hr_zone_times ?? d?.hr_zones
  const powerZones = d?.power_zone_times ?? d?.power_zones
  const paceZones = d?.pace_zone_times ?? d?.pace_zones
  const icon = SPORT_ICONS[data.type || ''] || '\u{1F3C6}'

  const subParts = [fmtDateShort(data.date), data.duration, data.icu_training_load != null ? `TSS ${data.icu_training_load}` : null, data.average_hr != null ? `\u2764\uFE0F ${data.average_hr} bpm` : null].filter(Boolean)

  return (
    <Layout backTo="/activities" backLabel="Назад к активностям" hideBottomTabs>
      {/* Header */}
      <div className="py-4 pb-3">
        <div className="text-xl font-bold flex items-center gap-2">{icon} {sportLabel(data.type)}</div>
        <div className="text-[13px] text-text-dim mt-1">{subParts.join(' \u00B7 ')}</div>
      </div>

      {!d ? (
        <ErrorMessage message="Нет детальных данных для этой активности." />
      ) : (
        <>
          {/* Summary Cards */}
          <div className="grid grid-cols-2 gap-2 mb-4">
            {isBike && (d.avg_power || d.normalized_power) && (
              <Card label="Power" value={`${d.normalized_power || d.avg_power}W`}
                sub={[d.avg_power && d.normalized_power ? `Avg ${d.avg_power}W` : null, d.intensity_factor ? `IF ${d.intensity_factor.toFixed(2)}` : null, d.variability_index ? `VI ${d.variability_index.toFixed(2)}` : null].filter(Boolean).join(' \u00B7 ')} />
            )}
            {isBike && d.avg_speed && (
              <Card label="Speed" value={`${fmtSpeed(d.avg_speed)} km/h`}
                sub={d.max_speed ? `Max ${fmtSpeed(d.max_speed)}` : undefined} />
            )}
            {isRun && d.pace && (
              <Card label="Pace" value={`${fmtPace(d.pace)}/km`}
                sub={d.gap ? `GAP ${fmtPace(d.gap)}/km` : undefined} />
            )}
            {isSwim && d.pace && (() => {
              const p100 = d.pace / 10
              return <Card label="Pace" value={`${fmtPace(p100)}/100m`} />
            })()}
            {(data.average_hr || d.max_hr) && (
              <Card label="Heart Rate" value={`${data.average_hr || '-'} bpm`}
                sub={d.max_hr ? `Max ${d.max_hr} bpm` : undefined} />
            )}
            {(d.efficiency_factor || d.power_hr || d.decoupling != null) && (
              <Card label="Efficiency"
                value={d.efficiency_factor ? d.efficiency_factor.toFixed(2) : d.power_hr ? d.power_hr.toFixed(2) : '-'}
                sub={[d.efficiency_factor ? 'EF' : null, d.power_hr && d.efficiency_factor ? `P:HR ${d.power_hr.toFixed(2)}` : null, d.decoupling != null ? `Decouple ${d.decoupling.toFixed(1)}%` : null].filter(Boolean).join(' \u00B7 ')} />
            )}
            {d.avg_cadence && (
              <Card label="Cadence"
                value={isRun ? `${Math.round(d.avg_cadence * 2)} spm` : `${Math.round(d.avg_cadence)} rpm`}
                sub={isRun && d.avg_stride ? `Stride ${d.avg_stride.toFixed(2)}m` : undefined} />
            )}
            {(d.distance || d.elevation_gain || d.calories) && (() => {
              const ps = [d.distance ? `${(d.distance / 1000).toFixed(1)} km` : null, d.elevation_gain ? `\u2B06 ${Math.round(d.elevation_gain)}m` : null, d.calories ? `${d.calories} kcal` : null, d.trimp ? `TRIMP ${Math.round(d.trimp)}` : null].filter(Boolean) as string[]
              return <Card label="Distance & More" value={ps[0]} sub={ps.slice(1).join(' \u00B7 ')} />
            })()}
          </div>

          {/* Zone Charts */}
          {hrZones && hrZones.some(v => v > 0) && <ZoneChart zones={hrZones} label="HR Zones" />}
          {powerZones && powerZones.some(v => v > 0) && <ZoneChart zones={powerZones} label="Power Zones" />}
          {paceZones && paceZones.some(v => v > 0) && <ZoneChart zones={paceZones} label="Pace Zones" />}

          {/* Intervals */}
          {d.intervals && d.intervals.length > 0 && (
            <div className="mb-4">
              <div className="text-sm font-bold mb-2 pb-1 border-b border-border">Intervals</div>
              <div className="overflow-x-auto bg-surface border border-border rounded-xl py-1">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr>
                      <th className="text-left font-semibold text-text-dim px-2 py-1.5 border-b border-border text-[11px] uppercase">#</th>
                      <th className="text-left font-semibold text-text-dim px-2 py-1.5 border-b border-border text-[11px] uppercase">Duration</th>
                      {isBike && <th className="text-left font-semibold text-text-dim px-2 py-1.5 border-b border-border text-[11px] uppercase">Power</th>}
                      {isRun && <th className="text-left font-semibold text-text-dim px-2 py-1.5 border-b border-border text-[11px] uppercase">Pace</th>}
                      <th className="text-left font-semibold text-text-dim px-2 py-1.5 border-b border-border text-[11px] uppercase">HR</th>
                      {!isSwim && <th className="text-left font-semibold text-text-dim px-2 py-1.5 border-b border-border text-[11px] uppercase">Cadence</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {d.intervals.map((iv, i) => (
                      <tr key={i}>
                        <td className="px-2 py-1.5 border-b border-border last:border-b-0">{i + 1}</td>
                        <td className="px-2 py-1.5 border-b border-border last:border-b-0">{fmtDuration(iv.moving_time || iv.elapsed_time)}</td>
                        {isBike && <td className="px-2 py-1.5 border-b border-border last:border-b-0">{iv.average_watts || iv.weighted_average_watts || '-'}W</td>}
                        {isRun && <td className="px-2 py-1.5 border-b border-border last:border-b-0">{(() => { const p = iv.gap || (iv.average_speed && iv.average_speed > 0 ? 1000 / iv.average_speed : null); return p ? fmtPace(p) : '-' })()}</td>}
                        <td className="px-2 py-1.5 border-b border-border last:border-b-0">{iv.average_heartrate ? Math.round(iv.average_heartrate) : '-'}</td>
                        {!isSwim && <td className="px-2 py-1.5 border-b border-border last:border-b-0">{iv.average_cadence ? Math.round(iv.average_cadence) : '-'}</td>}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {/* DFA Alpha 1 */}
          {hrv && (
            <div className="mb-4">
              <div className="text-sm font-bold mb-2 pb-1 border-b border-border">DFA Alpha 1</div>
              <div className="grid grid-cols-2 gap-2">
                <DfaItem label="Readiness (Ra)" value={hrv.ra_pct} pct />
                <DfaItem label="Durability (Da)" value={hrv.da_pct} pct />
                <DfaItem label="HRVT1" value={
                  hrv.hrvt1_hr
                    ? `${Math.round(hrv.hrvt1_hr)} bpm${hrv.hrvt1_power ? ` / ${Math.round(hrv.hrvt1_power)}W` : ''}${hrv.hrvt1_pace ? ` / ${hrv.hrvt1_pace}` : ''}`
                    : null
                } />
                <DfaItem label="HRVT2" value={hrv.hrvt2_hr ? `${Math.round(hrv.hrvt2_hr)} bpm` : null} />
                <DfaItem label="DFA a1 Mean" value={hrv.dfa_a1_mean != null ? hrv.dfa_a1_mean.toFixed(2) : null} />
                <DfaItem label="Quality" value={hrv.hrv_quality} />
              </div>
            </div>
          )}
        </>
      )}
    </Layout>
  )
}

function Card({ label, value, sub }: { label: string; value: string; sub?: string }) {
  return (
    <div className="bg-surface border border-border rounded-xl px-3.5 py-3">
      <div className="text-[11px] text-text-dim uppercase tracking-wide">{label}</div>
      <div className="text-lg font-bold mt-0.5">{value}</div>
      {sub && <div className="text-[11px] text-text-dim mt-px">{sub}</div>}
    </div>
  )
}

function DfaItem({ label, value, pct }: { label: string; value: number | string | null; pct?: boolean }) {
  let display = '\u2014'
  let colorCls = ''

  if (pct && typeof value === 'number') {
    display = `${value > 0 ? '+' : ''}${value.toFixed(1)}%`
    colorCls = value > 5 ? 'text-green' : value < -5 ? 'text-red' : 'text-yellow'
  } else if (value != null) {
    display = String(value)
  }

  return (
    <div className="bg-surface border border-border rounded-[10px] px-3 py-2.5">
      <div className="text-[11px] text-text-dim">{label}</div>
      <div className={`text-base font-bold mt-px ${colorCls}`}>{display}</div>
    </div>
  )
}
