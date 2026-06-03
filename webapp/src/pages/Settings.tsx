import { useEffect, useRef, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link } from 'react-router-dom'
import Layout from '../components/Layout'
import { BottomSheet, Card, MicroLabel, TopBar } from '../components/halo'
import BackfillSection from '../components/BackfillSection'
import PersonalCard from '../components/PersonalCard'
import { useAuth } from '../auth/useAuth'
import { apiFetch, apiFetchBlob } from '../api/client'
import type {
  AthleteGoal,
  AthleteGoalsResponse,
  AuthMeResponse,
  IntervalsStatus,
  MeasuredThreshold,
  MeasuredThresholdsResponse,
  SportTag,
  SportType,
} from '../api/types'

// Race-goal sport_type enum — mirrors backend `data.sport_map.RACE_SPORT_TYPES`.
// Order chosen for the Settings dropdown: multi-sport first (most common race
// goals), then single sports, then catch-all.
const SPORT_TYPE_OPTIONS: SportType[] = [
  'triathlon',
  'duathlon',
  'aquathlon',
  'run',
  'ride',
  'swim',
  'fitness',
]

type McpConfig = { url: string; token: string }

type IntervalsToast = {
  kind: 'success' | 'error'
  key: string  // i18n key
}

// "Radik Khaziev" → "RK"; single token → first 2 chars; empty → "" so the
// caller falls back to the athlete-id monogram.
function nameInitials(name: string | null): string {
  const parts = (name ?? '').trim().split(/\s+/).filter(Boolean)
  if (parts.length === 0) return ''
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
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
  // Telegram identity of the authed user (own, not data-owner's). `role` is
  // the raw backend value ("athlete"/"owner"/"demo"/…) shown as-is, English,
  // not localized — by request.
  const [identity, setIdentity] = useState<{
    name: string | null
    username: string | null
    role: string | null
    // URL from /auth/me (e.g. "/api/auth/avatar") — pointer to authed endpoint,
    // NOT directly renderable as <img src> because <img> can't carry the
    // Bearer header. The blob-resolved URL lives in `avatarBlobUrl` below.
    avatarUrl: string | null
  }>({ name: null, username: null, role: null, avatarUrl: null })
  // Object URL of the fetched-as-blob avatar — actual <img src>. Null while
  // the fetch is in flight or if it fails (UI falls back to initials).
  const [avatarBlobUrl, setAvatarBlobUrl] = useState<string | null>(null)
  const [profile, setProfile] = useState<{
    age?: number | null
    lthr_run?: number | null
    lthr_bike?: number | null
    ftp?: number | null
    css?: number | null
    weight?: number | null
    vo2max?: number | null
    hr_max?: { run?: number | null; bike?: number | null; swim?: number | null } | null
  } | null>(null)
  // List of all active future goals — fetched separately from /api/athlete/goals
  // (#323 Strand C). `auth_me.goal` only carries the primary anchor for legacy
  // single-goal consumers (morning report); the Settings list view needs ALL
  // goals so the athlete can edit each one independently.
  const [goals, setGoals] = useState<AthleteGoal[]>([])
  const [goalSaveError, setGoalSaveError] = useState<string | null>(null)
  // Our-measured ramp-test thresholds (DFA α1) — separate fetch so the card
  // gracefully degrades to auto-synced-only when this request fails or returns
  // nothing (no ramp test yet). Never blocks the rest of Settings.
  const [measured, setMeasured] = useState<MeasuredThresholdsResponse | null>(null)
  const [sports, setSports] = useState<SportTag[] | null>(null)
  const [sportsSaveError, setSportsSaveError] = useState<string | null>(null)

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
    apiFetch<AuthMeResponse & { profile?: typeof profile }>('/api/auth/me')
      .then(data => {
        setIntervals(data.intervals ?? { athlete_id: null, scope: null, connected: false })
        // Default to true on missing field so old API responses don't lock
        // existing users out of the OAuth button — only an explicit `false`
        // from a fresh server triggers the /start gate.
        setBotChatInitialized(data.bot_chat_initialized ?? true)
        setBotUsername(data.bot_username ?? null)
        setIdentity({
          name: data.display_name ?? null,
          username: data.username ?? null,
          role: data.role ?? null,
          avatarUrl: data.avatar_url ?? null,
        })
        if (data.profile) setProfile(data.profile)
        // Defensive: only accept an actual array. Anything else (null,
        // undefined, "", number) collapses to null so the gate stays
        // closed — same hardening as App.tsx.
        setSports(Array.isArray(data.sports) ? data.sports : null)
      })
      .catch(() => {
        setIntervals({ athlete_id: null, scope: null, connected: false })
        setBotChatInitialized(true)
      })
  }, [isAuthenticated])

  // Goals list — separate fetch from /api/auth/me so the Settings page owns
  // its own state. Failure leaves `goals=[]`, which renders the empty state
  // («No active goals — use /race in the bot to add one»). Demo session falls
  // through to whatever the backend returns for the demo user (typically the
  // owner's goals, read-only).
  useEffect(() => {
    if (!isAuthenticated) return
    apiFetch<AthleteGoalsResponse>('/api/athlete/goals')
      .then(data => {
        if (Array.isArray(data.goals)) setGoals(data.goals)
      })
      .catch(() => {
        // Don't show a UI error — the empty list is itself a valid state for a
        // user with no goals yet. If this is a real failure, the next fetch
        // (page reload) will retry.
        setGoals([])
      })
  }, [isAuthenticated])

  // Our-measured thresholds — separate fetch. On failure / no tests, leave
  // `measured=null` so the Пороги card silently falls back to auto-synced-only
  // tiles (the DFA-α1 secondary line just doesn't appear).
  useEffect(() => {
    if (!isAuthenticated) return
    apiFetch<MeasuredThresholdsResponse>('/api/athlete/measured-thresholds')
      .then(setMeasured)
      .catch(() => setMeasured(null))
  }, [isAuthenticated])

  // Resolve `avatar_url` → object URL so the <img> can render. The endpoint
  // is Bearer-protected (avoiding /static/avatar/* enumeration), and <img
  // src=...> can't carry custom headers — only cookies — so we have to fetch
  // the bytes through `apiFetchBlob` and hand the <img> a blob URL. On any
  // failure (404 from missing file, 401 from expired session) we leave
  // avatarBlobUrl null and the UI shows the initials fallback.
  useEffect(() => {
    const url = identity.avatarUrl
    if (!url) {
      setAvatarBlobUrl(null)
      return
    }
    let cancelled = false
    let objUrl: string | null = null
    apiFetchBlob(url)
      .then(blob => {
        if (cancelled) return
        objUrl = URL.createObjectURL(blob)
        setAvatarBlobUrl(objUrl)
      })
      .catch(() => {
        // Permanent failures (404) and transient ones both fall back to
        // initials — there's nothing useful to retry from the page side.
        if (!cancelled) setAvatarBlobUrl(null)
      })
    return () => {
      cancelled = true
      if (objUrl) URL.revokeObjectURL(objUrl)
    }
  }, [identity.avatarUrl])

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
  // Same monotonic-seq pattern for the sports endpoint — rapid checkbox
  // toggles can have PUT₁ failing late while PUT₂ already succeeded; without
  // this guard the late rollback would clobber the successful newer state.
  const sportsPutSeq = useRef(0)
  const lastSuccessfulSportsSeq = useRef(0)

  // Push a local-only goal edit to the backend for one specific goal.
  // Applies optimistic update first; on failure rolls back and sets an inline
  // error message so the row re-mounts with the original value — provided the
  // failing request is still the latest (see patchSeq above).
  const patchGoal = async (
    goalId: number,
    patch: Partial<{
      ctl_target: number | null
      per_sport_targets: Record<string, number | null>
      sport_type: SportType
    }>,
  ) => {
    const seq = ++patchSeq.current
    const prev = goals
    setGoals(curr =>
      curr.map(g => {
        if (g.id !== goalId) return g
        return {
          ...g,
          ...(patch.ctl_target !== undefined ? { ctl_target: patch.ctl_target } : {}),
          ...(patch.sport_type !== undefined ? { sport_type: patch.sport_type } : {}),
          ...(patch.per_sport_targets !== undefined
            ? {
                per_sport_targets: {
                  ...(g.per_sport_targets ?? {}),
                  ...patch.per_sport_targets,
                },
              }
            : {}),
        }
      }),
    )
    setGoalSaveError(null)
    try {
      await apiFetch(`/api/athlete/goal/${goalId}`, {
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
        setGoals(prev)
        const msg = e instanceof Error ? e.message : String(e)
        setGoalSaveError(msg || t('settings.goal.save_failed'))
      }
    }
  }

  // Profile PATCH (age today). No monotonic-seq guard like patchGoal — concurrent
  // PATCHes on the same field from a human spam-editing one number are unlikely
  // enough we accept a rare visible/DB desync if request₁ fails *after* request₂
  // succeeds. Revisit if we add more profile fields here.
  const [profileSaveError, setProfileSaveError] = useState<string | null>(null)
  const patchProfile = async (patch: { age?: number | null }) => {
    const prev = profile
    setProfile(curr => (curr ? { ...curr, ...patch } : curr))
    setProfileSaveError(null)
    try {
      await apiFetch('/api/athlete/profile', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
    } catch (e) {
      setProfile(prev)
      const msg = e instanceof Error ? e.message : String(e)
      setProfileSaveError(msg || t('settings.profile.save_failed'))
    }
  }

  // Toggle one sport in the user's selection. Optimistic + monotonic-seq
  // rollback (mirrors patchGoal) — without the seq guard a late PUT₁ failure
  // could clobber a successful PUT₂ on rapid double-clicks. Empty selection
  // is blocked locally (server enforces ≥1 too, but doing it here avoids a
  // wasted round-trip + flicker).
  const toggleSport = async (tag: SportTag) => {
    if (isDemo) return
    const current = sports ?? []
    const next = current.includes(tag) ? current.filter(s => s !== tag) : [...current, tag]
    if (next.length === 0) {
      setSportsSaveError(t('settings.sports.empty_warning'))
      return
    }
    const seq = ++sportsPutSeq.current
    const prev = sports
    setSports(next)
    setSportsSaveError(null)
    try {
      const result = await apiFetch<{ sports: SportTag[] }>('/api/auth/sports', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ sports: next }),
      })
      lastSuccessfulSportsSeq.current = Math.max(lastSuccessfulSportsSeq.current, seq)
      // Only commit the server-canonicalised list if no newer PUT has
      // landed in between — otherwise we'd overwrite PUT₂'s payload with
      // PUT₁'s response.
      if (seq === sportsPutSeq.current) {
        setSports(result.sports)
        // Broadcast so App-level `sports` state (used by the gate + any
        // future feature-flag reads) doesn't go stale. App.tsx listens
        // for this event and mirrors the value. CustomEvent keeps
        // Settings ↔ App decoupled without lifting state into a context.
        window.dispatchEvent(new CustomEvent('sports-updated', { detail: result.sports }))
      }
    } catch (e) {
      // Same staleness check as patchGoal: only roll back if our request is
      // the latest one and no newer PUT has succeeded since.
      if (seq === sportsPutSeq.current && seq > lastSuccessfulSportsSeq.current) {
        setSports(prev)
        const msg = e instanceof Error ? e.message : String(e)
        setSportsSaveError(msg || t('settings.sports.save_failed'))
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

  // Layout / card composition mirrors the designer prototype
  // (design-package/endurai/direction-b-extras.jsx — "B · Settings"):
  // identity → Personal → Thresholds → Intervals → Goals → Sports →
  // Language → MCP → id footer. Every logic block (handlers, conditionals,
  // optimistic-rollback) is byte-identical to the pre-port version — only
  // the JSX shell + ordering changed. Sections the mock omits but the real
  // app needs (full OAuth flow, MCP token, logout) are preserved in the
  // same card style. See WEBAPP_HALO_REDESIGN_SPEC §F-Settings.

  // Extracted Language Card — rendered as the right tile of the Sports+Language
  // pair (stacked on mobile, 2fr/1fr on md+) when sports renders, or solo
  // full-width when sports is null.
  const languageCard = (
    <Card>
      <div className="flex items-center justify-between">
        <div>
          <div className="text-[14px] font-semibold text-halo-ink">Language</div>
          <div className="mt-0.5 text-[12px] text-halo-ink-dim">Coach voice and UI</div>
        </div>
        <div className="flex rounded-[10px] bg-halo-surface-2 p-[3px]">
          {(['EN', 'RU'] as const).map(l => {
            const code = l.toLowerCase()
            const on = i18n.language === code
            return (
              <button
                key={l}
                type="button"
                onClick={() => changeLanguage(code)}
                className={`rounded-lg px-3 py-1.5 text-[12px] font-semibold cursor-pointer border-none font-sans ${
                  on
                    ? 'bg-halo-surface text-halo-ink shadow-[0_1px_2px_rgba(0,0,0,0.06)]'
                    : 'bg-transparent text-halo-ink-dim'
                }`}
              >
                {l}
              </button>
            )
          })}
        </div>
      </div>
    </Card>
  )

  return (
    <Layout maxWidth="480px">
      <div className="-mx-4 -mt-4 md:-mb-8 min-h-screen bg-halo-bg px-4 md:px-9 pb-6 font-sans text-halo-ink">
      {/* Brand label — English-only by request (not localized to «Профиль»).
          Role eyebrow removed by request: it was redundant with the identity
          pill (resolves the double-role-pill nit in spec §F-Settings-port).
          Desktop subtitle is literal English too — consistent with the
          de-i18n'd Settings chrome the user requested. */}
      <TopBar title={t('nav.profile')} subtitle="Profile, goals, sports & integrations" />
      <div className="flex flex-col gap-3.5 md:mt-[18px]">

      {/* Identity — prototype's signature first card. Primary = Telegram
          display name (fallback @handle/@athlete-id/Profile); sub = @handle
          else raw role; pill = raw backend role, English, NOT localized
          ("athlete"/"owner"/"demo"…) — by request. Avatar = name initials,
          fallback to the athlete-id monogram. */}
      <Card>
        {/* Mobile (prototype `BSettings`): 56×56 avatar + 17px name. Desktop
            (`BdSettings` direction-b-desktop.jsx:1523): 64×64 + 20px name. */}
        <div className="flex items-center gap-3.5 md:gap-[18px]">
          {avatarBlobUrl ? (
            <img
              src={avatarBlobUrl}
              alt=""
              className="h-14 w-14 shrink-0 rounded-full object-cover md:h-16 md:w-16"
              // Race window: blob URL revoked between render and load. Drop
              // both URLs from state so the initials fallback renders next.
              onError={() => {
                setAvatarBlobUrl(null)
                setIdentity(prev => ({ ...prev, avatarUrl: null }))
              }}
            />
          ) : (
            <div
              aria-hidden="true"
              className="flex h-14 w-14 shrink-0 items-center justify-center rounded-full bg-gradient-to-br from-halo-brand to-halo-brand-dark text-[20px] font-semibold tracking-tight text-white md:h-16 md:w-16 md:text-[22px]"
            >
              {nameInitials(identity.name) || (intervals?.athlete_id ?? 'EN').slice(0, 2).toUpperCase()}
            </div>
          )}
          <div className="min-w-0 flex-1">
            <div className="truncate text-[17px] font-semibold tracking-tight text-halo-ink md:text-[20px]">
              {identity.name
                || (identity.username ? `@${identity.username}` : intervals?.athlete_id ? `@${intervals.athlete_id}` : 'Profile')}
            </div>
            {/* Age removed by request — it lives in the Personal panel.
                Sub = @handle when known, else raw role. */}
            <div className="mt-0.5 text-[13px] text-halo-ink-dim">
              {identity.username ? `@${identity.username}` : (identity.role ?? '')}
            </div>
          </div>
          {identity.role && (
            <span className="rounded-pill bg-halo-surface-2 px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide text-halo-brand">
              {identity.role}
            </span>
          )}
        </div>
      </Card>

      {/* Personal — Halo v2 re-spec (direction-b-personal-edit.jsx): BpRow
          layout + age stepper + source-provenance badges + batch-save footer.
          Backend-honest: only Age is writable (PATCH /api/athlete/profile);
          Weight (latest wellness sample) + per-sport HR-max (Intervals
          auto-sync) are read-only with provenance badges. The mock's
          Weight-manual-override and HR-max bottom-sheet (slider / source
          toggle / 90-day history) have NO backend (G1=B "read-only, no
          migration") and are intentionally not built. Logged in spec. */}
      {profile && (
        <Panel
          label="Personal"
          /* Halo-v3: "Edit" affordance on the Personal card (prototype
             `BdSettings` Profile «Редактировать», desktop.jsx:755) → the
             focused `/settings/personal/edit` page hosting the same
             PersonalCard. The inline editor here stays — both surfaces
             share the single component. */
          hint={
            <Link
              to="/settings/personal/edit"
              className="text-[12px] font-semibold text-halo-brand-dark no-underline"
            >
              Edit ›
            </Link>
          }
        >
          <PersonalCard
            age={profile.age ?? null}
            weight={profile.weight ?? null}
            hrMax={profile.hr_max ?? null}
            disabled={isDemo}
            saveError={profileSaveError}
            onSaveAge={next => patchProfile({ age: next })}
          />
        </Panel>
      )}

      {/* Пороги — dual-source thresholds (Halo · direction-b-extras). The
          Intervals value stays the headline number for every metric; for the
          three we can measure ourselves (FTP, LTHR·run, LTHR·bike — via DFA-α1
          ramp tests) a small secondary line surfaces OUR test value +
          confidence (R²) + date beneath it. CSS (no swim method) and VO₂max
          (composite — lives in Endurance Score) stay sync-only and render the
          muted fallback shape — also what a measured tile collapses to when no
          test exists or its confidence couldn't be graded. */}
      {profile && (profile.lthr_run || profile.lthr_bike || profile.ftp || profile.css || profile.vo2max) && (() => {
        const runRow = measured?.thresholds.find(m => m.sport === 'Run')
        const bikeRow = measured?.thresholds.find(m => m.sport === 'Ride')
        const lang = i18n.language
        const cssVal =
          profile.css != null
            ? `${Math.floor(Number(profile.css) / 60)}:${String(Math.round(Number(profile.css) % 60)).padStart(2, '0')}`
            : null
        // Each metric carries its Intervals headline + (where we measure it) our
        // DFA-α1 test line. Drop metrics whose Intervals value is missing.
        const metrics: { key: string; label: string; unit: string; sync: string; our: OurThreshold | null }[] = [
          profile.ftp != null && { key: 'ftp', label: t('settings.profile.ftp'), unit: 'W', sync: String(profile.ftp), our: ourLine(bikeRow, 'power', lang) },
          profile.lthr_run != null && { key: 'lthr_run', label: t('settings.profile.lthr_run'), unit: 'bpm', sync: String(profile.lthr_run), our: ourLine(runRow, 'hr', lang) },
          profile.lthr_bike != null && { key: 'lthr_bike', label: t('settings.profile.lthr_bike'), unit: 'bpm', sync: String(profile.lthr_bike), our: ourLine(bikeRow, 'hr', lang) },
          cssVal != null && { key: 'css', label: t('settings.profile.css'), unit: '/100m', sync: cssVal, our: null },
          profile.vo2max != null && { key: 'vo2max', label: 'VO₂max', unit: '', sync: String(profile.vo2max), our: null },
        ].filter(Boolean) as { key: string; label: string; unit: string; sync: string; our: OurThreshold | null }[]
        // Partition by whether OUR test exists: accent full-width tiles for
        // measured metrics, the recessed 2-col grid for everything else
        // (sync-only metrics + measurable ones with no trusted test yet).
        const measuredTiles = metrics
          .filter(m => m.our)
          .map(m => <ThreshTile key={m.key} label={m.label} unit={m.unit} sync={m.sync} our={m.our} t={t} />)
        const syncTiles = metrics
          .filter(m => !m.our)
          .map(m => <ThreshTile key={m.key} label={m.label} unit={m.unit} sync={m.sync} our={null} t={t} />)
        return (
          <Panel label={t('settings.profile.thresholds_title')}>
            {/* Legend — only when we actually have a measured tile to explain.
                With no ramp tests the card degrades to a plain sync-only grid. */}
            {measuredTiles.length > 0 && (
              <div className="mb-3 flex items-center gap-4">
                <span className="inline-flex items-center gap-1.5">
                  <span className="h-2 w-2 rounded-full bg-halo-brand" />
                  <span className="text-[11px] text-halo-ink-dim">{t('settings.profile.measured_legend')}</span>
                </span>
                <span className="inline-flex items-center gap-1.5">
                  <span className="box-border h-2 w-2 rounded-full border-[1.5px] border-halo-ink-dimmer" />
                  <span className="text-[11px] text-halo-ink-dim">{t('settings.profile.auto_synced')}</span>
                </span>
              </div>
            )}
            {/* Mobile: measured tiles stack full-width (the dual-source line
                needs the room). Desktop (md+, widened to 1180px beside the
                sidebar): 3-across grid, matching the desktop BdSettings card. */}
            {measuredTiles.length > 0 && (
              <div className="flex flex-col gap-2.5 md:grid md:grid-cols-3 md:gap-3.5">{measuredTiles}</div>
            )}
            {syncTiles.length > 0 && (
              <>
                {measuredTiles.length > 0 && (
                  <div className="my-3 flex items-center gap-2.5 md:my-4">
                    <span className="text-[10px] font-bold uppercase tracking-wide text-halo-ink-dimmer">
                      {t('settings.profile.sync_only')}
                    </span>
                    <span className="h-px flex-1 bg-halo-border" />
                  </div>
                )}
                <div className="grid grid-cols-2 gap-2.5 md:grid-cols-3 md:gap-3.5">{syncTiles}</div>
              </>
            )}
          </Panel>
        )
      })()}

      {/* Intervals.icu Connection — the prototype collapses this to a tiny
          "Live + Disconnect" card, but the real app needs the full
          connect / migrate / start-bot / backfill state machine. Logic
          kept verbatim; only the card shell is Halo-Panel now. */}
      {isAuthenticated && !isDemo && (
        <Panel label={t('settings.intervals.title')}>
          {intervalsToast && (
            <div
              className={`text-[12px] mb-3 px-2 py-1.5 rounded-chip border bg-halo-surface ${
                intervalsToast.kind === 'success'
                  ? 'border-halo-status-green text-halo-status-green'
                  : 'border-halo-coral text-halo-coral'
              }`}
            >
              {t(intervalsToast.key)}
            </div>
          )}
          {!intervals && <p className="text-[13px] text-halo-ink-dim">{t('settings.intervals.loading')}</p>}
          {intervals && (!intervals.athlete_id || !intervals.connected) && botChatInitialized === false && (
            <div className="rounded-chip border border-halo-amber bg-halo-surface-2 p-3">
              <p className="text-[13px] text-halo-ink mb-2 leading-snug font-semibold">
                {t('settings.intervals.start_bot_required_title')}
              </p>
              <p className="text-[12px] text-halo-ink-dim mb-3 leading-snug">
                {t('settings.intervals.start_bot_required_desc')}
              </p>
              {botUsername ? (
                <a
                  href={`https://t.me/${botUsername}?start=fromwidget`}
                  target="_blank"
                  rel="noopener noreferrer"
                  className="flex items-center justify-center gap-2 w-full py-2.5 bg-halo-brand text-white rounded-pill text-sm font-semibold no-underline font-sans"
                >
                  {t('settings.intervals.start_bot_open')}
                </a>
              ) : (
                <p className="text-[12px] text-halo-ink-dim">{t('settings.intervals.start_bot_no_username')}</p>
              )}
            </div>
          )}
          {intervals && (!intervals.athlete_id || !intervals.connected) && botChatInitialized !== false && (
            <>
              <p className="text-[12px] text-halo-ink-dim mb-3 leading-snug">
                {t('settings.intervals.not_connected_desc')}
              </p>
              <button
                type="button"
                onClick={startIntervalsOAuth}
                disabled={intervalsBusy}
                className="flex items-center justify-center gap-2 w-full py-2.5 bg-halo-brand text-white rounded-pill text-sm font-semibold border-none cursor-pointer font-sans disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {intervalsBusy && <span className="inline-block w-3.5 h-3.5 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
                {intervalsBusy ? t('settings.intervals.redirecting') : t('settings.intervals.connect')}
              </button>
            </>
          )}
          {intervals && intervals.athlete_id && intervals.connected && (
            <>
              {/* Prototype `BSettings` Intervals card header: name + "OAuth ·
                  <id>" sub on the left, brand-cobalt dot + "Live" on the
                  right (NOT green; top-right, not a standalone line). */}
              <div className="flex items-center justify-between">
                <div className="min-w-0">
                  <div className="text-[14px] font-semibold text-halo-ink">Intervals.icu</div>
                  <div className="mt-0.5 truncate text-[12px] text-halo-ink-dim">
                    OAuth · {intervals.athlete_id}
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-2">
                  <span
                    className="h-2 w-2 rounded-full bg-halo-brand"
                    style={{ boxShadow: '0 0 0 4px var(--color-brand-light)' }}
                  />
                  {/* "Live" — literal English, brand-dark, as in the mock. */}
                  <span className="text-[11px] font-bold uppercase tracking-[0.4px] text-halo-brand-dark">
                    Live
                  </span>
                </div>
              </div>
              {intervals.scope && (
                <div className="mt-2.5">
                  <div className="mb-1.5 text-[11px] text-halo-ink-dim">{t('settings.intervals.scope')}</div>
                  <div className="flex flex-wrap gap-1.5">
                    {intervals.scope.split(',').map(s => s.trim()).filter(Boolean).map(s => (
                      <span
                        key={s}
                        className="rounded-md bg-halo-surface-2 px-2 py-1 font-mono text-[11px] text-halo-ink-dim"
                      >
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              )}
              {/* Prototype: two subtle bordered buttons in a row (Sync /
                  Disconnect). Backfill keeps its own state-machine; styled to
                  match. */}
              <div className="mt-3.5 flex items-start gap-2">
                <div className="flex-1">
                  <BackfillSection />
                </div>
                <button
                  type="button"
                  onClick={async () => {
                    if (!confirm(t('settings.intervals.disconnect_confirm'))) return
                    try {
                      await apiFetch('/api/intervals/auth/disconnect', { method: 'POST' })
                      setIntervals(prev => prev ? { ...prev, connected: false } : prev)
                    } catch (e) {
                      console.error('Disconnect failed:', e)
                    }
                  }}
                  className="shrink-0 self-start rounded-[10px] border border-halo-border bg-halo-surface px-3.5 py-2 text-[13px] text-halo-ink-dim cursor-pointer font-sans hover:bg-halo-surface-2"
                >
                  {/* Literal English, as in the original mock — no i18n. */}
                  Disconnect
                </button>
              </div>
            </>
          )}
        </Panel>
      )}

      {/* Race goals — prototype `BSettings` Goals section (direction-b-extras
          .jsx :200–276): per-goal cards (Race-pill + RU priority label + date,
          name, "Тип" select, big editable CTL Target, per-sport CTL rows) +
          dashed "+ Add goal". Literal copy as the designer drew it (RU where
          RU in the mock), real data bound, `patchGoal` logic byte-identical.
          Deviations vs mock: no per-sport "current / progress bar" — the
          /api/athlete/goals payload carries only targets, not current CTL
          (data-honest); "+ Add goal" is a placeholder — goal creation is via
          the bot `/race` (no webapp endpoint). Logged in spec. */}
      {goals.length > 0 && (
        <div>
          <div className="flex items-baseline justify-between px-1 pb-2">
            <MicroLabel>Goals</MicroLabel>
            <span className="text-[11px] font-medium text-halo-ink-dim">
              Тыкни в значение, чтобы изменить
            </span>
          </div>
          <div className="flex flex-col gap-2">
            {goals.map(g => {
              const meta =
                g.category === 'RACE_A'
                  ? { tag: 'A', label: 'Главная', color: 'var(--color-coral)' }
                  : g.category === 'RACE_B'
                    ? { tag: 'B', label: 'Второй', color: 'var(--color-amber)' }
                    : { tag: 'C', label: 'Контрольный', color: 'var(--color-brand)' }
              const date = new Intl.DateTimeFormat(i18n.language, {
                weekday: 'short',
                month: 'short',
                day: 'numeric',
              }).format(new Date(`${g.event_date}T00:00:00`))
              return (
                <Card key={g.id} className="!p-3.5">
                  <div className="flex items-center gap-2">
                    <span
                      className="rounded-pill px-[7px] py-0.5 text-[10px] font-bold uppercase tracking-[0.4px]"
                      style={{ background: `color-mix(in srgb, ${meta.color} 12%, transparent)`, color: meta.color }}
                    >
                      Race {meta.tag}
                    </span>
                    <span className="text-[11px] font-semibold text-halo-brand-dark">{meta.label}</span>
                    <span className="flex-1" />
                    <span className="text-[11px] text-halo-ink-dim">{date}</span>
                  </div>
                  <div className="mt-2 text-[15px] font-semibold tracking-[-0.2px] text-halo-ink">
                    {g.event_name}
                  </div>

                  <div className="mt-3 flex items-center justify-between border-t border-halo-border pt-3">
                    <span className="text-[12px] text-halo-ink-dim">Тип</span>
                    {/* Native `<select>` рендерится системно (уродливый
                        дропдаун, не попадает в Halo). Заменено на кастомный
                        chip-style popover (`SportTypeSelect`) — клавиатура
                        работает (Enter/Esc), click-outside закрывает. */}
                    <SportTypeSelect
                      value={g.sport_type}
                      disabled={isDemo}
                      onChange={next => patchGoal(g.id, { sport_type: next })}
                      t={t}
                    />
                  </div>

                  <div className="mt-1">
                    <EditableNumberRow
                      label="CTL Target"
                      size="lg"
                      value={g.ctl_target ?? null}
                      editHint={t('settings.goal.ctl_edit_hint')}
                      disabled={isDemo}
                      onCommit={next => patchGoal(g.id, { ctl_target: next })}
                    />
                    <EditableNumberRow
                      label="Swim CTL"
                      value={g.per_sport_targets?.swim ?? null}
                      editHint={t('settings.goal.ctl_edit_hint')}
                      disabled={isDemo}
                      onCommit={next => patchGoal(g.id, { per_sport_targets: { swim: next } })}
                    />
                    <EditableNumberRow
                      label="Bike CTL"
                      value={g.per_sport_targets?.ride ?? null}
                      editHint={t('settings.goal.ctl_edit_hint')}
                      disabled={isDemo}
                      onCommit={next => patchGoal(g.id, { per_sport_targets: { ride: next } })}
                    />
                    <EditableNumberRow
                      label="Run CTL"
                      value={g.per_sport_targets?.run ?? null}
                      editHint={t('settings.goal.ctl_edit_hint')}
                      disabled={isDemo}
                      onCommit={next => patchGoal(g.id, { per_sport_targets: { run: next } })}
                    />
                  </div>
                </Card>
              )
            })}
            {goalSaveError && <p className="px-1 text-[12px] text-halo-coral">{goalSaveError}</p>}
            {/* Goal creation is via the bot `/race` — no webapp endpoint;
                reproduced per literal-copy as a placeholder (logged in spec). */}
            <button
              type="button"
              className="rounded-card border border-dashed border-halo-ink-dimmer bg-transparent py-3 text-[13px] font-semibold text-halo-ink-dim cursor-pointer font-sans"
            >
              + Add goal
            </button>
          </div>
        </div>
      )}

      {/* Sports — only visible after the user has been through the picker
          (sports != null). For demo we render the row read-only as visual
          confirmation that the gate is wired.

          Use ``sports !== null`` rather than truthiness so an accidental empty
          array (server enforces ≥1, but defense-in-depth: partial deploy or
          buggy response could slip through) still renders the section. With
          the looser ``sports &&`` gate the user could be locked into an
          unrecoverable empty state — the SportsPicker only shows when sports
          is null at the App level, not when it's []. */}
      {/* Sports + Language pair. Mobile: stacked. Desktop (`BdSettings`
          direction-b-desktop.jsx:1589): 2-col row (2fr/1fr). Sports renders
          only when the picker has been completed — when sports is null the
          Language card is solo (full-width on both viewports).
          Literal copy from the mock — by request these blocks are not i18n'd. */}
      {sports !== null ? (
        // `md:items-start` keeps Language tile compact (its segmented control
        // doesn't stretch to match Sports' height when `sportsSaveError`
        // expands the left tile). Design uses `alignItems:'stretch'` — diverging
        // here to preserve the segmented-control proportions.
        <div className="flex flex-col gap-3.5 md:grid md:grid-cols-[2fr_1fr] md:items-start md:gap-[18px]">
          <Panel label="Active sports">
            <div className="flex gap-2">
              {([['swim', 'Swim', 'var(--color-amber)'], ['ride', 'Ride', 'var(--color-brand)'], ['run', 'Run', 'var(--color-coral)']] as const).map(
                ([tag, label, c]) => {
                  const active = sports.includes(tag as SportTag)
                  return (
                    <button
                      key={tag}
                      type="button"
                      onClick={() => toggleSport(tag as SportTag)}
                      disabled={isDemo}
                      aria-pressed={active}
                      className="flex-1 rounded-chip py-2.5 text-center text-[13px] font-semibold cursor-pointer transition-colors font-sans disabled:cursor-not-allowed"
                      style={
                        active
                          ? { background: `color-mix(in srgb, ${c} 12%, transparent)`, color: c, border: `1.5px solid ${c}` }
                          : { background: 'var(--color-surface-2)', color: 'var(--color-ink-dimmer)', border: '1.5px dashed var(--color-ink-dimmer)' }
                      }
                    >
                      {label}
                    </button>
                  )
                },
              )}
            </div>
            {sportsSaveError && (
              <p className="text-[12px] text-halo-coral mt-2">{sportsSaveError}</p>
            )}
          </Panel>
          {languageCard}
        </div>
      ) : (
        languageCard
      )}

      {/* MCP — single config block, prototype `BSettings` (direction-b-extras
          .jsx :316–352). Literal copy, not i18n'd (by request). Copy/reveal
          logic + snippets preserved verbatim. */}
      {isAuthenticated && !isDemo && (
        <Card className="!p-0 overflow-hidden">
          <div className="px-[18px] pb-2.5 pt-4">
            <div className="flex items-baseline gap-2">
              <span className="text-[15px] font-semibold text-halo-ink">MCP подключение</span>
              <span className="rounded bg-halo-ink px-1.5 py-0.5 text-[9px] font-bold uppercase tracking-[0.4px] text-white">
                BETA
              </span>
              <span className="flex-1" />
              <span className="text-[11px] text-halo-ink-dim">read-only</span>
            </div>
            <div className="mt-1.5 text-[13px] leading-relaxed text-halo-ink-dim">
              Добавь в свой MCP-клиент (Claude Desktop, Cursor и т.д.) — Endurai раздаёт сервер сам, ничего ставить локально не нужно.
            </div>
          </div>

          {mcpError && <p className="px-[18px] pb-[18px] text-[13px] text-halo-coral">{mcpError}</p>}
          {!mcpError && !mcpConfig && (
            <p className="px-[18px] pb-[18px] text-[13px] text-halo-ink-dim">Загрузка…</p>
          )}
          {mcpConfig && (
            <div className="flex flex-col gap-3 px-[18px] pb-[18px]">
              <div
                className="relative rounded-xl px-3.5 pb-3 pt-3.5 font-mono text-[11px] leading-relaxed"
                style={{ background: 'var(--color-ink)', color: '#cce4d3' }}
              >
                <pre className="m-0 overflow-x-auto whitespace-pre">{mcpJsonSnippetDisplay}</pre>
                <button
                  type="button"
                  onClick={() => copyToClipboard(mcpJsonSnippetReal, 'json')}
                  className="absolute right-2.5 top-2.5 rounded-md border-none bg-white/10 px-2.5 py-1 text-[11px] font-semibold text-white cursor-pointer font-sans"
                >
                  {copied === 'json' ? 'Скопировано' : 'Копировать'}
                </button>
              </div>
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => copyToClipboard(mcpJsonSnippetReal, 'json')}
                  className="flex-1 rounded-[10px] border-none bg-halo-ink py-2.5 text-[13px] font-semibold text-white cursor-pointer font-sans"
                >
                  {copied === 'json' ? 'Скопировано' : 'Копировать JSON'}
                </button>
                <button
                  type="button"
                  onClick={() => setMcpRevealed(v => !v)}
                  className="rounded-[10px] border border-halo-border bg-halo-surface px-3.5 py-2.5 text-[13px] font-semibold text-halo-ink cursor-pointer font-sans hover:bg-halo-surface-2"
                >
                  {mcpRevealed ? 'Скрыть токен' : 'Показать токен'}
                </button>
              </div>
              <div className="text-[11px] leading-relaxed text-halo-ink-dim">
                Токен даёт полный доступ к твоим тренировкам. Никому не передавай — можно отозвать в любой момент.
              </div>
            </div>
          )}
        </Card>
      )}

      {/* Identity footer — prototype's centered dimmer id line. Only the
          Intervals athlete id is on this payload (no Telegram chat-id),
          so we show just that; omitted entirely when unconnected. */}
      {intervals?.athlete_id && (
        <div className="pt-1 text-center text-[11px] tracking-wide text-halo-ink-dimmer">
          {t('settings.identity.athlete_prefix')} {intervals.athlete_id}
        </div>
      )}

      {/* Auth — not in the prototype mock, but load-bearing. Kept as a quiet
          full-width action below the id footer. */}
      {isAuthenticated && (
        <button
          onClick={logout}
          className="mt-2 w-full py-3 bg-halo-surface border border-halo-border rounded-card text-sm font-semibold text-halo-coral cursor-pointer hover:bg-halo-surface-2 transition-colors font-sans"
        >
          {t('settings.logout')}
        </button>
      )}
      </div>
      </div>
    </Layout>
  )
}


// Halo card panel (replaces the legacy emoji `Section`). Prototype
// `direction-b-extras.jsx` uses an uppercase micro-label eyebrow with an
// optional right-aligned dim hint — no emoji icons. `Card` is the bxCard
// primitive (white surface, 20px radius, hairline border, soft shadow).
function Panel({
  label,
  hint,
  children,
}: {
  label: string
  hint?: React.ReactNode
  children: React.ReactNode
}) {
  return (
    <Card>
      <div className="flex items-baseline justify-between mb-3">
        <MicroLabel>{label}</MicroLabel>
        {hint != null && <span className="text-[11px] text-halo-ink-dim">{hint}</span>}
      </div>
      {children}
    </Card>
  )
}

// Sport-type picker for the Goal card «Тип» row. Trigger is a small lavender
// pill; tap opens a halo `<BottomSheet>` with the option list. iOS-style
// pattern (mobile-first, mirrors Telegram Mini App UX). Replaces the inline
// dropdown that read «не по дизайну» per user feedback 2026-05-23.
//
// Why sheet over inline dropdown: 7 sports is enough that an inline list
// crowds the goal card; a sheet decouples the picker's height from the
// caller's layout and gives proper full-width tap targets on touch screens.
function SportTypeSelect({
  value,
  disabled,
  onChange,
  t,
}: {
  value: SportType
  disabled: boolean
  onChange: (next: SportType) => void
  t: (k: string) => string
}) {
  const [open, setOpen] = useState(false)
  const pick = (opt: SportType) => {
    setOpen(false)
    if (opt !== value) onChange(opt)
  }
  return (
    <>
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen(true)}
        aria-haspopup="dialog"
        aria-expanded={open}
        className="inline-flex items-center gap-1.5 rounded-lg bg-halo-surface-2 px-2.5 py-1.5 text-[12px] font-semibold text-halo-ink cursor-pointer disabled:cursor-not-allowed disabled:opacity-60 font-sans"
      >
        {t(`settings.goal.sport_type_options.${value}`)}
        <span aria-hidden="true" className="text-[10px] text-halo-ink-dim">▾</span>
      </button>
      <BottomSheet
        open={open}
        onClose={() => setOpen(false)}
        title={t('settings.goal.sport_type_picker_title')}
      >
        <ul role="listbox" className="flex flex-col">
          {SPORT_TYPE_OPTIONS.map(opt => {
            const active = opt === value
            return (
              <li key={opt}>
                <button
                  type="button"
                  role="option"
                  aria-selected={active}
                  onClick={() => pick(opt)}
                  className={`flex w-full items-center justify-between gap-3 rounded-[10px] px-3 py-3 text-left text-[15px] font-medium font-sans cursor-pointer ${
                    active ? 'bg-halo-brand-light text-halo-brand-dark' : 'text-halo-ink hover:bg-halo-surface-2'
                  }`}
                >
                  <span>{t(`settings.goal.sport_type_options.${opt}`)}</span>
                  {active && (
                    <span aria-hidden="true" className="text-halo-brand-dark">✓</span>
                  )}
                </button>
              </li>
            )
          })}
        </ul>
      </BottomSheet>
    </>
  )
}

// ── Dual-source thresholds (Halo redesign · direction-b-extras BxThreshTile) ──
// Confidence = R² tier of the DFA-α1 ramp-test regression, the cross-card
// signal grading how much we trust OUR measurement. high → brand cobalt,
// medium → amber, low → coral. Keyed by the backend's hrvt2_confidence buckets.
type ConfTier = 'high' | 'medium' | 'low'
const CONF_CLASS: Record<ConfTier, { dot: string; text: string }> = {
  high: { dot: 'bg-halo-brand', text: 'text-halo-brand' },
  medium: { dot: 'bg-halo-amber', text: 'text-halo-amber' },
  low: { dot: 'bg-halo-coral', text: 'text-halo-coral' },
}

// ISO date → localized short form ("2026-05-12" → "12 мая" / "May 12").
function formatMeasuredDate(iso: string, lang: string): string {
  const d = new Date(`${iso}T00:00:00`)
  if (Number.isNaN(d.getTime())) return iso
  return d.toLocaleDateString(lang === 'ru' ? 'ru-RU' : 'en-US', { day: 'numeric', month: 'short' })
}

type OurThreshold = { value: string; conf: ConfTier; date: string }

// Build the "our test" secondary line from a measured row. Returns null (→
// tile renders the sync-only fallback) when no row, no value, or the
// confidence bucket is missing / untrusted.
function ourLine(row: MeasuredThreshold | undefined, kind: 'hr' | 'power', lang: string): OurThreshold | null {
  if (!row) return null
  const conf = row.hrvt2_confidence
  if (conf !== 'high' && conf !== 'medium' && conf !== 'low') return null
  const raw = kind === 'power' ? row.hrvt2_power : row.hrvt2_hr
  if (raw == null) return null
  return { value: String(Math.round(raw)), conf, date: formatMeasuredDate(row.measured_at, lang) }
}

// One threshold tile, two shapes sharing the component:
//  • measured (our != null) → faint cobalt wash + left accent bar. The
//    Intervals value stays the headline; OUR test value rides beneath it as a
//    small secondary line with a confidence dot (R² tier) + date.
//  • sync-only (our == null) → muted fallback on the recessed surface: just
//    the auto-synced number + an "авто-синк" tag. Also the graceful fallback
//    when no ramp test exists or its confidence couldn't be graded.
function ThreshTile({
  label,
  unit,
  sync,
  our,
  t,
}: {
  label: string
  unit: string
  sync: string
  our: OurThreshold | null
  t: (k: string) => string
}) {
  if (!our) {
    return (
      <div className="rounded-chip border border-halo-border bg-halo-surface-2 px-3 py-3 md:px-4 md:py-3.5">
        <div className="text-[11px] text-halo-ink-dim">{label}</div>
        <div className="mt-1 flex items-baseline gap-1">
          <span className="text-[20px] font-semibold tracking-tight text-halo-ink">{sync}</span>
          {unit && <span className="text-[11px] text-halo-ink-dimmer">{unit}</span>}
        </div>
        <div className="mt-1.5 flex items-center gap-1.5">
          <span className="box-border h-[7px] w-[7px] rounded-full border-[1.5px] border-halo-ink-dimmer" />
          <span className="text-[10px] font-semibold tracking-wide text-halo-ink-dimmer">
            {t('settings.profile.auto_synced')}
          </span>
        </div>
      </div>
    )
  }
  const c = CONF_CLASS[our.conf]
  return (
    <div
      className="rounded-chip border border-l-[3px] border-halo-border border-l-halo-brand px-3.5 py-3 md:px-4 md:py-3.5"
      style={{ background: 'color-mix(in srgb, var(--color-brand) 4.5%, transparent)' }}
    >
      <div className="text-[11px] text-halo-ink-dim">{label}</div>
      {/* Intervals value — the headline number. */}
      <div className="mt-1 flex items-baseline gap-1">
        <span className="text-[24px] font-bold tracking-tight text-halo-ink">{sync}</span>
        <span className="text-[12px] text-halo-ink-dim">{unit}</span>
        <span className="ml-0.5 text-[10px] font-semibold tracking-wide text-halo-ink-dimmer">
          {t('settings.profile.auto_synced')}
        </span>
      </div>
      {/* Our DFA-α1 test value — small secondary line, confidence by dot. */}
      <div className="mt-2 flex items-center gap-1.5 border-t border-dashed border-halo-border pt-2">
        <span className={`h-[7px] w-[7px] shrink-0 rounded-full ${c.dot}`} />
        <span className="text-[11px] text-halo-ink-dim">
          {t('settings.profile.measured_test')} <b className="font-bold text-halo-ink">{our.value}</b>
          <span className={`font-semibold ${c.text}`}> · {t(`settings.profile.confidence.${our.conf}`)}</span>
          <span className="text-halo-ink-dimmer"> · {our.date}</span>
        </span>
      </div>
    </div>
  )
}

// Click-to-edit row for numeric fields (CTL target, per-sport CTL, athlete age).
// Optimistic commit: value is pushed upstream via `onCommit(next)`; on error
// the parent rolls back its state and renders an inline error message so the
// row re-mounts with the original value.
//
// Input constraints come from the caller (must match the server DTO bounds in
// api/dto.py). Defaults 0/200 match the original CTL-target bounds for
// backward compatibility with existing callers. Out-of-range or non-numeric
// input is caught in `commit()` BEFORE the PATCH fires — we keep the editor
// open and show an inline message so the user can correct without a
// round-trip + rollback flicker.
function EditableNumberRow({
  label,
  value,
  onCommit,
  editHint,
  disabled,
  min = 0,
  max = 200,
  sub,
  size = 'sm',
  unit,
}: {
  label: string
  value: number | null | undefined
  onCommit: (next: number | null) => Promise<void>
  editHint: string
  disabled?: boolean
  min?: number
  max?: number
  // Optional prototype-fidelity extras (additive — existing callers omit them
  // and render byte-identically). `sub` = dim caption under the label;
  // `size='lg'` = big value (prototype Personal); `unit` = trailing unit.
  sub?: string
  size?: 'sm' | 'lg'
  unit?: string
}) {
  const { t } = useTranslation()
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
      setError(t('settings.editable_number.error_invalid', { min, max }))
      return
    }
    if (next !== null && (next < min || next > max)) {
      setError(t('settings.editable_number.error_out_of_range', { min, max }))
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
    <div className={`flex justify-between py-1.5 border-b border-halo-border last:border-b-0 ${sub != null ? 'items-center' : 'items-start'}`}>
      {sub != null ? (
        <div className="min-w-0">
          <div className="text-[13px] font-semibold text-halo-ink">{label}</div>
          <div className="mt-px text-[11px] text-halo-ink-dim">{sub}</div>
        </div>
      ) : (
        <span className="text-[13px] text-halo-ink-dim">{label}</span>
      )}
      {editing ? (
        <div className="flex flex-col items-end">
          <input
            type="number"
            min={min}
            max={max}
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
            className="text-[13px] font-medium text-right w-20 bg-transparent border border-halo-border rounded px-1 text-halo-ink focus:outline-none focus:border-halo-brand"
          />
          {error && <span className="mt-1 text-[11px] text-halo-coral">{error}</span>}
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
          className="group inline-flex items-center gap-1.5 min-h-[32px] px-1 -mx-1 text-[13px] font-medium text-halo-ink border-b border-dashed border-halo-ink-dimmer hover:border-halo-brand hover:text-halo-brand cursor-pointer disabled:cursor-not-allowed disabled:border-transparent disabled:hover:text-halo-ink"
        >
          <span className={size === 'lg' ? 'text-[18px] font-semibold tracking-[-0.4px]' : ''}>
            {value != null ? String(value) : '—'}
          </span>
          {unit && <span className="text-[11px] text-halo-ink-dimmer">{unit}</span>}
          <span aria-hidden="true" className="text-[11px] text-halo-ink-dim group-hover:text-halo-brand">✎</span>
        </button>
      )}
    </div>
  )
}
