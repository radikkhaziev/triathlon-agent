import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import type { TFunction } from 'i18next'
import { apiFetch } from '../api/client'

// Must mirror BackfillStatusResponse in api/dto.py. Kept inline instead of
// in api/types.ts because this is the only component that consumes it.
interface BackfillStatus {
  status: 'none' | 'running' | 'completed' | 'failed'
  cursor_dt?: string | null
  oldest_dt?: string | null
  newest_dt?: string | null
  progress_pct?: number
  chunks_done?: number
  period_days?: number | null
  started_at?: string | null
  finished_at?: string | null
  last_error?: string | null
}

// Matches the cooldown windows in api/routers/auth.py. When finished_at +
// cooldown is in the past, a retry is allowed — otherwise the button is
// disabled and we show the remaining time.
const COOLDOWN_EMPTY_SEC = 60 * 60          // EMPTY_INTERVALS → 1h
const COOLDOWN_COMPLETED_SEC = 7 * 24 * 3600 // completed + data → 7d
const POLL_INTERVAL_MS = 5000

function isEmptyImport(s: BackfillStatus): boolean {
  return s.status === 'completed' && s.last_error === 'EMPTY_INTERVALS'
}

// Translate known server sentinels into a user-facing explanation. Anything
// not in this map (including the collapsed "internal" catchall — the server
// sanitizes unknown errors to that) falls through to the generic message so
// we never leak raw exception strings. Keep in sync with
// ``_LAST_ERROR_ALLOWLIST`` in ``api/routers/auth.py``.
function explainLastError(
  raw: string | null | undefined,
  t: TFunction,
): string | null {
  if (!raw) return null
  if (raw === 'watchdog_exhausted') return t('settings.backfill.error_watchdog_exhausted')
  if (raw === 'OAuth revoked during backfill') return t('settings.backfill.error_oauth_revoked')
  if (raw === 'EMPTY_INTERVALS') return null  // handled via isEmptyImport branch
  return t('settings.backfill.error_generic')
}

function cooldownRemainingSec(s: BackfillStatus, now: number): number {
  if (s.status !== 'completed' || !s.finished_at) return 0
  const cooldown = isEmptyImport(s) ? COOLDOWN_EMPTY_SEC : COOLDOWN_COMPLETED_SEC
  const finishedMs = Date.parse(s.finished_at)
  if (Number.isNaN(finishedMs)) return 0
  const remainingMs = finishedMs + cooldown * 1000 - now
  return Math.max(0, Math.ceil(remainingMs / 1000))
}

function formatCountdown(totalSec: number, t: TFunction): string {
  if (totalSec <= 0) return ''
  if (totalSec < 60) return t('settings.backfill.available_in_seconds', { seconds: totalSec })
  if (totalSec < 3600) return t('settings.backfill.available_in_minutes', { minutes: Math.ceil(totalSec / 60) })
  if (totalSec < 86400) return t('settings.backfill.available_in_hours', { hours: Math.ceil(totalSec / 3600) })
  return t('settings.backfill.available_in_days', { days: Math.ceil(totalSec / 86400) })
}

export default function BackfillSection() {
  const { t } = useTranslation()
  const [status, setStatus] = useState<BackfillStatus | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null)

  // Single fetch helper — used on mount, after retry, and by the poll tick.
  const fetchStatus = async () => {
    try {
      const s = await apiFetch<BackfillStatus>('/api/auth/backfill-status')
      setStatus(s)
      return s
    } catch {
      // Keep old state on transient failures so UI doesn't flicker to empty;
      // the next tick will try again.
      return null
    }
  }

  useEffect(() => {
    fetchStatus()
  }, [])

  // Poll every 5s while running. The tick also bumps `now` so the disabled-
  // countdown text updates without a separate timer. We stop the interval on
  // any non-running state to avoid hammering the API after finalization.
  useEffect(() => {
    if (status?.status !== 'running') {
      if (pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
      return
    }
    pollRef.current = setInterval(() => {
      fetchStatus()
      setNow(Date.now())
    }, POLL_INTERVAL_MS)
    return () => {
      if (pollRef.current) {
        clearInterval(pollRef.current)
        pollRef.current = null
      }
    }
  }, [status?.status])

  // Separate 1-Hz tick for the disabled-countdown display. Only runs when
  // we're showing a cooldown, so idle sessions don't spin a timer forever.
  const countdownSec = status ? cooldownRemainingSec(status, now) : 0
  const showCountdown = status && isEmptyImport(status) && countdownSec > 0
  useEffect(() => {
    if (!showCountdown) return
    const id = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(id)
  }, [showCountdown])

  const handleRetry = async () => {
    if (busy) return
    setBusy(true)
    setError(null)
    try {
      await apiFetch<{ status: string }>('/api/auth/retry-backfill', { method: 'POST' })
      await fetchStatus()
    } catch (e) {
      const msg = e instanceof Error ? e.message : String(e)
      // 409 / 429 messages come through as `detail` text via apiFetch.
      setError(msg || t('settings.backfill.retry_failed'))
    } finally {
      setBusy(false)
    }
  }

  if (!status) return null

  // --- Running: progress bar + poll in background ---------------------------
  if (status.status === 'running') {
    const pct = Math.round(status.progress_pct ?? 0)
    return (
      <div>
        <p className="text-[12px] text-text-dim mb-2 leading-snug">
          {t('settings.backfill.in_progress_desc')}
        </p>
        <div className="w-full h-2 bg-surface-2 rounded-full overflow-hidden">
          <div
            className="h-full bg-accent transition-all duration-300"
            style={{ width: `${pct}%` }}
          />
        </div>
        <div className="flex justify-between mt-1.5 text-[11px] text-text-dim font-mono">
          <span>{pct}%</span>
          <span>{t('settings.backfill.chunks_done', { done: status.chunks_done ?? 0 })}</span>
        </div>
      </div>
    )
  }

  // --- Completed + data + <7d: quiet success, no button --------------------
  if (status.status === 'completed' && !isEmptyImport(status) && countdownSec > 0) {
    return (
      <div className="text-[13px] text-green">
        ✅ {t('settings.backfill.completed')}
      </div>
    )
  }

  // --- Button variants ------------------------------------------------------
  let label: string
  let variant: 'primary' | 'secondary' | 'danger' | 'disabled'
  let tooltip: string | undefined

  if (status.status === 'none') {
    label = t('settings.backfill.button_first')
    variant = 'primary'
  } else if (status.status === 'failed') {
    label = t('settings.backfill.button_failed')
    variant = 'danger'
    tooltip = explainLastError(status.last_error, t) || undefined
  } else if (isEmptyImport(status) && countdownSec > 0) {
    label = formatCountdown(countdownSec, t)
    variant = 'disabled'
  } else if (isEmptyImport(status)) {
    label = t('settings.backfill.button_empty_retry')
    variant = 'primary'
  } else {
    // completed + data + ≥7d — resync allowed
    label = t('settings.backfill.button_resync')
    variant = 'secondary'
  }

  const classes = {
    primary: 'bg-accent text-white border-accent',
    secondary: 'bg-surface border-border text-text hover:bg-surface-2',
    danger: 'bg-surface border-red text-red hover:bg-red/5',
    disabled: 'bg-surface-2 border-border text-text-dim cursor-not-allowed',
  }[variant]

  return (
    <div>
      <button
        type="button"
        onClick={handleRetry}
        disabled={busy || variant === 'disabled'}
        title={tooltip}
        className={`flex items-center justify-center gap-2 w-full py-2.5 rounded-xl text-sm font-semibold border cursor-pointer font-sans disabled:opacity-60 disabled:cursor-not-allowed ${classes}`}
      >
        {busy && (
          <span className="inline-block w-3.5 h-3.5 border-2 border-current/30 border-t-current rounded-full animate-spin" />
        )}
        {label}
      </button>
      {error && (
        <p className="text-[11px] text-red mt-2">{error}</p>
      )}
      {variant === 'danger' && (
        <p className="text-[11px] text-text-dim mt-2 leading-snug">
          {explainLastError(status.last_error, t)}
        </p>
      )}
    </div>
  )
}
