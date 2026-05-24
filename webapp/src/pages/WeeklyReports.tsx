import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import { apiFetch } from '../api/client'
import type { WeeklyReportListItem, WeeklyReportListResponse } from '../api/types'
import ErrorMessage from '../components/ErrorMessage'
import Layout from '../components/Layout'
import { TopBar } from '../components/halo'
import LoadingSpinner from '../components/LoadingSpinner'

const PAGE_SIZE = 20

/**
 * Format an ISO Monday (`YYYY-MM-DD`) as a human-readable Mon-Sun range.
 *
 * The list represents Mon-Sun summaries, but the URL only carries the
 * Monday — we synthesise the Sunday locally rather than over-the-wire to
 * keep the API minimal. Locale comes from i18n so the rendered month name
 * follows the user's preferred language.
 */
function formatWeekRange(isoMonday: string, locale: string): string {
  // Parse + format in UTC. Without ``Z`` + ``timeZone: 'UTC'``, parsing
  // happens in local TZ and Intl renders in local TZ — a UTC-positive
  // user (Belgrade UTC+02) would see Monday rendered as the preceding
  // Sunday because the parsed Date is shifted into yesterday at format
  // time. Same TZ-shift bug as ``shiftIsoDate`` in WeeklyReport.tsx.
  const monday = new Date(`${isoMonday}T00:00:00Z`)
  const sunday = new Date(monday)
  sunday.setUTCDate(monday.getUTCDate() + 6)
  const fmt = new Intl.DateTimeFormat(locale, {
    day: 'numeric',
    month: 'short',
    timeZone: 'UTC',
  })
  return `${fmt.format(monday)} — ${fmt.format(sunday)}`
}

export default function WeeklyReports() {
  const { t, i18n } = useTranslation()
  const [items, setItems] = useState<WeeklyReportListItem[]>([])
  const [nextBefore, setNextBefore] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadingMore, setLoadingMore] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const fetchPage = async (before: string | null) => {
    const params = new URLSearchParams({ limit: String(PAGE_SIZE) })
    if (before) params.set('before', before)
    return apiFetch<WeeklyReportListResponse>(`/api/weekly-reports?${params}`)
  }

  // Initial load.
  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    fetchPage(null)
      .then(resp => {
        if (cancelled) return
        setItems(resp.items)
        setNextBefore(resp.next_before)
      })
      .catch(err => {
        if (cancelled) return
        setError(err instanceof Error ? err.message : t('weekly.error_load'))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const loadMore = async () => {
    if (!nextBefore || loadingMore) return
    setLoadingMore(true)
    setError(null)
    try {
      const resp = await fetchPage(nextBefore)
      // Append rather than replace — we're paginating older history into the
      // tail of the list. ``next_before === null`` means the API ran out of
      // older rows; the «Load more» button hides itself in that branch.
      setItems(prev => [...prev, ...resp.items])
      setNextBefore(resp.next_before)
    } catch (err) {
      setError(err instanceof Error ? err.message : t('weekly.error_load'))
    } finally {
      setLoadingMore(false)
    }
  }

  // Current-week Monday (UTC, same basis as formatWeekRange) → "This week" pill.
  const now = new Date()
  const dow = (now.getUTCDay() + 6) % 7
  const thisMonday = new Date(Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() - dow))
    .toISOString()
    .slice(0, 10)

  return (
    <Layout maxWidth="480px">
      <div className="-mx-4 -mt-4 md:-mb-8 min-h-screen bg-halo-bg px-4 md:px-9 font-sans text-halo-ink">
      <TopBar title={t('weekly.title')} />
      <div className="pb-3 text-[13px] text-halo-ink-dim">{t('weekly.written_by')}</div>
      {loading && <LoadingSpinner />}

      {!loading && error && items.length === 0 && <ErrorMessage message={error} />}

      {!loading && !error && items.length === 0 && (
        <p className="text-center text-halo-ink-dim text-sm py-8">{t('weekly.empty')}</p>
      )}

      {items.length > 0 && (
        <div className="space-y-3">
          {items.map(item => (
            <Link
              key={item.week_start}
              to={`/weekly/${item.week_start}`}
              className="block bg-halo-surface border border-halo-border rounded-card p-4 no-underline text-halo-ink shadow-card hover:bg-halo-surface-2 transition-colors"
            >
              <div className="flex items-center justify-between">
                <span className="text-[12px] font-bold uppercase tracking-[0.6px] text-halo-ink-dim">
                  {formatWeekRange(item.week_start, i18n.language)}
                </span>
                {item.week_start === thisMonday && (
                  <span className="rounded-pill bg-halo-brand px-2 py-[3px] text-[10px] font-bold uppercase tracking-[0.6px] text-white">
                    {t('weekly.this_week')}
                  </span>
                )}
              </div>
              <p className="mt-2 text-[15px] font-semibold leading-snug line-clamp-3 text-halo-ink">
                {item.preview}
              </p>
            </Link>
          ))}
        </div>
      )}

      {nextBefore && (
        <div className="pt-4">
          <button
            type="button"
            onClick={loadMore}
            disabled={loadingMore}
            className="w-full py-3 rounded-card bg-halo-surface border border-halo-border text-sm font-medium text-halo-ink hover:bg-halo-surface-2 transition-colors disabled:opacity-60 disabled:cursor-not-allowed"
          >
            {loadingMore ? t('weekly.loading_more') : t('weekly.load_more')}
          </button>
        </div>
      )}

      {/* Show error inline below the list when «Load more» fails so the
          first page stays readable instead of swapping in a full-screen
          error state and losing the user's scroll position. */}
      {error && items.length > 0 && (
        <div className="pt-3">
          <ErrorMessage message={error} />
        </div>
      )}
      </div>
    </Layout>
  )
}
