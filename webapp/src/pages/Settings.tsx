import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Layout from '../components/Layout'
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
  const { logout, isAuthenticated } = useAuth()
  const [mcpConfig, setMcpConfig] = useState<McpConfig | null>(null)
  const [mcpError, setMcpError] = useState<string | null>(null)
  const [mcpRevealed, setMcpRevealed] = useState(false)
  const [copied, setCopied] = useState<string | null>(null)
  const [intervals, setIntervals] = useState<IntervalsStatus | null>(null)
  const [intervalsToast, setIntervalsToast] = useState<IntervalsToast | null>(null)
  const [intervalsBusy, setIntervalsBusy] = useState(false)

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
    apiFetch<AuthMeResponse>('/api/auth/me')
      .then(data => {
        setIntervals(data.intervals ?? { method: 'none', athlete_id: null, scope: null })
      })
      .catch(() => {
        setIntervals({ method: 'none', athlete_id: null, scope: null })
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
      {isAuthenticated && (
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
          {intervals && (!intervals.athlete_id || intervals.method === 'none') && (
            <>
              <p className="text-[12px] text-text-dim mb-3 leading-snug">
                {t('settings.intervals.not_connected_desc')}
              </p>
              <button
                type="button"
                onClick={startIntervalsOAuth}
                disabled={intervalsBusy}
                className="block w-full py-2.5 bg-accent text-white text-center rounded-xl text-sm font-semibold border-none cursor-pointer font-sans disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {t('settings.intervals.connect')}
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
              <p className="text-[11px] text-text-dim mt-3 leading-snug">
                {t('settings.intervals.disconnect_soon')}
              </p>
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
                className="block w-full mt-3 py-2.5 bg-surface border border-accent text-accent text-center rounded-xl text-sm font-semibold cursor-pointer font-sans disabled:opacity-60 disabled:cursor-not-allowed"
              >
                {t('settings.intervals.migrate_to_oauth')}
              </button>
            </>
          )}
        </Section>
      )}

      {/* Athlete Profile */}
      <Section title="Athlete Profile" icon="🏊‍♂️">
        <Row label="Age" value="43" />
        <Row label="LTHR Run" value="153 bpm" />
        <Row label="LTHR Bike" value="153 bpm" />
        <Row label="FTP" value="233W" />
        <Row label="CSS" value="2:21/100m" />
      </Section>

      {/* Race Goal */}
      <Section title="Race Goal" icon="🏁">
        <Row label="Event" value="Ironman 70.3" />
        <Row label="Date" value="Sep 15, 2026" />
        <Row label="CTL Target" value="75" />
        <Row label="Swim CTL" value="15" />
        <Row label="Bike CTL" value="35" />
        <Row label="Run CTL" value="25" />
      </Section>

      {/* MCP Connection */}
      {isAuthenticated && (
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
