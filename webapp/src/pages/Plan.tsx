import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import WeekNav from '../components/WeekNav'
import LastSyncedLabel from '../components/LastSyncedLabel'
import { useWeekNav } from '../hooks/useWeekNav'
import { useApi } from '../hooks/useApi'
import { formatDayDate, stripWorkoutPrefix } from '../lib/formatters'
import { SPORT_ICONS } from '../lib/constants'
import type { ScheduledWorkoutsResponse, ScheduledWorkout } from '../api/types'

export default function Plan() {
  const { t, i18n } = useTranslation()
  const { offset, prev, next } = useWeekNav()
  const { data, loading, error } = useApi<ScheduledWorkoutsResponse>(`/api/scheduled-workouts?week_offset=${offset}`)

  return (
    <Layout title={t('plan.title')}>
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

      {data?.role === 'owner' && <LastSyncedLabel at={data.last_synced_at} />}

      {loading && <LoadingSpinner />}
      {error && <ErrorMessage message={t('plan.load_error')} />}

      {!loading && !error && data && (
        <div>
          {data.days.map(day => {
            const isToday = day.date === data.today
            return (
              <div key={day.date} className={`bg-surface border rounded-[14px] mb-2.5 overflow-hidden ${isToday ? 'border-accent' : 'border-border'}`}>
                <div className="flex items-center justify-between px-4 py-3">
                  <span className="text-sm font-semibold">{formatDayDate(day.date, day.weekday, i18n.language)}</span>
                  {isToday && <span className="text-[10px] font-bold bg-accent text-white px-2 py-0.5 rounded-lg tracking-wide">{t('common.today_badge')}</span>}
                </div>
                {day.workouts.length === 0 ? (
                  <div className="px-4 pb-3.5 text-[13px] text-text-dim italic">{t('plan.rest_day')}</div>
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
  const icon = SPORT_ICONS[w.type || ''] || '\u{1F3C6}'
  const name = stripWorkoutPrefix(w.name)
  const meta = [w.duration, w.distance_km != null ? `${w.distance_km.toFixed(1)} km` : null].filter(Boolean).join(' \u00B7 ')

  const linkClass = [
    'border-t border-border flex items-center gap-2.5 px-4 py-3',
    'cursor-pointer hover:bg-surface-2 transition-colors no-underline text-text',
  ].join(' ')

  return (
    <Link to={`/workout/${w.id}`} className={linkClass}>
      <span className="text-lg shrink-0 w-6 text-center">{icon}</span>
      <div className="flex-1 min-w-0">
        <div className="text-[13px] font-semibold truncate">{name}</div>
        {meta && <div className="text-xs text-text-dim mt-px">{meta}</div>}
      </div>
      <span className="text-xs text-text-dim shrink-0">&rsaquo;</span>
    </Link>
  )
}
