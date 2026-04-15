import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import Gauge from '../components/Gauge'
import AiRecommendation from '../components/AiRecommendation'
import OnboardingPrompt from '../components/OnboardingPrompt'
import { apiFetch } from '../api/client'
import { num, fmtDateShort, sportLabel } from '../lib/formatters'
import { CATEGORY_COLORS, SPORT_ICONS } from '../lib/constants'
import type {
  AuthMeResponse,
  WellnessResponse,
  ScheduledWorkoutsResponse,
  ActivitiesWeekResponse,
  ActivityItem,
} from '../api/types'

export default function Today() {
  const { t } = useTranslation()
  const [report, setReport] = useState<WellnessResponse | null>(null)
  const [workouts, setWorkouts] = useState<ScheduledWorkoutsResponse | null>(null)
  const [activities, setActivities] = useState<ActivitiesWeekResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [needsOnboarding, setNeedsOnboarding] = useState(false)

  useEffect(() => {
    const controller = new AbortController()
    const opts = { signal: controller.signal }

    // Check onboarding state first — users without a linked Intervals.icu
    // athlete get the OnboardingPrompt instead of a broken empty dashboard.
    // We don't parallelize with the data fetches because if the user is new,
    // the data endpoints would just 404/return empty anyway.
    apiFetch<AuthMeResponse>('/api/auth/me', opts)
      .then(me => {
        if (!me.intervals?.athlete_id) {
          if (!controller.signal.aborted) {
            setNeedsOnboarding(true)
            setLoading(false)
          }
          return null
        }
        return Promise.all([
          apiFetch<WellnessResponse>('/api/report', opts),
          apiFetch<ScheduledWorkoutsResponse>('/api/scheduled-workouts?week_offset=0', opts),
          apiFetch<ActivitiesWeekResponse>('/api/activities-week?week_offset=0', opts),
        ])
      })
      .then(result => {
        if (!result || controller.signal.aborted) return
        const [r, w, a] = result
        setReport(r)
        setWorkouts(w)
        setActivities(a)
        setLoading(false)
      })
      .catch(err => {
        if (!controller.signal.aborted) {
          setError(err.message)
          setLoading(false)
        }
      })
    return () => controller.abort()
  }, [])

  if (needsOnboarding) return <OnboardingPrompt />
  if (loading) return <Layout maxWidth="480px"><LoadingSpinner /></Layout>
  if (error) return <Layout maxWidth="480px"><ErrorMessage message={t('today.load_error')} /></Layout>
  if (!report?.has_data) return <Layout maxWidth="480px"><ErrorMessage message={t('today.no_data')} /></Layout>

  const rec = report.recovery
  const cat = rec?.category || 'moderate'
  const color = CATEGORY_COLORS[cat] || CATEGORY_COLORS.moderate
  const load = report.training_load
  const hrvData = report.hrv
  const primary = hrvData?.primary_algorithm || 'flatt_esco'
  const hrvBlock = hrvData?.[primary as keyof typeof hrvData]
  const hrvDelta = typeof hrvBlock === 'object' && hrvBlock !== null && 'delta_pct' in hrvBlock ? hrvBlock.delta_pct : null

  // Today's workouts from weekly data
  const todayWorkouts = workouts?.days.find(d => d.date === workouts.today)?.workouts || []

  // Last completed activity (most recent from this week's data)
  const lastActivity = (() => {
    if (!activities) return null
    const allActivities: (ActivityItem & { date: string })[] = []
    for (const day of activities.days) {
      for (const a of day.activities) {
        allActivities.push({ ...a, date: day.date })
      }
    }
    return allActivities.length > 0 ? allActivities[allActivities.length - 1] : null
  })()

  return (
    <Layout maxWidth="480px">
      {/* Recovery card */}
      <div className="bg-surface rounded-2xl p-5 mb-3">
        <div className="flex items-center gap-4">
          <div className="relative w-[90px] h-[90px] shrink-0">
            <Gauge score={rec?.score || 0} color={color} size={90} lineWidth={8} />
            <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 text-center">
              <div className="text-2xl font-extrabold leading-none" style={{ color }}>
                {rec?.score != null ? Math.round(rec.score) : '--'}
              </div>
            </div>
          </div>
          <div className="flex-1">
            <div className="text-xs font-bold uppercase tracking-wide mb-1" style={{ color }}>
              {rec?.title || ''}
            </div>
            <div className="text-[13px] text-text-dim">{rec?.recommendation || ''}</div>
          </div>
        </div>
      </div>

      {/* Plan today */}
      {todayWorkouts.length > 0 && (
        <div className="bg-surface rounded-2xl p-4 mb-3">
          <div className="flex items-center gap-2 mb-3">
            <span className="text-lg">📋</span>
            <span className="text-sm font-bold">{t('today.plan_today')}</span>
          </div>
          {todayWorkouts.map(w => {
            const icon = SPORT_ICONS[w.type || ''] || '🏆'
            const name = w.name?.replace(/^[A-Z]+:/, '').trim() || t('today.workout')
            return (
              <div key={w.id} className="flex items-center gap-2.5 py-1.5">
                <span className="text-base">{icon}</span>
                <span className="text-[13px] flex-1 truncate">{name}</span>
                {w.duration && <span className="text-xs text-text-dim">{w.duration}</span>}
              </div>
            )
          })}
        </div>
      )}

      {/* Last activity */}
      {lastActivity && (
        <div className="bg-surface rounded-2xl p-4 mb-3">
          <div className="flex items-center gap-2 mb-2">
            <span className="text-lg">🏃</span>
            <span className="text-sm font-bold">{t('today.last_activity')}</span>
          </div>
          <div className="flex items-center gap-2.5">
            <span className="text-base">{SPORT_ICONS[lastActivity.type || ''] || '🏆'}</span>
            <div className="flex-1">
              <div className="text-[13px] font-medium">{sportLabel(lastActivity.type)}</div>
              <div className="text-xs text-text-dim">
                {fmtDateShort(lastActivity.date)}
                {lastActivity.duration && ` \u00B7 ${lastActivity.duration}`}
                {lastActivity.icu_training_load != null && ` \u00B7 TSS ${lastActivity.icu_training_load}`}
              </div>
            </div>
            <Link to={`/activity/${lastActivity.id}`} className="text-xs text-accent no-underline">
              {t('today.details')} &rarr;
            </Link>
          </div>
        </div>
      )}

      {/* Quick stats */}
      <div className="grid grid-cols-3 gap-2 mb-3">
        <QuickStat label="CTL" value={load?.ctl != null ? load.ctl.toFixed(0) : '--'} />
        <QuickStat
          label="TSB"
          value={load?.tsb != null ? `${load.tsb >= 0 ? '+' : ''}${load.tsb.toFixed(0)}` : '--'}
          valueClass={load?.tsb != null ? (load.tsb > 10 ? 'text-green' : load.tsb < -25 ? 'text-red' : '') : ''}
        />
        <QuickStat
          label="HRV"
          value={hrvDelta != null ? `${hrvDelta >= 0 ? '+' : ''}${num(hrvDelta)}%` : '--'}
          valueClass={hrvDelta != null ? (hrvDelta > 0 ? 'text-green' : hrvDelta < -10 ? 'text-red' : '') : ''}
        />
      </div>

      {/* AI Recommendation — collapsed by default */}
      <AiCollapsible claude={report.ai_recommendation} />
    </Layout>
  )
}

function QuickStat({ label, value, valueClass }: { label: string; value: string; valueClass?: string }) {
  return (
    <div className="bg-surface rounded-xl p-3 text-center">
      <div className={`text-lg font-bold ${valueClass || ''}`}>{value}</div>
      <div className="text-[10px] text-text-dim mt-0.5">{label}</div>
    </div>
  )
}

function AiCollapsible({ claude }: { claude: string | null }) {
  const { t } = useTranslation()
  const [expanded, setExpanded] = useState(false)

  if (!claude) return null

  return (
    <div className="bg-surface rounded-2xl p-4 mb-3">
      <button
        onClick={() => setExpanded(!expanded)}
        className="flex items-center gap-2 w-full bg-transparent border-none cursor-pointer text-left font-sans p-0"
      >
        <span className="text-lg">🤖</span>
        <span className="text-sm font-bold flex-1">{t('today.ai_recommendation')}</span>
        <span className={`text-xs text-text-dim transition-transform ${expanded ? 'rotate-90' : ''}`}>▶</span>
      </button>
      {expanded && (
        <div className="mt-3">
          <AiRecommendation claude={claude} />
        </div>
      )}
    </div>
  )
}
