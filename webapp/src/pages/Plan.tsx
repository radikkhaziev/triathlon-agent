import { useState } from 'react'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import WeekNav from '../components/WeekNav'
import SyncButton from '../components/SyncButton'
import { useWeekNav } from '../hooks/useWeekNav'
import { useApi } from '../hooks/useApi'
import { formatDayDate, stripWorkoutPrefix } from '../lib/formatters'
import { SPORT_ICONS } from '../lib/constants'
import type { ScheduledWorkoutsResponse, SyncResponse, ScheduledWorkout } from '../api/types'

export default function Plan() {
  const { offset, prev, next } = useWeekNav()
  const { data, loading, error, reload } = useApi<ScheduledWorkoutsResponse>(`/api/scheduled-workouts?week_offset=${offset}`)

  const handleSynced = (_result: SyncResponse) => {
    reload()
  }

  return (
    <Layout title="План тренировок">
      {data && (
        <WeekNav
          weekStart={data.week_start}
          weekEnd={data.week_end}
          hasPrev={data.has_prev}
          hasNext={data.has_next}
          onPrev={prev}
          onNext={next}
        />
      )}

      {data?.role === 'owner' && (
        <SyncButton
          endpoint="/api/jobs/sync-workouts"
          lastSyncedAt={data.last_synced_at}
          onSynced={handleSynced}
        />
      )}

      {loading && <LoadingSpinner />}
      {error && <ErrorMessage message="Не удалось загрузить план." />}

      {!loading && !error && data && (
        <div>
          {data.days.map(day => {
            const isToday = day.date === data.today
            return (
              <div key={day.date} className={`bg-surface border rounded-[14px] mb-2.5 overflow-hidden ${isToday ? 'border-accent' : 'border-border'}`}>
                <div className="flex items-center justify-between px-4 py-3">
                  <span className="text-sm font-semibold">{formatDayDate(day.date, day.weekday)}</span>
                  {isToday && <span className="text-[10px] font-bold bg-accent text-white px-2 py-0.5 rounded-lg tracking-wide">СЕГОДНЯ</span>}
                </div>
                {day.workouts.length === 0 ? (
                  <div className="px-4 pb-3.5 text-[13px] text-text-dim italic">День отдыха</div>
                ) : (
                  day.workouts.map(w => <WorkoutItem key={w.id} workout={w} />)
                )}
              </div>
            )
          })}
        </div>
      )}
    </Layout>
  )
}

function WorkoutItem({ workout: w }: { workout: ScheduledWorkout }) {
  const [expanded, setExpanded] = useState(false)
  const icon = SPORT_ICONS[w.type || ''] || '\u{1F3C6}'
  const name = stripWorkoutPrefix(w.name)
  const meta = [w.duration, w.distance_km ? `${w.distance_km} km` : null].filter(Boolean).join(' \u00B7 ')
  const hasDesc = !!w.description?.trim()

  return (
    <div className="border-t border-border">
      <div
        className={`flex items-center gap-2.5 px-4 py-3 ${hasDesc ? 'cursor-pointer hover:bg-surface-2' : ''} transition-colors`}
        onClick={hasDesc ? () => setExpanded(!expanded) : undefined}
      >
        <span className="text-lg shrink-0 w-6 text-center">{icon}</span>
        <div className="flex-1 min-w-0">
          <div className="text-[13px] font-semibold truncate">{name}</div>
          {meta && <div className="text-xs text-text-dim mt-px">{meta}</div>}
        </div>
        {hasDesc && (
          <span className={`text-xs text-text-dim transition-transform shrink-0 ${expanded ? 'rotate-90' : ''}`}>
            &#x25B6;
          </span>
        )}
      </div>
      {expanded && hasDesc && (
        <div className="px-4 pb-3.5 border-t border-border">
          <pre className="font-mono text-xs leading-relaxed text-text-dim whitespace-pre-wrap break-words m-0 p-3 bg-bg rounded-lg">
            {w.description}
          </pre>
        </div>
      )}
    </div>
  )
}
