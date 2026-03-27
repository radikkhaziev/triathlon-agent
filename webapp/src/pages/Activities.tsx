import { useState, useRef } from 'react'
import { Link } from 'react-router-dom'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import WeekNav from '../components/WeekNav'
import SyncButton from '../components/SyncButton'
import ZoneBar from '../components/ZoneBar'
import { useWeekNav } from '../hooks/useWeekNav'
import { useApi } from '../hooks/useApi'
import { apiFetch } from '../api/client'
import { formatDayDate, sportLabel, fmtPace } from '../lib/formatters'
import { SPORT_ICONS, BIKE_TYPES, RUN_TYPES } from '../lib/constants'
import type { ActivitiesWeekResponse, ActivityItem, ActivityDetailsResponse, SyncResponse } from '../api/types'

export default function Activities() {
  const { offset, prev, next } = useWeekNav()
  const { data, loading, error, reload } = useApi<ActivitiesWeekResponse>(`/api/activities-week?week_offset=${offset}`)

  const handleSynced = (_result: SyncResponse) => {
    reload()
  }

  return (
    <Layout title="Активности" backTo="/">
      {data && (
        <WeekNav
          weekStart={data.week_start}
          weekEnd={data.week_end}
          hasPrev={data.has_prev}
          hasNext={offset < 0}
          onPrev={prev}
          onNext={next}
        />
      )}

      {data?.role === 'owner' && (
        <SyncButton
          endpoint="/api/jobs/sync-activities"
          lastSyncedAt={data.last_synced_at}
          onSynced={handleSynced}
        />
      )}

      {loading && <LoadingSpinner />}
      {error && <ErrorMessage message="Не удалось загрузить активности." />}

      {!loading && !error && data && (
        <div>
          {data.days.map(day => {
            const isToday = day.date === data.today
            const isFuture = day.date > data.today
            return (
              <div key={day.date} className={`bg-surface border rounded-[14px] mb-2.5 overflow-hidden ${isToday ? 'border-accent' : 'border-border'}`}>
                <div className="flex items-center justify-between px-4 py-3">
                  <span className="text-sm font-semibold">{formatDayDate(day.date, day.weekday)}</span>
                  {isToday && <span className="text-[10px] font-bold bg-accent text-white px-2 py-0.5 rounded-lg tracking-wide">СЕГОДНЯ</span>}
                </div>
                {day.activities.length === 0 && !isFuture && (
                  <div className="px-4 pb-3.5 text-[13px] text-text-dim italic">День отдыха</div>
                )}
                {day.activities.map(a => (
                  <ActivityRow key={a.id} activity={a} />
                ))}
              </div>
            )
          })}
        </div>
      )}
    </Layout>
  )
}

function ActivityRow({ activity: a }: { activity: ActivityItem }) {
  const [expanded, setExpanded] = useState(false)
  const [detail, setDetail] = useState<ActivityDetailsResponse | null>(null)
  const [detailLoading, setDetailLoading] = useState(false)
  const cacheRef = useRef<Record<string, ActivityDetailsResponse>>({})

  const icon = SPORT_ICONS[a.type || ''] || '\u{1F3C6}'
  const label = sportLabel(a.type)
  const meta = [a.duration, a.icu_training_load != null ? `TSS ${a.icu_training_load}` : null, a.average_hr != null ? `\u2764\uFE0F ${a.average_hr}` : null].filter(Boolean).join(' \u00B7 ')

  const toggleDetail = async () => {
    if (expanded) {
      setExpanded(false)
      return
    }
    setExpanded(true)

    if (cacheRef.current[a.id]) {
      setDetail(cacheRef.current[a.id])
      return
    }

    setDetailLoading(true)
    try {
      const data = await apiFetch<ActivityDetailsResponse>(`/api/activity/${a.id}/details`)
      cacheRef.current[a.id] = data
      setDetail(data)
    } catch {
      setDetail(null)
    } finally {
      setDetailLoading(false)
    }
  }

  return (
    <div className="border-t border-border">
      <div
        className="flex items-center gap-2.5 px-4 py-3 cursor-pointer hover:bg-surface-2 transition-colors"
        onClick={toggleDetail}
      >
        <span className="text-lg shrink-0 w-6 text-center">{icon}</span>
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-semibold truncate">{label}</div>
          {meta && <div className="text-xs text-text-dim mt-px">{meta}</div>}
        </div>
        <span className={`text-xs text-text-dim transition-transform shrink-0 ${expanded ? 'rotate-90' : ''}`}>&#x25B6;</span>
      </div>

      {expanded && (
        <div className="px-4 pb-3.5 animate-fadeIn">
          {detailLoading && <div className="text-xs text-text-dim py-2">Загрузка...</div>}
          {!detailLoading && detail && <InlineDetail data={detail} />}
          {!detailLoading && !detail && <div className="text-xs text-text-dim">Нет детальных данных</div>}
        </div>
      )}
    </div>
  )
}

function DetailLine({ parts }: { parts: React.ReactNode[] }) {
  if (parts.length === 0) return null
  return (
    <div>
      {parts.map((p, i) => (
        <span key={i}>{i > 0 && ' \u00B7 '}{p}</span>
      ))}
    </div>
  )
}

function InlineDetail({ data }: { data: ActivityDetailsResponse }) {
  const d = data.details
  const type = data.type || ''
  if (!d) return <div className="text-xs text-text-dim">Нет детальных данных</div>

  const lines: React.ReactNode[] = []

  if (BIKE_TYPES.includes(type)) {
    const parts: React.ReactNode[] = []
    if (d.normalized_power) parts.push(<strong>NP {d.normalized_power}W</strong>)
    if (d.intensity_factor) parts.push(`IF ${d.intensity_factor.toFixed(2)}`)
    if (d.efficiency_factor) parts.push(`EF ${d.efficiency_factor.toFixed(2)}`)
    if (d.decoupling != null) parts.push(`Decouple ${d.decoupling.toFixed(1)}%`)
    if (parts.length) lines.push(<DetailLine parts={parts} />)
    const parts2: React.ReactNode[] = []
    if (d.elevation_gain) parts2.push(`\u2B06\uFE0F ${Math.round(d.elevation_gain)}m`)
    if (d.avg_cadence) parts2.push(`\uD83D\uDD04 ${Math.round(d.avg_cadence)}rpm`)
    if (d.calories) parts2.push(`\uD83D\uDD25 ${d.calories}kcal`)
    if (parts2.length) lines.push(<DetailLine parts={parts2} />)
  } else if (RUN_TYPES.includes(type)) {
    const parts: React.ReactNode[] = []
    const pace = fmtPace(d.pace)
    if (pace) parts.push(<strong>Pace {pace}/km</strong>)
    const gap = fmtPace(d.gap)
    if (gap) parts.push(`GAP ${gap}/km`)
    if (d.efficiency_factor) parts.push(`EF ${d.efficiency_factor.toFixed(2)}`)
    if (d.decoupling != null) parts.push(`Decouple ${d.decoupling.toFixed(1)}%`)
    if (parts.length) lines.push(<DetailLine parts={parts} />)
    const parts2: React.ReactNode[] = []
    if (d.elevation_gain) parts2.push(`\u2B06\uFE0F ${Math.round(d.elevation_gain)}m`)
    if (d.avg_cadence) parts2.push(`\uD83D\uDC63 ${Math.round(d.avg_cadence * 2)}spm`)
    if (d.avg_stride) parts2.push(`Stride ${d.avg_stride.toFixed(2)}m`)
    if (parts2.length) lines.push(<DetailLine parts={parts2} />)
  } else if (type === 'Swim') {
    const parts: React.ReactNode[] = []
    if (d.pace) {
      const p100 = d.pace / 10
      const m = Math.floor(p100 / 60)
      const s = Math.round(p100 % 60)
      parts.push(<strong>Pace {m}:{String(s).padStart(2, '0')}/100m</strong>)
    }
    if (d.calories) parts.push(`\uD83D\uDD25 ${d.calories}kcal`)
    if (parts.length) lines.push(<DetailLine parts={parts} />)
  } else {
    const parts: React.ReactNode[] = []
    if (d.calories) parts.push(`\uD83D\uDD25 ${d.calories}kcal`)
    if (d.distance) parts.push(`${(d.distance / 1000).toFixed(1)}km`)
    if (parts.length) lines.push(<DetailLine parts={parts} />)
  }

  return (
    <>
      <div className="text-xs text-text-dim leading-[1.8]">
        {lines.map((line, i) => <div key={i}>{line}</div>)}
      </div>
      {d.hr_zones && <ZoneBar zones={d.hr_zones} label="HR Zones" />}
      {d.power_zones && <ZoneBar zones={d.power_zones} label="Power Zones" />}
      {d.pace_zones && <ZoneBar zones={d.pace_zones} label="Pace Zones" />}
      <Link to={`/activity/${data.activity_id}`} className="inline-block mt-2 text-xs text-accent no-underline hover:underline">
        Details &rarr;
      </Link>
    </>
  )
}
