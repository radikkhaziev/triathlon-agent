import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import Layout from '../components/Layout'
import { useAuth } from '../auth/useAuth'
import { apiFetch } from '../api/client'

type McpConfig = { url: string; token: string }

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

  useEffect(() => {
    if (!isAuthenticated) return
    apiFetch<McpConfig>('/api/auth/mcp-config')
      .then(setMcpConfig)
      .catch((e: Error) => setMcpError(e.message || 'Failed to load MCP config'))
  }, [isAuthenticated])

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

      {/* AI Workouts */}
      <Section title="AI Workouts" icon="🤖">
        <Row label="Auto-generate" value="Coming soon" />
        <Row label="Auto-push to Garmin" value="Coming soon" />
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
