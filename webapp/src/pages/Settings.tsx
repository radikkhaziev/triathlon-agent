import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Layout from '../components/Layout'
import BackfillSection from '../components/BackfillSection'
import { useAuth } from '../auth/useAuth'
import { apiFetch } from '../api/client'
import type { AuthMeResponse, IntervalsStatus } from '../api/types'

type McpConfig = { url: string; token: string }

type IntervalsToast = {
  kind: 'success' | 'error'
  key: string  // i18n key
}

function parseIntervalsQueryParam(search: string): IntervalsToast | null {
  const params = new URLSearchParams(search)
  if (params.get('connected') === 'intervals') {
    return { kind: 'success', key: 'settings.intervals.toast_connected' }
  }
  const error = params.get('error')
  if (!error) return null
  if (error === 'oauth_cancelled') return { kind: 'error', key: 'settings.intervals.toast_cancelled' }
  if (error === 'oauth_account_mismatch') return { kind: 'error', key: 'settings.intervals.toast_mismatch' }
  if (error.startsWith('oauth_')) return { kind: 'error', key: 'settings.intervals.toast_error' }
  return null
}

function buildMcpJsonSnippet(url: string, token: string): string {
  return JSON.stringify(
    {
      mcpServers: {
        endurai: {
          type: 'http',
          url,
          headers: { Authorization: `Bearer ${token}` },
        },
      },
    },
    null,
    2,
  )
}

export default function Settings() {
  const { t, i18n } = useTranslation()
  const { logout, isAuthenticated, isDemo } = useAuth()
  const [mcpConfig, setMcpConfig] = useState<McpConfig | null>(null)
  const [mcpError, setMcpError] = useState<string | null>(null)
  const [mcpRevealed, setMcpRevealed] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const [intervals, setIntervals] = useState<IntervalsStatus | null>(null)
  const [intervalsToast, setIntervalsToast] = useState<IntervalsToast | null>(null)
  const [intervalsBusy, setIntervalsBusy] = useState(false)
  // Issue #266: track whether the user has actually opened a chat with the
  // bot. Login Widget signups land with ``false`` and must press /start in
  // the bot before OAuth — otherwise notifications 400 with chat-not-found.
  const [botChatInitialized, setBotChatInitialized] = useState<boolean | null>(null)
  const [botUsername, setBotUsername] = useState<string | null>(null)
  const [profile, setProfile] = useState<{
    age?: number | null
    lthr_run?: number | null
    lthr_bike?: number | null
    ftp?: number | null
    css?: number | null
  } | null>(null)
  const [goal, setGoal] = useState<{
    id?: number | null
    event_name: string
    event_date: string
    ctl_target?: number | null
    per_sport_targets?: { swim?: number; ride?: number; run?: number } | null
  } | null>(null)
  const [goalSaveError, setGoalSaveError] = useState<string | null>(null)

  useEffect(() => {
    if (!isAuthenticated) return
    apiFetch<McpConfig>('/api/auth/mcp-config')
      .then(setMcpConfig)
      .catch((e: Error) => setMcpError(e.message || 'Failed to load MCP config'))
  }, [isAuthenticated])

  // Intervals.icu connection status — separate fetch from /api/auth/me so
  // the Settings page owns its own state without waiting on App-level context.
  // On any fetch failure we fall back to a `none` status instead of leaving
  // `intervals=null` forever, which would stick the UI in the "loading" text.
  useEffect(() => {
    if (!isAuthenticated) return
    apiFetch<AuthMeResponse & { profile?: typeof profile; goal?: typeof goal }>('/api/auth/me')
      .then(data => {
        setIntervals(data.intervals ?? { method: 'none', athlete_id: null, scope: null })
        // Default to true on missing field so old API responses don't lock
        // existing users out of the OAuth button — only an explicit `false`
        // from a fresh server triggers the /start gate.
        setBotChatInitialized(data.bot_chat_initialized ?? true)
        setBotUsername(data.bot_username ?? null)
        if (data.profile) setProfile(data.profile)
        if (data.goal) setGoal(data.goal)
      })
      .catch(() => {
        setIntervals({ method: 'none', athlete_id: null, scope: null })
        setBotChatInitialized(true)
      })
  }, [isAuthenticated])

  // One-shot toast after OAuth callback redirect. Clears `?connected=` or
  // `?error=` from the URL so a reload doesn't re-fire the toast.
  useEffect(() => {
    const toast = parseIntervalsQueryParam(window.location.search)
    if (!toast) return
    setIntervalsToast(toast)
    const url = new URL(window.location.href)
    url.searchParams.delete('connected')
    url.searchParams.delete('error')
    window.history.replaceState({}, '', url.toString())
    const timer = setTimeout(() => setIntervalsToast(null), 5000)
    return () => clearTimeout(timer)
  }, [])

  // Monotonic request-id so a late failure from an older PATCH doesn't
  // rollback the state to a value that has since been overwritten by a newer
  // successful PATCH. We only roll back if the failing request is the most
  // recent one we've issued.
  const patchSeq = useRef(0)
  const lastSuccessfulSeq = useRef(0)

  // Push a local-only goal edit (ctl_target / per_sport_targets) to the backend.
  // Applies optimistic update first; on failure rolls back and sets an inline
  // error message so the row re-mounts with the original value — provided the
  // failing request is still the latest (see patchSeq above).
  const patchGoal = async (
    patch: Partial<{ ctl_target: number | null; per_sport_targets: Record<string, number | null> }>,
  ) => {
    if (!goal?.id) return
    const seq = ++patchSeq.current
    const prev = goal
    const next = {
      ...goal,
      ...(patch.ctl_target !== undefined ? { ctl_target: patch.ctl_target } : {}),
      ...(patch.per_sport_targets !== undefined
        ? {
            per_sport_targets: {
              ...(goal.per_sport_targets ?? {}),
              ...patch.per_sport_targets,
            },
          }
        : {}),
    }
    setGoal(next)
    setGoalSaveError(null)
    try {
      await apiFetch(`/api/athlete/goal/${goal.id}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
      lastSuccessfulSeq.current = Math.max(lastSuccessfulSeq.current, seq)
    } catch (e) {
      // Only roll back if this request is still the latest. If a newer PATCH
      // already succeeded (`seq < lastSuccessfulSeq.current`) or is in-flight
      // (`seq < patchSeq.current`), keep the current state — our older failure
      // no longer reflects the user's intent.
      if (seq === patchSeq.current && seq > lastSuccessfulSeq.current) {
        setGoal(prev)
        const msg = e instanceof Error ? e.message : String(e)
        setGoalSaveError(msg || t('settings.goal.save_failed'))
      }
    }
  }

  // OAuth initiation: XHR POST (so apiFetch attaches auth header) → receive
  // authorize URL → navigate browser. A plain <a href> would NOT send the
  // Bearer/initData header and hit a 401.
  //
  // `intervalsBusy` guards against double-click: a rapid second click while
  // the first POST is in flight would generate a second state JWT and show
  // two in-flight navigations competing for `window.location`.
  const startIntervalsOAuth = async () => {
    if (intervalsBusy) return
    setIntervalsBusy(true)
    try {
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(
        '/api/intervals/auth/init',
        { method: 'POST' },
      )
      window.location.assign(authorize_url)
    } catch (e) {
      const msg = e instanceof Error ? e.message : 'failed'
      setIntervalsToast({ kind: 'error', key: 'settings.intervals.toast_error' })
      setIntervalsBusy(false)
      console.error('Intervals OAuth init failed:', msg)
    }
  }

  const copyToClipboard = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text)
      setCopied(label)
      setTimeout(() => setCopied(null), 1500)
    } catch {
      // noop — user can select and Ctrl+C
    }
  }

  // Two separate snippets: display (masked until revealed) vs clipboard (always real).
  // Show/Hide on this page masks the displayed text so shoulder-surfers / screenshots
  // don't leak the token, but Copy still copies the real value so the config works.
  const mcpJsonSnippetDisplay = mcpConfig
    ? buildMcpJsonSnippet(mcpConfig.url, mcpRevealed ? mcpConfig.token : '•'.repeat(32))
    : ''
  const mcpJsonSnippetReal = mcpConfig ? buildMcpJsonSnippet(mcpConfig.url, mcpConfig.token) : ''

  const changeLanguage = (lng: string) => {
    i18n.changeLanguage(lng)
    apiFetch('/api/auth/language', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ language: lng }),
    }).catch(() => {})
  }

  return (
    <Layout title={t('settings.title')} maxWidth="480px">
      {/* Language */}
      <Section title={t('settings.language')} icon="🌐">
        <div className="flex gap-2">
          <button
            onClick={() => changeLanguage('ru')}
            className={`flex-1 py-2.5 rounded-xl text-sm font-semibold border cursor-pointer transition-colors font-sans ${
              i18n.language === 'ru'
                ? 'bg-accent text-white border-accent'
                : 'bg-surface border-border text-text hover:bg-surface-2'
            }`}
          >
            {t('settings.russian')}
          </button>
          <button
            onClick={() => changeLanguage('en')}
            className={`flex-1 py-2.5 rounded-xl text-sm font-semibold border cursor-pointer transition-colors font-sans ${
              i18n.language === 'en'
                ? 'bg-accent text-white border-accent'
                : 'bg-surface border-border text-text hover:bg-surface-2'
            }`}
          >
            {t('settings.english')}
          </button>
        </div>
      </Section>

      {/* Intervals.icu Connection */}
      {isAuthenticated && !isDemo && (
        <Section title={t('settings.intervals.title')} icon="🔗">
          {intervalsToast && (
            <div
              className={`text-[12px] mb-3 px-2 py-1.5 rounded-lg border ${
                intervalsToast.kind === 'success'
                  ? 'bg-green/10 border-green/30 text-green'
                  : 'bg-red/10 border-red/30 text-red'
              }`}
            >
              {t(intervalsToast.key)}
            </div>
          )}
          {!intervals && <p className="text-[13px] text-text-dim">{t('settings.intervals.loading')}</p>}
          {intervals && (!intervals.athlete_id || intervals.method === 'none') && botChatInitialized === false && (
            <div className="rounded-xl border border-amber-500/30 bg-amber-500/10 p-3">
              <p className="text-[13px] text-text mb-2 leading-snug font-semibold">
                {t('settings.intervals.start_bot_required_title')}
              </p>
              <p className="text-[12px] text-text-dim mb-3 leading-snug">
                {t('settings.intervals.start_bot_required_desc')}
              </p>
              {botUsername ? (
                <a
                  href={`https://t.me/${botUsername}?start=fromwidget`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-center gap-2 w-full py-2.5 bg-accent text-white rounded-xl text-sm font-semibold no-underline font-sans"
                >
                  {t('settings.intervals.start_bot_open')}
                </a>
              ) : (
                <p className="text-[12px] text-text-dim">{t('settings.intervals.start_bot_no_username')}</p>
              )}
            </div>
          )}
          {intervals && (!intervals.athlete_id || intervals.method === 'none') && botChatInitialized !== false && (
            <>
              <p className="text-[12px] text-text-dim mb-3 leading-snug">
                {t('settings.intervals.not_connected_desc')}
              </p>
              <button
                type="button"
                onClick={startIntervalsOAuth}
                disabled={intervalsBusy}
                className="flex items-center justify-center gap-2 w-full py-2.5 bg-accent text-white rounded-xl text-sm font-semibold border-none cursor-pointer font-sans disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {intervalsBusy && <span className="inline-block w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
                {intervalsBusy ? t('settings.intervals.redirecting') : t('settings.intervals.connect')}
              </button>
            </>
          )}
          {intervals && intervals.athlete_id && intervals.method === 'oauth' && (
            <>
              <div className="text-[13px] text-green mb-2">✅ {t('settings.intervals.connected')}</div>
              <Row label={t('settings.intervals.athlete')} value={intervals.athlete_id} />
              <Row label={t('settings.intervals.method')} value={t('settings.intervals.method_oauth')} />
              {intervals.scope && (
                <Row label={t('settings.intervals.scope')} value={intervals.scope} />
              )}
              <div className="mt-3">
                <BackfillSection />
              </div>
              <button
                type="button"
                onClick={async () => {
                  if (!confirm(t('settings.intervals.disconnect_confirm'))) return
                  try {
                    await apiFetch('/api/intervals/auth/disconnect', { method: 'POST' })
                    setIntervals(prev => prev ? { ...prev, method: 'none', scope: null } : prev)
                  } catch (e) {
                    console.error('Disconnect failed:', e)
                  }
                }}
                className="block w-full mt-3 py-2 bg-surface border border-red text-red text-center rounded-xl text-sm font-semibold cursor-pointer font-sans hover:bg-red/5"
              >
                {t('settings.intervals.disconnect')}
              </button>
            </>
          )}
          {intervals && intervals.athlete_id && intervals.method === 'api_key' && (
            <>
              <div className="text-[13px] text-text-dim mb-2">✅ {t('settings.intervals.connected_legacy')}</div>
              <Row label={t('settings.intervals.athlete')} value={intervals.athlete_id} />
              <Row label={t('settings.intervals.method')} value={t('settings.intervals.method_api_key')} />
              <button
                type="button"
                onClick={startIntervalsOAuth}
                disabled={intervalsBusy}
                className="flex items-center justify-center gap-2 w-full mt-3 py-2.5 bg-surface border border-accent text-accent rounded-xl text-sm font-semibold cursor-pointer font-sans disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {intervalsBusy && <span className="inline-block w-3.5 h-3.5 border-2 border-accent/30 border-t-accent rounded-full animate-spin" />}
                {intervalsBusy ? t('settings.intervals.redirecting') : t('settings.intervals.migrate_to_oauth')}
              </button>
            </>
          )}
        </Section>
      )}

      {/* Athlete Profile */}
      {profile && (
        <Section title="Athlete Profile" icon="🏊‍♂️">
          {profile.age && <Row label="Age" value={String(profile.age)} />}
          {profile.lthr_run && <Row label="LTHR Run" value={`${profile.lthr_run} bpm`} />}
          {profile.lthr_bike && <Row label="LTHR Bike" value={`${profile.lthr_bike} bpm`} />}
          {profile.ftp && <Row label="FTP" value={`${profile.ftp}W`} />}
          {profile.css && <Row label="CSS" value={`${Math.floor(Number(profile.css) / 60)}:${String(Math.round(Number(profile.css) % 60)).padStart(2, '0')}/100m`} />}
        </Section>
      )}

      {/* Race Goal */}
      {goal && (
        <Section title="Race Goal" icon="🏁">
          <Row label="Event" value={String(goal.event_name)} />
          <Row label="Date" value={String(goal.event_date)} />
          {!isDemo && goal.id && (
            <p className="text-[11px] text-text-dim mt-2 mb-1 leading-snug">
              {t('settings.goal.ctl_edit_hint_section')}
            </p>
          )}
          <EditableNumberRow
            label="CTL Target"
            value={goal.ctl_target ?? null}
            editHint={t('settings.goal.ctl_edit_hint')}
            disabled={isDemo || !goal.id}
            onCommit={next => patchGoal({ ctl_target: next })}
          />
          <EditableNumberRow
            label="Swim CTL"
            value={goal.per_sport_targets?.swim ?? null}
            editHint={t('settings.goal.ctl_edit_hint')}
            disabled={isDemo || !goal.id}
            onCommit={next => patchGoal({ per_sport_targets: { swim: next } })}
          />
          <EditableNumberRow
            label="Bike CTL"
            value={goal.per_sport_targets?.ride ?? null}
            editHint={t('settings.goal.ctl_edit_hint')}
            disabled={isDemo || !goal.id}
            onCommit={next => patchGoal({ per_sport_targets: { ride: next } })}
          />
          <EditableNumberRow
            label="Run CTL"
            value={goal.per_sport_targets?.run ?? null}
            editHint={t('settings.goal.ctl_edit_hint')}
            disabled={isDemo || !goal.id}
            onCommit={next => patchGoal({ per_sport_targets: { run: next } })}
          />
          {goalSaveError && (
            <p className="text-[12px] text-red mt-2">{goalSaveError}</p>
          )}
          <p className="text-[11px] text-text-dim mt-3 leading-snug">
            {t('settings.goal.edit_via_chat_hint')}
          </p>
        </Section>
      )}

      {/* MCP Connection */}
      {isAuthenticated && !isDemo && (
        <Section title={t('settings.mcp.title')} icon="🔌">
          {mcpError && <p className="text-[13px] text-red">{mcpError}</p>}
          {!mcpError && !mcpConfig && (
            <p className="text-[13px] text-text-dim">{t('settings.mcp.loading')}</p>
          )}
          {mcpConfig && (
            <>
              <p className="text-[12px] text-text-dim mb-3 leading-snug">
                {t('settings.mcp.description')}
              </p>

              <div className="mb-2">
                <div className="flex justify-between items-center mb-1">
                  <span className="text-[12px] text-text-dim">{t('settings.mcp.url_label')}</span>
                  <button
                    type="button"
                    onClick={() => copyToClipboard(mcpConfig.url, 'url')}
                    className="text-[11px] text-accent hover:underline cursor-pointer font-sans"
                  >
                    {copied === 'url' ? t('settings.mcp.copied') : t('settings.mcp.copy')}
                  </button>
                </div>
                <code className="block text-[11px] bg-surface-2 border border-border rounded-lg px-2 py-1.5 break-all font-mono">
                  {mcpConfig.url}
                </code>
              </div>

              <div className="mb-3">
                <div className="flex justify-between items-center mb-1">
                  <span className="text-[12px] text-text-dim">{t('settings.mcp.token_label')}</span>
                  <div className="flex gap-3">
                    <button
                      type="button"
                      onClick={() => setMcpRevealed(v => !v)}
                      className="text-[11px] text-accent hover:underline cursor-pointer font-sans"
                    >
                      {mcpRevealed ? t('settings.mcp.hide') : t('settings.mcp.show')}
                    </button>
                    <button
                      type="button"
                      onClick={() => copyToClipboard(mcpConfig.token, 'token')}
                      className="text-[11px] text-accent hover:underline cursor-pointer font-sans"
                    >
                      {copied === 'token' ? t('settings.mcp.copied') : t('settings.mcp.copy')}
                    </button>
                  </div>
                </div>
                <code className="block text-[11px] bg-surface-2 border border-border rounded-lg px-2 py-1.5 break-all font-mono">
                  {mcpRevealed ? mcpConfig.token : '•'.repeat(32)}
                </code>
              </div>

              <div>
                <div className="flex justify-between items-center mb-1">
                  <span className="text-[12px] text-text-dim">{t('settings.mcp.json_config')}</span>
                  <button
                    type="button"
                    onClick={() => copyToClipboard(mcpJsonSnippetReal, 'json')}
                    className="text-[11px] text-accent hover:underline cursor-pointer font-sans"
                  >
                    {copied === 'json' ? t('settings.mcp.copied') : t('settings.mcp.copy')}
                  </button>
                </div>
                <pre className="text-[10px] bg-surface-2 border border-border rounded-lg px-2 py-1.5 overflow-x-auto font-mono leading-tight whitespace-pre">{mcpJsonSnippetDisplay}</pre>
              </div>
            </>
          )}
        </Section>
      )}

      {/* Auth */}
      {isAuthenticated && (
        <div className="mt-6">
          <button
            onClick={logout}
            className="w-full py-3 bg-surface border border-border rounded-xl text-sm font-semibold text-red cursor-pointer hover:bg-surface-2 transition-colors font-sans"
          >
            {t('settings.logout')}
          </button>
        </div>
      )}
    </Layout>
  )
}

function Section({ title, icon, children }: { title: string; icon: string; children: React.ReactNode }) {
  return (
    <div className="bg-surface rounded-2xl p-4 mb-3">
      <div className="flex items-center gap-2 mb-3">
        <span className="text-lg">{icon}</span>
        <span className="text-sm font-bold">{title}</span>
      </div>
      {children}
    </div>
  )
}

function Row({ label, value }: { label: string; value: string }) {
  return (
    <div className="flex justify-between items-center py-1.5 border-b border-border last:border-b-0">
      <span className="text-[13px] text-text-dim">{label}</span>
      <span className="text-[13px] font-medium">{value}</span>
    </div>
  )
}

// Click-to-edit row for local-only numeric fields (CTL target, per-sport CTL).
// Optimistic commit: value is pushed upstream via `onCommit(next)`; on error
// the parent rolls back its state and renders an inline error message so the
// row re-mounts with the original value.
//
// Input constraints (must match server DTO bounds at api/dto.py:
// `ge=0, le=200`). Out-of-range or non-numeric input is caught in `commit()`
// BEFORE the PATCH fires — we keep the editor open and show an inline
// message so the user can correct without a round-trip + rollback flicker.
const EDITABLE_MIN = 0
const EDITABLE_MAX = 200

function EditableNumberRow({
  label,
  value,
  onCommit,
  editHint,
  disabled,
}: {
  label: string
  value: number | null | undefined
  onCommit: (next: number | null) => Promise<void>
  editHint: string
  disabled?: boolean
}) {
  const [editing, setEditing] = useState(false)
  const [draft, setDraft] = useState<string>(value != null ? String(value) : '')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!editing) {
      setDraft(value != null ? String(value) : '')
      setError(null)
    }
  }, [value, editing])

  const commit = async () => {
    const trimmed = draft.trim()
    const next = trimmed === '' ? null : Number(trimmed)

    if (next !== null && Number.isNaN(next)) {
      setError(`Enter a number between ${EDITABLE_MIN} and ${EDITABLE_MAX}.`)
      return
    }
    if (next !== null && (next < EDITABLE_MIN || next > EDITABLE_MAX)) {
      setError(`Value must be between ${EDITABLE_MIN} and ${EDITABLE_MAX}.`)
      return
    }
    if (next === value) {
      setEditing(false)
      setError(null)
      return
    }
    setBusy(true)
    try {
      await onCommit(next)
      setError(null)
      setEditing(false)
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="flex justify-between items-start py-1.5 border-b border-border last:border-b-0">
      <span className="text-[13px] text-text-dim">{label}</span>
      {editing ? (
        <div className="flex flex-col items-end">
          <input
            type="number"
            min={EDITABLE_MIN}
            max={EDITABLE_MAX}
            step={1}
            autoFocus
            value={draft}
            disabled={busy}
            onChange={e => {
              setDraft(e.target.value)
              if (error) setError(null)
            }}
            onBlur={commit}
            onKeyDown={e => {
              if (e.key === 'Enter') {
                ;(e.target as HTMLInputElement).blur()
              } else if (e.key === 'Escape') {
                setDraft(value != null ? String(value) : '')
                setError(null)
                setEditing(false)
              }
            }}
            aria-invalid={error ? true : undefined}
            className="text-[13px] font-medium text-right w-20 bg-transparent border border-border rounded px-1 focus:outline-none focus:border-primary"
          />
          {error && <span className="mt-1 text-[11px] text-red">{error}</span>}
        </div>
      ) : (
        <button
          type="button"
          disabled={disabled}
          title={editHint}
          aria-label={`${label}: ${value ?? '—'}. ${editHint}`}
          onClick={() => {
            setError(null)
            setEditing(true)
          }}
          className="group inline-flex items-center gap-1.5 min-h-[32px] px-1 -mx-1 text-[13px] font-medium text-text border-b border-dashed border-border hover:border-accent hover:text-accent cursor-pointer disabled:cursor-not-allowed disabled:border-transparent disabled:hover:text-text"
        >
          <span>{value != null ? String(value) : '—'}</span>
          <span aria-hidden="true" className="text-[11px] text-text-dim group-hover:text-accent">✏️</span>
        </button>
      )}
    </div>
  )
}
