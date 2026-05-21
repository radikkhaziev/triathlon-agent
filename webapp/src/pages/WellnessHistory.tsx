import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate } from 'react-router-dom'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import { useApi } from '../hooks/useApi'
import { fmtDateYmd } from '../lib/formatters'
import { classifyRecovery, type RecoveryCategory } from '../utils/recovery'
import type { RecoveryTrendSeries } from '../api/types'

/**
 * All-history calendar — month-grid recovery heatmap (prototype
 * `BHistoryCalendar`, direction-b-halo.jsx). Reached from the leading
 * "All history" pill in the Wellness date strip; tapping a day deep-links
 * back to /wellness for that date's full snapshot.
 *
 * Per-day recovery scores come from `/api/recovery-trend?days=60` — the same
 * endpoint the Recovery-trend detail screen uses, so no backend work. Cell
 * colours key off `classifyRecovery` (40/70/85 bands) — data-honest.
 */

// How far back the heatmap reaches. The recovery-trend endpoint caps at 365;
// 60 days ≈ 2 months, enough to spot multi-week patterns without a huge fetch.
const HISTORY_DAYS = 60

// Calendar-cell heatmap palette, keyed to classifyRecovery's bands. A
// saturated grid variant of the Wellness recovery-gauge wash — a
// self-contained heatmap palette (same precedent as the Sleep zone colours).
const RECOVERY_BAND: Record<RecoveryCategory, { bg: string; ink: string; dot: string }> = {
  excellent: { bg: '#dcfce7', ink: '#15803d', dot: '#15803d' },
  good: { bg: '#bbf7d0', ink: '#166534', dot: '#16a34a' },
  moderate: { bg: '#fde7b3', ink: '#92400e', dot: '#d97706' },
  low: { bg: '#fde6e6', ink: '#991b1b', dot: '#dc2626' },
}

const BAND_ORDER: RecoveryCategory[] = ['low', 'moderate', 'good', 'excellent']

type Cell = { day: number; iso: string; score: number | null; isToday: boolean; isFuture: boolean } | null

export default function WellnessHistory() {
  const { t, i18n } = useTranslation()
  const navigate = useNavigate()
  // monthOffset: 0 = current month, -1 = previous, … Forward is blocked —
  // there's no wellness data past today.
  const [monthOffset, setMonthOffset] = useState(0)
  const { data, loading, error } = useApi<RecoveryTrendSeries>(`/api/recovery-trend?days=${HISTORY_DAYS}`)

  // ISO date → recovery score. The trend series is parallel arrays; null
  // scores (cold-start / missing days) are dropped — those cells read "no data".
  const scoreByDate = new Map<string, number>()
  data?.dates.forEach((d, i) => {
    const v = data.recovery[i]
    if (v != null) scoreByDate.set(d, v)
  })

  const today = new Date()
  today.setHours(0, 0, 0, 0)
  const todayYmd = fmtDateYmd(today)
  const viewMonth = new Date(today.getFullYear(), today.getMonth() + monthOffset, 1)
  const monthLabel = viewMonth.toLocaleDateString(i18n.language, { month: 'long', year: 'numeric' })

  // Oldest month the fetch actually reaches — back-nav stops here so the user
  // can't page into all-grey months that only look empty because the data
  // window ends, not because they rested.
  const oldest = new Date(today)
  oldest.setDate(oldest.getDate() - (HISTORY_DAYS - 1))
  const minMonthOffset = (oldest.getFullYear() - today.getFullYear()) * 12 + (oldest.getMonth() - today.getMonth())

  // Monday-first calendar grid (Russian/European convention).
  const daysInMonth = new Date(viewMonth.getFullYear(), viewMonth.getMonth() + 1, 0).getDate()
  const startWeekday = (new Date(viewMonth.getFullYear(), viewMonth.getMonth(), 1).getDay() + 6) % 7
  const cells: Cell[] = []
  for (let i = 0; i < startWeekday; i++) cells.push(null)
  for (let d = 1; d <= daysInMonth; d++) {
    const dt = new Date(viewMonth.getFullYear(), viewMonth.getMonth(), d)
    const iso = fmtDateYmd(dt)
    cells.push({ day: d, iso, score: scoreByDate.get(iso) ?? null, isToday: iso === todayYmd, isFuture: iso > todayYmd })
  }
  while (cells.length % 7) cells.push(null)

  // Month summary — derived from the cells that actually have a score.
  const monthScores = cells.flatMap(c => (c && c.score != null ? [c.score] : []))
  const avg = monthScores.length ? Math.round(monthScores.reduce((a, b) => a + b, 0) / monthScores.length) : null
  const best = monthScores.length ? Math.max(...monthScores) : null
  const lowest = monthScores.length ? Math.min(...monthScores) : null

  // Locale weekday short names, Monday-first (2024-01-01 is a Monday).
  const weekdayHeaders = Array.from({ length: 7 }, (_, i) =>
    new Date(2024, 0, 1 + i).toLocaleDateString(i18n.language, { weekday: 'short' }),
  )

  const navBtn =
    'flex h-8 w-8 items-center justify-center rounded-full border border-halo-border bg-halo-surface ' +
    'text-lg leading-none text-halo-ink disabled:cursor-not-allowed disabled:bg-halo-bg ' +
    'disabled:text-halo-ink-dimmer disabled:opacity-50'

  return (
    <Layout maxWidth="480px">
      <div className="-mx-4 -mt-4 md:-mb-8 min-h-screen bg-halo-bg px-4 md:px-9 font-sans text-halo-ink">
        <header className="flex items-center px-1 pt-[18px] pb-2.5">
          <Link
            to="/wellness"
            className="inline-flex items-center gap-1.5 py-1.5 pl-1 pr-2.5 text-sm font-medium text-halo-ink-dim no-underline"
          >
            <span className="text-lg leading-none">‹</span> {t('nav.today')}
          </Link>
        </header>

        <div className="pb-3">
          <div className="text-[22px] font-semibold tracking-[-0.4px]">{t('history.title')}</div>
          <div className="mt-0.5 text-[13px] text-halo-ink-dim">{t('history.subtitle', { days: HISTORY_DAYS })}</div>
        </div>

        {loading && !data && <LoadingSpinner />}
        {error && !data && <ErrorMessage message={t('wellness.load_error')} />}

        {data && (
          <div className="flex flex-col gap-3.5 pb-6">
            {/* Month grid */}
            <div className="rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card">
              <div className="mb-3.5 flex items-center justify-between">
                <button
                  type="button"
                  onClick={() => setMonthOffset(o => Math.max(minMonthOffset, o - 1))}
                  disabled={monthOffset <= minMonthOffset}
                  aria-label="Previous month"
                  className={navBtn}
                >
                  ‹
                </button>
                <span className="text-[15px] font-semibold first-letter:uppercase">{monthLabel}</span>
                <button
                  type="button"
                  onClick={() => setMonthOffset(o => Math.min(0, o + 1))}
                  disabled={monthOffset >= 0}
                  aria-label="Next month"
                  className={navBtn}
                >
                  ›
                </button>
              </div>

              <div className="mb-1.5 grid grid-cols-7 gap-1">
                {weekdayHeaders.map((w, i) => (
                  <div
                    key={i}
                    className="text-center text-[10px] font-bold uppercase tracking-[0.4px] text-halo-ink-dimmer first-letter:uppercase"
                  >
                    {w}
                  </div>
                ))}
              </div>

              <div className="grid grid-cols-7 gap-1">
                {cells.map((c, i) => {
                  if (!c) return <div key={i} />
                  if (c.isFuture) {
                    return (
                      <div key={i} className="flex aspect-square items-center justify-center text-xs text-halo-ink-dimmer">
                        {c.day}
                      </div>
                    )
                  }
                  if (c.score == null) {
                    return (
                      <div
                        key={i}
                        className="flex aspect-square items-center justify-center rounded-lg bg-halo-bg text-xs text-halo-ink-dimmer"
                      >
                        {c.day}
                      </div>
                    )
                  }
                  const band = RECOVERY_BAND[classifyRecovery(c.score)]
                  return (
                    <button
                      key={i}
                      type="button"
                      onClick={() => navigate(`/wellness?date=${c.iso}`)}
                      aria-label={`${c.iso} — recovery ${c.score}`}
                      className="flex aspect-square flex-col items-center justify-center gap-px rounded-lg text-[13px] font-semibold transition-opacity hover:opacity-80"
                      style={{ background: band.bg, color: band.ink, border: c.isToday ? '2px solid var(--color-ink)' : 'none' }}
                    >
                      <span className="leading-none">{c.day}</span>
                      <span className="text-[9px] font-bold leading-none opacity-75">{c.score}</span>
                    </button>
                  )
                })}
              </div>
            </div>

            {/* Legend */}
            <div className="rounded-card border border-halo-border bg-halo-surface px-3.5 py-3 shadow-card">
              <div className="flex flex-wrap justify-between gap-1.5">
                {BAND_ORDER.map(cat => (
                  <div key={cat} className="flex items-center gap-1.5 text-[11px]">
                    <div
                      className="h-3 w-3 rounded"
                      style={{ background: RECOVERY_BAND[cat].bg, border: `1px solid ${RECOVERY_BAND[cat].dot}` }}
                    />
                    <span className="font-semibold text-halo-ink-dim">{t(`history.band.${cat}`)}</span>
                  </div>
                ))}
              </div>
            </div>

            {/* Month summary */}
            <div className="rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card">
              <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim first-letter:uppercase">
                {t('history.summary', { month: monthLabel })}
              </div>
              <div className="mt-2.5 grid grid-cols-3 gap-2.5">
                {([['avg', avg], ['best', best], ['lowest', lowest]] as const).map(([key, val]) => {
                  const band = val != null ? RECOVERY_BAND[classifyRecovery(val)] : null
                  return (
                    <div key={key}>
                      <div className="text-[11px] font-semibold uppercase tracking-[0.4px] text-halo-ink-dim">
                        {t(`history.${key}`)}
                      </div>
                      <div className="mt-1 flex items-baseline gap-1">
                        <span
                          className="text-2xl font-semibold tracking-[-0.5px]"
                          style={{ color: band?.ink ?? 'var(--color-ink)' }}
                        >
                          {val ?? '—'}
                        </span>
                        {val != null && <span className="text-[10px] text-halo-ink-dimmer">/100</span>}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          </div>
        )}
      </div>
    </Layout>
  )
}
