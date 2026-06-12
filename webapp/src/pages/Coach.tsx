import { useTranslation } from 'react-i18next'
import { Link, useSearchParams } from 'react-router-dom'
import ReactMarkdown from 'react-markdown'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import DemoSampleBadge from '../components/DemoSampleBadge'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../auth/useAuth'
import { fmtDateYmd } from '../lib/formatters'
import type { WellnessResponse } from '../api/types'

/**
 * Coach — full `wellness.ai_recommendation` view (prototype `BCoach`,
 * direction-b-halo.jsx). Reached from the "Coach note" teaser on Wellness.
 * One screen, one voice: the free-form Claude string in full, no second AI
 * string anywhere else (the recovery chip+rec stays the deterministic
 * "what to do today"). New `/coach` route — added by explicit request,
 * reversing the earlier G3=(b) "AI off the hero" decision.
 */
const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/

export default function Coach() {
  const { t, i18n } = useTranslation()
  const [searchParams] = useSearchParams()
  // `?date=` deep-link — the Wellness coach teaser passes whichever day you
  // were viewing. Default today; garbage or a future date falls back to today.
  const todayYmd = fmtDateYmd(new Date())
  const dateParam = searchParams.get('date')
  const date = dateParam && ISO_DATE_RE.test(dateParam) && dateParam <= todayYmd ? dateParam : todayYmd
  const isToday = date === todayYmd

  const { isDemo } = useAuth()
  const { data, loading, error } = useApi<WellnessResponse>(`/api/wellness-day?date=${date}`)
  // Demo never receives the real note (server stubs it) — render the canned
  // English sample so the page shows the product's form, not an empty state.
  const ai = isDemo ? t('demo.coach_sample') : (data?.has_data && data.ai_recommendation?.trim()) || null

  // Eyebrow + back-link reflect the day: "this morning" only reads right for
  // today; a past day shows its date and returns to /wellness on that date.
  const eyebrow = isToday
    ? t('coach.eyebrow')
    : t('coach.eyebrow_date', {
        date: new Intl.DateTimeFormat(i18n.language, { weekday: 'short', month: 'short', day: 'numeric' }).format(
          new Date(`${date}T00:00:00`),
        ),
      })
  const backTo = isToday ? '/wellness' : `/wellness?date=${date}`

  return (
    <Layout maxWidth="480px">
      <div className="-mx-4 -mt-4 md:-mb-8 min-h-screen bg-halo-bg px-4 md:px-9 font-sans text-halo-ink">
        {/* Back chevron only — no logo, no chrome (prototype). */}
        <header className="flex items-center px-1 pt-[18px] pb-2.5">
          <Link
            to={backTo}
            className="inline-flex items-center gap-1.5 py-1.5 pl-1 pr-2.5 text-sm font-medium text-halo-ink-dim no-underline"
          >
            <span className="text-lg leading-none">‹</span> {t('nav.today')}
          </Link>
        </header>

        <div className="flex flex-1 flex-col px-5 pb-8 pt-3.5">
          <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
            {eyebrow}
          </div>
          {isDemo && <DemoSampleBadge textKey="demo.sample_badge" />}
          {loading && <LoadingSpinner />}
          {error && <ErrorMessage message={t('wellness.load_error')} />}
          {!loading && !error && (
            ai ? (
              // ai_recommendation генерится Claude'ом и приходит markdown'ом
              // (заголовки, **bold**, списки, --- разделители). До этого
              // рендерился как plain text → markdown сырой виден на экране.
              // safeUrlTransform: http(s)+mailto only — паттерн из WeeklyReport.
              <div
                className={
                  'mt-3.5 text-[17px] leading-[1.55] tracking-[-0.1px] text-halo-ink ' +
                  '[&_p]:mb-3 [&_p:last-child]:mb-0 ' +
                  '[&_strong]:font-semibold ' +
                  '[&_h1]:text-[22px] [&_h1]:font-semibold [&_h1]:mt-5 [&_h1]:mb-2 ' +
                  '[&_h2]:text-[19px] [&_h2]:font-semibold [&_h2]:mt-5 [&_h2]:mb-2 ' +
                  '[&_h3]:text-[17px] [&_h3]:font-semibold [&_h3]:mt-4 [&_h3]:mb-1.5 ' +
                  '[&_ul]:list-disc [&_ul]:pl-5 [&_ul]:mb-3 ' +
                  '[&_ol]:list-decimal [&_ol]:pl-5 [&_ol]:mb-3 ' +
                  '[&_li]:mb-1 ' +
                  '[&_hr]:my-5 [&_hr]:border-0 [&_hr]:border-t [&_hr]:border-halo-border ' +
                  '[&_a]:text-halo-brand-dark [&_a]:underline ' +
                  '[&_code]:rounded [&_code]:bg-halo-surface-2 [&_code]:px-1.5 [&_code]:py-0.5 [&_code]:text-[14px] [&_code]:font-mono ' +
                  '[&_blockquote]:border-l-4 [&_blockquote]:border-halo-border [&_blockquote]:pl-3 [&_blockquote]:text-halo-ink-dim'
                }
              >
                <ReactMarkdown
                  urlTransform={(url: string) => /^(https?:|mailto:)/i.test(url) ? url : ''}
                >
                  {ai}
                </ReactMarkdown>
              </div>
            ) : (
              <p className="mt-3.5 text-[15px] text-halo-ink-dim">
                {isToday ? t('coach.no_note') : t('coach.no_note_date')}
              </p>
            )
          )}
        </div>
      </div>
    </Layout>
  )
}
