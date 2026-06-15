import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, useNavigate, useSearchParams } from 'react-router-dom'
import Layout from '../components/Layout'
import { TopBar, Gauge, MiniRangeGauge, StackedBar, DateStrip, TrainingStrainCard, EnduranceScoreCard, type DatePill } from '../components/halo'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import TodayWorkoutCard from '../components/TodayWorkoutCard'
import { useDayNav } from '../hooks/useDayNav'
import { useChangelog } from '../hooks/useChangelog'
import { useApi } from '../hooks/useApi'
import { useAuth } from '../auth/useAuth'
import { apiFetch, ApiError } from '../api/client'
import { num, relativeTime, fmtDateYmd } from '../lib/formatters'
import { tsbZoneOf } from '../lib/constants'
import {
  classifyRecovery,
  recommendTraining,
  sleepZoneOf,
  RECOVERY_CHIP,
  RECOVERY_REC_COPY,
  STATUS_EMOJI,
  type RmssdStatus,
} from '../utils/recovery'
import type { WellnessResponse, WellnessResponseData, RecoveryTrendSeries } from '../api/types'

const fmtPct = (n: number) => (n >= 0 ? '+' : '') + num(n) + '%'
const fmtDelta = (n: number) => (n >= 0 ? '+' : '') + num(n)

/**
 * Plain-text preview for the coach-teaser строка (`ai_recommendation` приходит
 * markdown'ом — `**bold**`, заголовки, `---` разделители, эмодзи). Полный
 * рендер живёт на /coach через `ReactMarkdown`; здесь strip-markdown +
 * первая non-empty строка чтобы preview не показывал сырые `**…**`.
 *
 * Поддерживаются основные inline syntax: bold/italic, заголовки, `---` HR,
 * markdown links `[text](url)`, inline-code. Эмодзи и обычный текст остаются.
 */
function teaserText(md: string): string {
  const lines = md
    .split('\n')
    .map(l => l.trim())
    .filter(l => l && !/^-{3,}$/.test(l))
  for (const raw of lines) {
    const cleaned = raw
      .replace(/^#+\s+/, '')
      .replace(/\*\*([^*]+)\*\*/g, '$1')
      .replace(/(^|[^*])\*([^*]+)\*(?!\*)/g, '$1$2')
      .replace(/\[([^\]]+)\]\([^)]+\)/g, '$1')
      .replace(/`([^`]+)`/g, '$1')
      .trim()
    if (cleaned) return cleaned
  }
  return ''
}

const parseYmd = (s: string) => {
  const [y, m, d] = s.split('-').map(Number)
  return new Date(y, m - 1, d)
}

const ISO_DATE_RE = /^\d{4}-\d{2}-\d{2}$/

export default function Wellness() {
  const { t, i18n } = useTranslation()
  const { isDemo } = useAuth()
  // `?date=` deep-link — the All-history calendar opens a past day here.
  // useDayNav clamps it to ≤ today; only read once, on mount.
  const [searchParams] = useSearchParams()
  const dateParam = searchParams.get('date')
  const { currentDate, dateStr, isToday, goTo } = useDayNav(
    dateParam && ISO_DATE_RE.test(dateParam) ? parseYmd(dateParam) : undefined,
  )
  const { data, loading, error, reload } = useApi<WellnessResponse>(`/api/wellness-day?date=${dateStr}`)
  // Desktop-only Row 3 (HRV / RHR / Recovery · 7d) sparkline cards — prototype
  // `BdWellness` rows 1360-1474. Backed by /api/recovery-trend (the same endpoint
  // RecoveryTrend.tsx + WellnessHistory.tsx already consume). The series is
  // anchored to real "today" (the endpoint has no date param), so we only fetch
  // — and only render the sparkline cards — when the selected day IS today;
  // otherwise the spark would contradict the past-day headline values. Passing
  // `null` to useApi skips the request entirely on non-today views.
  const { data: trend } = useApi<RecoveryTrendSeries>(isToday ? '/api/recovery-trend?days=7' : null)
  const { changelog, unread, markRead } = useChangelog()
  const [showBreakdown, setShowBreakdown] = useState(false)

  const lang = i18n.language === 'en' ? 'en' : 'ru'
  const wdFmt = new Intl.DateTimeFormat(i18n.language, { weekday: 'short', day: 'numeric' })
  const rightDate = new Intl.DateTimeFormat(i18n.language, { weekday: 'short', month: 'short', day: 'numeric' }).format(currentDate)

  // Last 3 days ending at today (prototype `BDateStrip`: 2 past + Today). No
  // future pill — there's never wellness data for tomorrow. Older days live
  // behind the "All history" pill (the calendar heatmap).
  const todayMid = new Date()
  todayMid.setHours(0, 0, 0, 0)
  const todayYmd = fmtDateYmd(todayMid)
  const pills: DatePill[] = [-2, -1, 0].map(off => {
    const d = new Date(todayMid)
    d.setDate(d.getDate() + off)
    const key = fmtDateYmd(d)
    const base = wdFmt.format(d)
    const realToday = key === todayYmd
    return {
      key,
      label: realToday ? `${t('common.today_badge')} · ${base}` : base,
      today: key === dateStr, // cobalt = selected day
      future: key > todayYmd,
    }
  })

  return (
    <Layout maxWidth="480px">
      <div className="-mx-4 -mt-4 md:-mb-8 min-h-screen bg-halo-bg px-4 md:px-9 font-sans text-halo-ink">
        <TopBar title={t('nav.today')} right={rightDate} subtitle={t('wellness.desktop_subtitle')} />

        {isToday && unread && changelog && !isDemo && (
          <a
            href={changelog.url}
            target="_blank"
            rel="noopener noreferrer"
            onClick={markRead}
            className="mb-3 flex items-center gap-3 rounded-chip bg-halo-ink px-3.5 py-3 text-white no-underline"
          >
            <span
              aria-hidden="true"
              className="inline-flex h-[30px] w-[30px] items-center justify-center rounded-lg bg-white/10 text-sm"
            >
              ✨
            </span>
            <span className="min-w-0 flex-1">
              <span className="block text-[13px] font-semibold">{t('wellness.whats_new')}</span>
              <span className="mt-px block truncate text-[11px] text-white/70">{changelog.title}</span>
            </span>
            <span aria-hidden="true" className="text-[13px] text-white/70">↗</span>
            <button
              type="button"
              aria-label="dismiss"
              onClick={e => {
                e.preventDefault()
                e.stopPropagation()
                markRead()
              }}
              className="ml-0.5 border-none bg-transparent px-2 py-1 text-sm text-white/60"
            >
              ×
            </button>
          </a>
        )}

        <DateStrip
          pills={pills}
          onPick={k => goTo(parseYmd(k))}
          leading={
            <Link
              to="/wellness/history"
              aria-label={t('history.title')}
              className="inline-flex items-center gap-1 whitespace-nowrap rounded-pill border border-halo-border bg-transparent px-3 py-2 text-[11px] font-bold uppercase tracking-[0.5px] text-halo-ink-dim no-underline"
            >
              {t('history.title')}
              <span aria-hidden="true" className="text-[13px] leading-none text-halo-ink-dimmer">›</span>
            </Link>
          }
        />

        {isToday && data?.has_data && (
          <div className="flex items-center justify-between pb-3 text-xs text-halo-ink-dim">
            <span>
              {t('wellness.synced')}{' '}
              <span className="font-semibold text-halo-ink">{relativeTime(data.updated_at ?? null, i18n.language)}</span>
              {/* Точное HH:MM после relative — relative неточен для решения
                  «синкать ли», прототип BWellness:311 «Synced 27 min ago · 07:15» */}
              {data.updated_at && (
                <span className="text-halo-ink-dimmer">
                  {' · '}
                  {new Date(data.updated_at).toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' })}
                </span>
              )}
            </span>
            <RefreshButton onDone={reload} t={t} />
          </div>
        )}

        {loading && <LoadingSpinner />}
        {error && <ErrorMessage message={t('wellness.load_error')} />}

        {!loading && !error && data && !data.has_data && (
          <WellnessEmpty onJumpToday={() => goTo(todayMid)} t={t} />
        )}

        {!loading && !error && data?.has_data && (
          /* Mobile: single column (prototype `BWellness`), DOM order
             Recovery → HRV/RHR → Sleep → Strain → Load → Workout → Body →
             Coach (byte-identical — desktop-only cards carry `hidden`, so they
             drop out of mobile flow). Desktop (prototype `BdWellness`
             direction-b-desktop.jsx:1078): 12-col grid in the design's rows —
             Row1 Recovery(7) + Endurance(5); Row2 Sleep + Strain + Load;
             Row3 HRV + RHR + Recovery·7d (sparkline cards, desktop-only);
             Row4 Body; then Workout + Coach full-width. Endurance Score lives
             on both Today (desktop) and Trends — desktop has room, mobile
             stays lean. */
          <div className="flex flex-col gap-3.5 pb-4 md:grid md:grid-cols-12 md:items-start md:gap-[18px] md:[grid-auto-rows:max-content]">
            <div className="md:col-start-1 md:col-span-7 md:row-start-1">
              <RecoveryHero data={data} lang={lang} showBreakdown={showBreakdown} onToggle={() => setShowBreakdown(s => !s)} t={t} />
            </div>
            {/* Endurance Score — desktop-only (composite slow read; mobile keeps
                it on Trends → Load only). Self-fetches /api/endurance-score. */}
            <div className="hidden md:col-start-8 md:col-span-5 md:row-start-1 md:block">
              <EnduranceScoreCard />
            </div>
            {/* HRV + RHR combined tile — mobile always. On desktop it shows only
                for a past selected date (`!isToday`), standing in for the
                today-anchored Row-3 sparkline cards so the selected-day snapshot
                stays visible. On today, desktop hides it and uses the sparklines. */}
            <div className={isToday ? 'md:hidden' : 'md:col-start-1 md:col-span-8 md:row-start-3'}>
              <PairedMetrics data={data} t={t} />
            </div>
            {/* Row 2 (design Row1b): Sleep · Strain · Load — three equal cols. */}
            <div className="md:col-start-1 md:col-span-4 md:row-start-2">
              <SleepCard data={data} t={t} />
            </div>
            <div className="md:col-start-5 md:col-span-4 md:row-start-2">
              <TrainingStrainCard />
            </div>
            <div className="md:col-start-9 md:col-span-4 md:row-start-2">
              <TrainingLoadCard data={data} />
            </div>
            {/* Row 3 (design Row2): HRV · RHR · Recovery·7d — sparkline cards,
                desktop-only AND today-only (the 7-day series is anchored to real
                today; for a past selected date the PairedMetrics tile above takes
                Row-3 instead, and the trend fetch is skipped). */}
            {isToday && (
              <>
                <div className="hidden md:col-start-1 md:col-span-4 md:row-start-3 md:block">
                  <MetricTrendCard metric="hrv" data={data} trend={trend} lang={lang} t={t} />
                </div>
                <div className="hidden md:col-start-5 md:col-span-4 md:row-start-3 md:block">
                  <MetricTrendCard metric="rhr" data={data} trend={trend} lang={lang} t={t} />
                </div>
                <div className="hidden md:col-start-9 md:col-span-4 md:row-start-3 md:block">
                  <RecoveryTrendMiniCard data={data} trend={trend} lang={lang} t={t} />
                </div>
              </>
            )}
            {/* Plan vs Actual for the selected day — placed right before Body
                so the screen reads top-to-bottom as «how you feel» (Recovery /
                Endurance / HRV-RHR / Sleep / Load) → «what you trained» (Plan
                vs Actual + Body). Mobile follows JSX order, desktop reflows
                via row-start. */}
            <div className="md:col-span-12 md:col-start-1 md:row-start-4">
              <TodayWorkoutCard dateStr={dateStr} currentDate={currentDate} isToday={isToday} />
            </div>
            <div className="md:col-span-12 md:col-start-1 md:row-start-5">
              <BodyCard data={data} t={t} />
            </div>

            {/* Coach-note teaser → /coach (prototype BWellness, Halo v2 —
                reverses G3=(b)). Single-line peek at ai_recommendation; the
                recovery chip+rec stays the authoritative "what to do today".
                Only when the backend actually produced a note. */}
            {(isDemo || data.ai_recommendation?.trim()) && (
              <Link
                to={isToday ? '/coach' : `/coach?date=${dateStr}`}
                aria-label={t('wellness.coach_note')}
                className="flex w-full items-center gap-3 rounded-[18px] bg-halo-ink p-3.5 text-left text-white no-underline shadow-card md:col-span-12 md:col-start-1 md:row-start-6"
              >
                <span className="inline-flex h-8 w-8 shrink-0 items-center justify-center rounded-[10px] bg-white/10 text-[13px] font-bold tracking-[0.4px]">
                  AI
                </span>
                <span className="min-w-0 flex-1">
                  <span className="block text-[10px] font-bold uppercase tracking-[0.8px] text-white/55">
                    {t('wellness.coach_note')}
                  </span>
                  <span className="mt-[3px] block truncate text-[13px] leading-snug text-white/90">
                    {/* Demo: server stubs the note — teaser shows the canned sample's first line. */}
                    {isDemo ? t('demo.coach_teaser') : teaserText(data.ai_recommendation ?? '')}
                  </span>
                </span>
                <span aria-hidden="true" className="shrink-0 text-lg leading-none text-white/55">›</span>
              </Link>
            )}
          </div>
        )}
      </div>
    </Layout>
  )
}

type TFn = (k: string, o?: Record<string, unknown>) => string

// Recovery hero — Gauge arc + score + deterministic chip+rec (README §6 / G3=b).
function RecoveryHero({
  data,
  lang,
  showBreakdown,
  onToggle,
  t,
}: {
  data: WellnessResponseData
  lang: 'en' | 'ru'
  showBreakdown: boolean
  onToggle: () => void
  t: TFn
}) {
  const navigate = useNavigate()
  // `has_data: true` does NOT guarantee a score — cold-start / <14d-HRV rows
  // exist with recovery_score=null (backend early-returns). Never fabricate a
  // verdict from a phantom 0 (gotcha #3): bail to a neutral, honest state.
  const score = data.recovery?.score != null ? Math.round(data.recovery.score) : null
  const hasScore = score != null
  const rmssd = (data.hrv?.status ?? 'insufficient_data') as RmssdStatus
  const cat = hasScore ? classifyRecovery(score) : null
  const rec = cat ? recommendTraining(cat, rmssd) : null
  const chip = cat ? RECOVERY_CHIP[lang][cat] : null
  const recCopy = rec ? RECOVERY_REC_COPY[lang][rec] : null
  const isSkip = rec === 'skip'

  // 4 категории = 4 разных цвета (low/moderate/good/excellent), не
  // склеиваем good+excellent в один cobalt как раньше:
  //   low       → coral  (плохо, светофор-красный)
  //   moderate  → amber  (средне, светофор-жёлтый)
  //   good      → brand  (хорошо, кобальт)
  //   excellent → status-green (отлично, светофор-зелёный)
  // HRV-red skip override остаётся coral.
  const arcColor = !hasScore
    ? 'var(--color-ink-dimmer)'
    : isSkip || cat === 'low'
      ? 'var(--color-coral)'
      : cat === 'moderate'
        ? 'var(--color-amber)'
        : cat === 'good'
          ? 'var(--color-brand)'
          : 'var(--color-status-green)'
  // Track / chip wash — точные per-category тинты. Excellent получает
  // зелёный wash (#dcfce7, тот же что Dashboard TSB optimal-zone).
  const arcWash = !hasScore
    ? 'var(--color-surface-2)'
    : isSkip || cat === 'low'
      ? '#fde6e6'
      : cat === 'moderate'
        ? '#f5e6c8'
        : cat === 'good'
          ? 'var(--color-brand-light)'
          : '#dcfce7'

  const breakdown: { k: string; emoji: string | null; val: string; wt: number }[] = [
    {
      k: 'HRV',
      emoji: STATUS_EMOJI[(data.hrv?.status as RmssdStatus) ?? 'insufficient_data'],
      val: data.hrv?.delta_pct != null ? `${fmtPct(data.hrv.delta_pct)} ${t('wellness.vs_baseline')}` : '—',
      wt: 35,
    },
    {
      k: 'Banister',
      emoji: null,
      val: data.stress?.banister_recovery != null ? `${num(data.stress.banister_recovery, 0)} / 100` : '—',
      wt: 25,
    },
    {
      k: 'RHR',
      emoji: STATUS_EMOJI[(data.rhr?.status as RmssdStatus) ?? 'insufficient_data'],
      val: data.rhr?.status ? t(`status.${data.rhr.status}`) : '—',
      wt: 20,
    },
    {
      k: 'Sleep',
      emoji: null,
      val: data.sleep?.score != null ? `${data.sleep.score} / 100` : '—',
      wt: 20,
    },
  ]

  // The whole card taps through to /wellness/recovery — same hover + clickable
  // affordance as the Sleep/Body/Load cards. It can't be a real <Link>: the
  // "how score" disclosure below is a <button>, and a <button> nested in an
  // <a> is invalid HTML. So it's a role="link" div — the breakdown button
  // stops propagation, and the keyboard handler only fires for the card's own
  // focus (not bubbled from the button).
  const goToTrend = () => navigate('/wellness/recovery')
  return (
    <div
      role="link"
      tabIndex={0}
      aria-label={t('recovery_trend.title')}
      onClick={goToTrend}
      onKeyDown={e => {
        if (e.key === 'Enter' && e.target === e.currentTarget) {
          e.preventDefault()
          goToTrend()
        }
      }}
      className="cursor-pointer overflow-hidden rounded-card border border-halo-border bg-halo-surface shadow-card transition-colors hover:bg-halo-surface-2"
    >
      {/* Header — eyebrow + chevron affordance (the whole card → /wellness/recovery,
          the Recovery/HRV/RHR trend chart). */}
      <div className="flex items-center justify-between px-5 pt-5">
        <span className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
          {t('wellness.recovery')}
        </span>
        <span aria-hidden="true" className="text-[15px] leading-none text-halo-ink-dimmer">›</span>
      </div>
      {/* Mobile: vertical stack (gauge → chip+breakdown). Desktop (prototype
          `BdWellness` rows 259-337): row layout — gauge left, chip+breakdown
          right inside the same card so the wider hero earns its width. */}
      <div className="flex flex-col md:flex-row md:items-center md:gap-7 md:px-6 md:pb-5">
        <div className="flex justify-center pt-2 md:flex-shrink-0 md:pt-4">
          <Gauge
            width={240}
            height={220}
            cx={120}
            cy={120}
            r={92}
            strokeWidth={16}
            value={score}
            color={arcColor}
            trackColor={arcWash}
            /* Category boundaries from utils/recovery.classifyRecovery:
               <40 low / 40-70 moderate / 70-85 good / >85 excellent.
               Halo-v3 swap from prototype's visual 33/66 to the real
               backend gradations — data-honest. */
            ticks={hasScore ? [40, 70, 85] : undefined}
            endLabels={['0', '100']}
            center={(cx, cy) => (
              <>
                <text x={cx} y={cy + 4} textAnchor="middle" fontSize="64" fontWeight="600" fill="var(--color-ink)" letterSpacing="-3">
                  {score ?? '--'}
                </text>
                <text x={cx} y={cy + 30} textAnchor="middle" fontSize="12" fill="var(--color-ink-dim)" style={{ textTransform: 'uppercase' }}>
                  {cat ?? '—'}
                </text>
              </>
            )}
          />
        </div>
        <div className="flex flex-col gap-2 px-4 pb-4 md:min-w-0 md:flex-1 md:px-0 md:pb-0">
          {chip ? (
            <div className="flex items-center gap-2.5 rounded-chip px-3 py-2.5" style={{ backgroundColor: arcWash }}>
              <span aria-hidden="true" className="text-lg leading-none">{chip.emoji}</span>
              <div className="min-w-0 flex-1">
                <div className="text-[10px] font-semibold uppercase tracking-[0.5px] text-halo-ink-dim">{chip.label}</div>
                <div className="mt-0.5 text-[14px] font-semibold leading-snug text-halo-ink">{recCopy}</div>
              </div>
            </div>
          ) : (
            <div className="rounded-chip bg-halo-surface-2 px-3 py-2.5 text-[13px] text-halo-ink-dim">
              {t('wellness.score_unavailable')}
            </div>
          )}

          {isSkip && (
            <div
              className="flex items-start gap-2.5 rounded-chip px-3 py-2.5"
              style={{ background: '#fef2f2', border: '1px solid #fecaca' }}
            >
              <span aria-hidden="true" className="text-sm leading-tight">⚠</span>
              <div className="text-[12px] leading-snug" style={{ color: '#7f1d1d' }}>
                <strong>{t('wellness.hrv_override_title')}:</strong> {t('wellness.skip_override')}
              </div>
            </div>
          )}

          {/* stopPropagation — toggling the disclosure must not also fire the
              card's navigate-to-trend click. */}
          <button
            type="button"
            onClick={e => {
              e.stopPropagation()
              onToggle()
            }}
            aria-expanded={showBreakdown}
            aria-controls="recovery-breakdown"
            className="mt-0.5 flex w-full items-center justify-between rounded-chip border border-dashed border-halo-border px-3 py-2.5 text-[12px] font-semibold tracking-[0.2px] text-halo-ink-dim"
          >
            <span>{t('wellness.how_score')}</span>
            <span aria-hidden="true" className={`transition-transform ${showBreakdown ? 'rotate-180' : ''}`}>⌄</span>
          </button>
          {showBreakdown && (
            <div
              id="recovery-breakdown"
              onClick={e => e.stopPropagation()}
              className="flex flex-col gap-0.5 px-1 pt-1"
            >
              {breakdown.map(b => (
                <div key={b.k} className="grid grid-cols-[62px_18px_1fr_auto] items-center gap-2 px-2 py-2">
                  <span className="text-[12px] font-bold text-halo-ink">{b.k}</span>
                  <span className="text-center text-[12px] leading-none">{b.emoji || ''}</span>
                  <span className="text-[12px] font-medium text-halo-ink-dim">{b.val}</span>
                  <span className="min-w-[34px] rounded-pill bg-halo-brand-light px-1.5 py-0.5 text-center text-[10px] font-bold tracking-[0.4px] text-halo-brand-dark">
                    {b.wt}%
                  </span>
                </div>
              ))}
              <div className="mt-1.5 px-2 text-[10px] leading-relaxed text-halo-ink-dimmer">
                {t('wellness.breakdown_note')}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

// HRV + RHR — paired compact cards with a mini range gauge each.
function PairedMetrics({ data, t }: { data: WellnessResponseData; t: TFn }) {
  // Halo-v3 (2026-05-20) drop: delta-строки («+17.6% · 7d» / «−3.8 · 30d»)
  // убраны с плитки по дизайну — дельта + бэйслайн доступны на /wellness/:metric
  // drill-down (MetricDetail hero + Statistics).
  const metrics = [
    {
      k: 'HRV',
      val: data.hrv?.today,
      unit: t('wellness.ms'),
      status: (data.hrv?.status ?? 'insufficient_data') as RmssdStatus,
      lo: data.hrv?.lower_bound,
      hi: data.hrv?.upper_bound,
      cur: data.hrv?.today,
    },
    {
      k: 'RHR',
      val: data.rhr?.today,
      unit: 'bpm',
      status: (data.rhr?.status ?? 'insufficient_data') as RmssdStatus,
      lo: data.rhr?.lower_bound,
      hi: data.rhr?.upper_bound,
      cur: data.rhr?.today,
    },
  ]
  return (
    <div className="grid grid-cols-2 gap-2.5">
      {metrics.map(m => {
        const sColor = `var(--color-status-${m.status === 'insufficient_data' ? 'gray' : m.status})`
        return (
          /* Tile → /wellness/:metric drill-down (prototype `BMetricDetail`,
             halo-v3 drop). Same DOM/classes — just hoisted to a Link so the
             card itself is the tap target. */
          <Link
            key={m.k}
            to={`/wellness/${m.k.toLowerCase()}`}
            className="block rounded-card border border-halo-border bg-halo-surface p-3.5 no-underline text-inherit shadow-card transition-colors hover:bg-halo-surface-2"
          >
            <div className="flex items-center justify-between">
              <span className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">{m.k}</span>
              <span className="flex items-center gap-1.5 text-[13px] leading-none">
                {STATUS_EMOJI[m.status]}
                <span aria-hidden="true" className="text-halo-ink-dimmer">›</span>
              </span>
            </div>
            <div className="mt-1.5 flex items-baseline gap-1">
              <span className="text-[28px] font-semibold tracking-[-1px] text-halo-ink">
                {m.val != null ? num(m.val, m.k === 'RHR' ? 0 : 1) : '--'}
              </span>
              <span className="text-xs text-halo-ink-dim">{m.unit}</span>
            </div>
            {m.lo != null && m.hi != null && m.cur != null ? (
              <div className="mt-2.5">
                <MiniRangeGauge lo={m.lo} hi={m.hi} cur={m.cur} color={sColor} loLabel={num(m.lo, 0)} hiLabel={num(m.hi, 0)} />
              </div>
            ) : (
              <div className="mt-2.5 h-7" />
            )}
          </Link>
        )
      })}
    </div>
  )
}

// Shared 7-point sparkline for the desktop Row-3 trend cards (prototype
// `BdWellness` rows 1394-1406). Null days (gaps) are skipped — the line
// connects only present points; <2 points → fixed-height spacer so the card
// height stays stable while data loads.
function Spark({ values, color }: { values: (number | null)[]; color: string }) {
  const N = values.length
  const present = values
    .map((v, i) => ({ i, v }))
    .filter((p): p is { i: number; v: number } => p.v != null)
  if (present.length < 2) return <div className="mt-3.5 h-[60px]" />
  const W = 200
  const H = 50
  const vs = present.map(p => p.v)
  const lo = Math.min(...vs)
  const hi = Math.max(...vs)
  const pad = (hi - lo) * 0.15 + 0.5
  const yMin = lo - pad
  const yMax = hi + pad
  // present.length >= 2 guaranteed above ⇒ N >= 2, so (N - 1) is never 0.
  const x = (i: number) => (i / (N - 1)) * W
  const y = (v: number) => H - ((v - yMin) / (yMax - yMin)) * H
  const line = present.map((p, k) => `${k === 0 ? 'M' : 'L'} ${x(p.i).toFixed(1)} ${y(p.v).toFixed(1)}`).join(' ')
  const area = `${line} L ${x(present[present.length - 1].i).toFixed(1)} ${H} L ${x(present[0].i).toFixed(1)} ${H} Z`
  return (
    <svg viewBox={`0 0 ${W} ${H + 10}`} width="100%" height={H + 10} preserveAspectRatio="none" className="mt-3.5 block overflow-visible">
      <path d={area} fill={color} opacity="0.1" />
      <path d={line} fill="none" stroke={color} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
      {present.map((p, k) => (
        <circle
          key={p.i}
          cx={x(p.i)}
          cy={y(p.v)}
          r={k === present.length - 1 ? 3.5 : 2}
          fill={k === present.length - 1 ? color : '#fff'}
          stroke={color}
          strokeWidth="1.4"
        />
      ))}
    </svg>
  )
}

// Weekday labels under a spark — derived from real trend dates via Intl (not
// hardcoded RU as the prototype drew them; data-honest i18n, §9.3 precedent).
function SparkDays({ dates, lang }: { dates: string[] | undefined; lang: string }) {
  const fmt = new Intl.DateTimeFormat(lang, { weekday: 'short' })
  const labels = (dates ?? []).slice(-7).map(d => fmt.format(parseYmd(d.slice(0, 10))))
  if (labels.length === 0) return null
  return (
    <div className="mt-1 flex justify-between text-[9px] font-semibold tracking-[0.3px] text-halo-ink-dimmer">
      {labels.map((d, i) => (
        <span key={i}>{d}</span>
      ))}
    </div>
  )
}

// HRV / RHR desktop sparkline card (prototype `BdWellness` rows 1361-1422):
// value + delta + 7-day spark + min/max range strip. Mobile keeps the combined
// PairedMetrics tile. Whole card → /wellness/:metric drill-down.
function MetricTrendCard({
  metric,
  data,
  trend,
  lang,
  t,
}: {
  metric: 'hrv' | 'rhr'
  data: WellnessResponseData
  trend: RecoveryTrendSeries | null
  lang: string
  t: TFn
}) {
  const block = metric === 'hrv' ? data.hrv : data.rhr
  const status = (block?.status ?? 'insufficient_data') as RmssdStatus
  const sColor = `var(--color-status-${status === 'insufficient_data' ? 'gray' : status})`
  const last7 = ((metric === 'hrv' ? trend?.hrv : trend?.rhr) ?? []).slice(-7)
  const today = block?.today
  const unit = metric === 'hrv' ? t('wellness.ms') : 'bpm'
  // HRV → % vs baseline; RHR has no %-delta (inverted bpm metric) so use its
  // raw 30-day delta. Both keep the design's delta line, so the two cards stay
  // the same height in the row.
  const deltaText =
    metric === 'hrv'
      ? data.hrv?.delta_pct != null
        ? fmtPct(data.hrv.delta_pct)
        : null
      : data.rhr?.delta_30d != null
        ? fmtDelta(data.rhr.delta_30d)
        : null
  const lo = block?.lower_bound
  const hi = block?.upper_bound
  return (
    <Link
      to={`/wellness/${metric}`}
      className="block rounded-card border border-halo-border bg-halo-surface p-[18px] no-underline text-inherit shadow-card transition-colors hover:bg-halo-surface-2"
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">{metric.toUpperCase()}</span>
        <span className="flex items-center gap-1.5 text-[13px] leading-none">
          {STATUS_EMOJI[status]}
          <span aria-hidden="true" className="text-halo-ink-dimmer">›</span>
        </span>
      </div>
      <div className="mt-2 flex items-baseline gap-1.5">
        <span className="text-[34px] font-semibold tracking-[-1px] text-halo-ink">
          {today != null ? num(today, metric === 'rhr' ? 0 : 1) : '--'}
        </span>
        <span className="text-[13px] text-halo-ink-dim">{unit}</span>
      </div>
      {deltaText != null && (
        <div className="text-[12px] font-semibold" style={{ color: sColor }}>
          {deltaText} {t('wellness.vs_baseline')}
        </div>
      )}
      <Spark values={last7} color={sColor} />
      <SparkDays dates={trend?.dates} lang={lang} />
      {lo != null && hi != null && today != null && (
        <div className="mt-3">
          <MiniRangeGauge lo={lo} hi={hi} cur={today} color={sColor} loLabel={num(lo, 0)} hiLabel={num(hi, 0)} />
        </div>
      )}
    </Link>
  )
}

// Recovery · 7-day trend card (prototype `BdWellness` rows 1424-1474): today's
// score + week average + 7-day spark + min/today/max strip. Desktop Row-3 third
// slot; whole card → /wellness/recovery.
function RecoveryTrendMiniCard({
  data,
  trend,
  lang,
  t,
}: {
  data: WellnessResponseData
  trend: RecoveryTrendSeries | null
  lang: string
  t: TFn
}) {
  const score = data.recovery?.score != null ? Math.round(data.recovery.score) : null
  const last7 = (trend?.recovery ?? []).slice(-7)
  const present = last7.filter((v): v is number => v != null)
  const avg = present.length ? Math.round(present.reduce((a, b) => a + b, 0) / present.length) : null
  const lo = present.length ? Math.round(Math.min(...present)) : null
  const hi = present.length ? Math.round(Math.max(...present)) : null
  return (
    <Link
      to="/wellness/recovery"
      className="block rounded-card border border-halo-border bg-halo-surface p-[18px] no-underline text-inherit shadow-card transition-colors hover:bg-halo-surface-2"
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">{t('wellness.recovery_7d')}</span>
        <span aria-hidden="true" className="text-[15px] leading-none text-halo-ink-dimmer">›</span>
      </div>
      <div className="mt-2 flex items-baseline gap-1">
        <span className="text-[34px] font-semibold tracking-[-1px] text-halo-ink">{score ?? '--'}</span>
        <span className="text-[13px] text-halo-ink-dim">/ 100</span>
      </div>
      {avg != null && <div className="text-[12px] font-semibold text-halo-ink-dim">{t('wellness.week_avg', { val: avg })}</div>}
      <Spark values={last7} color="var(--color-status-green)" />
      <SparkDays dates={trend?.dates} lang={lang} />
      {lo != null && hi != null && (
        <div className="mt-3 flex items-center justify-between text-[11px] font-semibold text-halo-ink-dim">
          <span>{t('wellness.range_min', { val: lo })}</span>
          {score != null && (
            <span className="text-halo-ink">
              {t('common.today_badge')} {score}
            </span>
          )}
          <span>{t('wellness.range_max', { val: hi })}</span>
        </div>
      )}
    </Link>
  )
}

// Sleep — duration + score + last-7-nights vertical bars (прототип BWellness
// direction-b-halo.jsx:469-523). Bars передают variance (полезнее single
// score); каждый bar окрашен по своей score-зоне (`sleepZoneOf` — single
// source of truth, общий со Sleep-trend экраном): today плотным `line`-цветом,
// прошлые ночи тем же цветом но dimmed. Вся карта — тап на /wellness/sleep.
function SleepCard({ data, t }: { data: WellnessResponseData; t: TFn }) {
  const score = data.sleep?.score ?? null
  const nights = data.sleep?.last_7_nights ?? []
  const zone = score != null ? sleepZoneOf(score) : null
  const BAR_H_MAX = 56
  const BAR_H_MIN = 6
  return (
    <Link
      to="/wellness/sleep"
      className="block rounded-card border border-halo-border bg-halo-surface p-[18px] no-underline text-inherit shadow-card transition-colors hover:bg-halo-surface-2"
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">{t('wellness.sleep')}</span>
        <span aria-hidden="true" className="text-[15px] leading-none text-halo-ink-dimmer">›</span>
      </div>
      <div className="mt-1 flex items-center justify-between gap-3.5">
        <div className="min-w-0">
          <div className="text-[28px] font-semibold tracking-[-0.5px] text-halo-ink">{data.sleep?.duration || '--'}</div>
          <div className="mt-0.5 flex items-center gap-1.5">
            <span className="text-[13px] text-halo-ink-dim">
              {score != null ? t('wellness.sleep_score', { score }) : '--'}
            </span>
            {zone && (
              <span
                className="rounded-pill px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.5px]"
                style={{ color: zone.line, background: zone.fill }}
              >
                {zone.label}
              </span>
            )}
          </div>
        </div>
        {nights.length > 0 && (
          <div className="flex h-14 shrink-0 items-end gap-[5px]">
            {nights.map((s, i) => {
              const isToday = i === nights.length - 1
              // Height ∝ score; min 6px чтобы плохая ночь не исчезала, null=min.
              const h = s != null ? Math.max(BAR_H_MIN, (s / 100) * BAR_H_MAX) : BAR_H_MIN
              const barZone = s != null ? sleepZoneOf(s) : null
              return (
                <div
                  key={i}
                  title={s != null ? String(Math.round(s)) : '—'}
                  style={{
                    width: 8,
                    height: h,
                    borderRadius: 4,
                    background: barZone ? barZone.line : 'var(--color-surface-2)',
                    opacity: barZone && !isToday ? 0.4 : 1,
                    border: isToday ? 'none' : '1px solid var(--color-border)',
                  }}
                />
              )
            })}
          </div>
        )}
      </div>
      {nights.length > 0 && (
        <div className="mt-1.5 flex justify-end text-[9px] font-bold uppercase tracking-[0.4px] text-halo-ink-dimmer">
          {t('wellness.last_7_nights')}
        </div>
      )}
    </Link>
  )
}

// Training load — 3-col Fitness/Fatigue/Form + stacked swim/ride/run bar.
// Chrome (label/headers/sport-legend) = literal English по запросу
// пользователя: «тут на английском все можно» — Training-load Halo-vocabulary
// (CTL/ATL/TSB + Fitness/Fatigue/Form + Swim/Ride/Run + «Ramp / sweet spot»)
// бренд-стандарт триатлонных метрик, читается одинаково в обеих локалях.
// Тот же де-i18n паттерн, что у Settings chrome (§9.3 / §10.3).
function TrainingLoadCard({ data }: { data: WellnessResponseData }) {
  const tl = data.training_load
  const sc = tl?.sport_ctl
  const cells = [
    { k: 'Fitness', sub: 'CTL', val: tl?.ctl, signed: false },
    { k: 'Fatigue', sub: 'ATL', val: tl?.atl, signed: false },
    { k: 'Form', sub: 'TSB', val: tl?.tsb, signed: true },
  ]
  // Sport-color convention used across the app (lib/constants.sportColor +
  // Dashboard SPORT_META): Swim=amber, Ride=brand-cobalt, Run=coral.
  const seg = [
    { v: sc?.swim ?? 0, c: 'var(--color-amber)', label: 'Swim' },
    { v: sc?.ride ?? 0, c: 'var(--color-brand)', label: 'Ride' },
    { v: sc?.run ?? 0, c: 'var(--color-coral)', label: 'Run' },
  ]
  const hasSeg = seg.some(s => s.v > 0)
  return (
    <Link
      to="/wellness/load"
      className="block rounded-card border border-halo-border bg-halo-surface p-[18px] no-underline text-inherit shadow-card transition-colors hover:bg-halo-surface-2"
    >
      <div className="flex items-baseline justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
          Training load
        </span>
        <span className="inline-flex items-baseline gap-2">
          {tl?.ramp_rate != null && (
            <span className="text-[11px] font-semibold text-halo-brand-dark">
              Ramp {fmtDelta(tl.ramp_rate)} · sweet spot
            </span>
          )}
          <span aria-hidden="true" className="self-center text-[15px] leading-none text-halo-ink-dimmer">›</span>
        </span>
      </div>
      <div className="mt-3 grid grid-cols-3 gap-3.5">
        {cells.map(c => {
          // Form (TSB) coloured by the 5-band gradation (risk/optimal/gray/
          // fresh/transition — `lib/constants.TSB_ZONES`, shared with LoadDetail).
          // CTL/ATL stay regular ink.
          const colour =
            c.signed && c.val != null
              ? tsbZoneOf(c.val).line
              : 'var(--color-ink)'
          return (
            <div key={c.sub}>
              <div className="text-[11px] font-semibold text-halo-ink-dim">{c.k}</div>
              <div
                className="mt-0.5 text-[22px] font-semibold tracking-[-0.5px]"
                style={{ color: colour }}
              >
                {c.val != null ? (c.signed && c.val > 0 ? '+' : '') + num(c.val) : '--'}
              </div>
              <div className="mt-px text-[10px] uppercase tracking-[0.6px] text-halo-ink-dimmer">{c.sub}</div>
            </div>
          )
        })}
      </div>
      {hasSeg && (
        <div className="mt-3.5">
          <StackedBar
            segments={seg.map(s => ({ flex: s.v, color: s.c }))}
            height={10}
            track="var(--color-brand-light)"
          />
          <div className="mt-1.5 flex justify-between text-[10px] font-medium text-halo-ink-dim">
            {seg.map(s => (
              <span key={s.label} className="inline-flex items-center gap-1">
                <span className="h-1.5 w-1.5 rounded-full" style={{ background: s.c }} />
                {s.label} {num(s.v, 1)}
              </span>
            ))}
          </div>
        </div>
      )}
    </Link>
  )
}

// Body — Weight / Body fat / VO₂max / Steps as a 2×2 grid (прототип BWellness
// direction-b-halo.jsx:573-602). Карта тапается → /wellness/body (Body-trend
// экран с графиками по каждой метрике). Decorative sub-captions прототипа
// убраны — реальных trend-данных на /wellness-day нет; тренд живёт на детали.
function BodyCard({ data, t }: { data: WellnessResponseData; t: TFn }) {
  const b = data.body
  if (!b || (b.weight == null && b.body_fat == null && b.vo2max == null && b.steps == null)) return null
  const cells = [
    { k: t('wellness.weight'), val: b.weight != null ? num(b.weight) : '--', unit: t('common.kg') },
    { k: t('wellness.body_fat'), val: b.body_fat != null ? num(b.body_fat) : '--', unit: '%' },
    { k: t('wellness.vo2max'), val: b.vo2max != null ? num(b.vo2max) : '--', unit: '' },
    { k: t('wellness.steps'), val: b.steps != null ? b.steps.toLocaleString() : '--', unit: '' },
  ]
  return (
    <Link
      to="/wellness/body"
      className="block rounded-card border border-halo-border bg-halo-surface p-[18px] no-underline text-inherit shadow-card transition-colors hover:bg-halo-surface-2"
    >
      <div className="flex items-center justify-between">
        <span className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">{t('wellness.body')}</span>
        <span aria-hidden="true" className="text-[15px] leading-none text-halo-ink-dimmer">›</span>
      </div>
      {/* Mobile: 2×2 grid. Desktop (prototype `BdWellness` rows 567-583):
          single-row 4-col strip — weight/BF/VO₂/steps fit on one line at
          1180px content width. */}
      <div className="mt-2.5 grid grid-cols-2 gap-x-[18px] gap-y-3.5 md:grid-cols-4 md:gap-x-6 md:gap-y-0">
        {cells.map(c => (
          <div key={c.k}>
            <div className="text-[11px] font-semibold text-halo-ink-dim">{c.k}</div>
            <div className="mt-0.5 text-[22px] font-semibold tracking-[-0.5px] text-halo-ink md:text-[28px] md:mt-1">
              {c.val}
              {c.unit && <span className="text-[11px] font-medium text-halo-ink-dim"> {c.unit}</span>}
            </div>
          </div>
        ))}
      </div>
    </Link>
  )
}

// «Обновить» кнопка — диспатчит actor_user_wellness через POST /api/jobs/
// refresh-wellness (backend handles 60s cooldown). После dispatch'а ждём
// REFRESH_WAIT_MS чтобы Dramatiq worker успел отработать (wellness fetch +
// HRV/RHR fan-out), затем reload `useApi` чтобы экран показал свежие данные.
// На 429 показываем countdown по `retry_after_sec` из ответа и блокируем
// кнопку до окончания cooldown.
const REFRESH_WAIT_MS = 12000

function RefreshButton({ onDone, t }: { onDone: () => void; t: TFn }) {
  const [busy, setBusy] = useState(false)
  const [cooldown, setCooldown] = useState(0)
  // Pending post-dispatch refetch timer — cleared on unmount so the delayed
  // onDone()/setBusy() never fire on an unmounted component.
  const waitTimer = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(
    () => () => {
      if (waitTimer.current) clearTimeout(waitTimer.current)
    },
    [],
  )

  // Cooldown countdown — single 1Hz interval only while ticking, to avoid
  // a forever-spinning timer when idle.
  const ticking = cooldown > 0
  useEffect(() => {
    if (!ticking) return
    const id = setInterval(() => setCooldown(c => Math.max(0, c - 1)), 1000)
    return () => clearInterval(id)
  }, [ticking])

  const onClick = async () => {
    if (busy || cooldown > 0) return
    setBusy(true)
    try {
      await apiFetch('/api/jobs/refresh-wellness', { method: 'POST' })
      // Worker is async; wait for fan-out to settle, then refetch.
      waitTimer.current = setTimeout(() => {
        waitTimer.current = null
        onDone()
        setBusy(false)
      }, REFRESH_WAIT_MS)
    } catch (e) {
      setBusy(false)
      if (e instanceof ApiError && e.status === 429) {
        const d = e.detail as { retry_after_sec?: number } | null
        const wait = d?.retry_after_sec ?? 60
        setCooldown(wait)
      }
    }
  }

  const disabled = busy || cooldown > 0
  const label = busy
    ? t('wellness.refresh_running')
    : cooldown > 0
      ? t('wellness.refresh_wait', { sec: cooldown })
      : t('wellness.refresh')
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled}
      className="inline-flex items-center gap-1.5 rounded-pill border border-halo-border bg-halo-surface px-2.5 py-1.5 text-[11px] font-semibold tracking-[0.2px] text-halo-ink-dim disabled:cursor-not-allowed disabled:opacity-60"
    >
      {busy ? (
        <span aria-hidden="true" className="inline-block h-3 w-3 animate-spin rounded-full border-2 border-current/30 border-t-current" />
      ) : (
        <span aria-hidden="true" className="text-[13px] leading-none">↻</span>
      )}
      {label}
    </button>
  )
}

// Empty state (has_data:false) — ghost arc + neutral waiting copy. Drops the
// prototype's fabricated "yesterday tip" card, the "Garmin syncs by 6:30"
// line, and the unbacked "Force sync now" button (no such API). Logged as a
// data-honesty deviation in WEBAPP_HALO_REDESIGN_SPEC.
function WellnessEmpty({ onJumpToday, t }: { onJumpToday: () => void; t: TFn }) {
  return (
    <div className="flex flex-col gap-3.5 pb-4">
      <div className="overflow-hidden rounded-card border border-halo-border bg-halo-surface text-center shadow-card">
        <div className="px-5 pb-2 pt-6">
          <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
            {t('wellness.recovery')}
          </div>
          <div className="mt-1 text-[15px] text-halo-ink-dim">{t('wellness.waiting_sync')}</div>
        </div>
        <div className="flex justify-center pt-2">
          <Gauge
            width={220}
            height={180}
            cx={110}
            cy={110}
            r={92}
            strokeWidth={16}
            value={null}
            color="var(--color-ink-dimmer)"
            trackColor="var(--color-surface-2)"
            center={(cx, cy) => (
              <>
                <text x={cx} y={cy - 2} textAnchor="middle" fontSize="48" fontWeight="600" fill="var(--color-ink-dimmer)" letterSpacing="-2">
                  —
                </text>
                <text x={cx} y={cy + 22} textAnchor="middle" fontSize="11" fill="var(--color-ink-dimmer)" style={{ textTransform: 'uppercase' }}>
                  {t('wellness.no_data_short')}
                </text>
              </>
            )}
          />
        </div>
        <div className="px-6 pb-6 text-center text-[15px] text-halo-ink-dim">{t('wellness.no_data')}</div>
      </div>

      <button
        type="button"
        onClick={onJumpToday}
        className="rounded-card border-none bg-halo-ink py-3.5 text-[15px] font-semibold text-white"
      >
        {t('wellness.jump_today')}
      </button>
      <Link
        to="/calendar"
        className="py-2.5 text-center text-[13px] font-semibold text-halo-ink-dim no-underline"
      >
        {t('wellness.tomorrow_plan')} →
      </Link>
    </div>
  )
}
