import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Layout from './Layout'
import { apiFetch, ApiError } from '../api/client'

/**
 * Empty state for authenticated users without a connected Intervals.icu
 * account. Shown on data routes (Wellness, Plan, Activities, …) while
 * the user hasn't finished onboarding.
 *
 * The CTA triggers an XHR POST to `/api/intervals/auth/init` (so `apiFetch`
 * attaches the Authorization header) and then navigates the browser to the
 * returned `authorize_url`. A plain `<a href>` would NOT send the bearer /
 * initData header and hit a 401.
 */
export default function OnboardingPrompt() {
  const { t } = useTranslation()
  const [error, setError] = useState(false)
  const [busy, setBusy] = useState(false)
  // 412 from /api/intervals/auth/init means the user has no bot chat yet
  // (Login Widget signup, never pressed /start). We swap the OAuth CTA for
  // a "open the bot" deep link until they fix it.
  const [needsBotStart, setNeedsBotStart] = useState<{ bot_username: string | null } | null>(null)

  const startOAuth = async () => {
    setError(false)
    setBusy(true)
    try {
      const { authorize_url } = await apiFetch<{ authorize_url: string }>(
        '/api/intervals/auth/init',
        { method: 'POST' },
      )
      window.location.assign(authorize_url)
    } catch (e) {
      if (e instanceof ApiError && e.status === 412) {
        const d = e.detail as { error?: string; bot_username?: string | null } | null
        if (d?.error === 'bot_chat_not_initialized') {
          setNeedsBotStart({ bot_username: d.bot_username ?? null })
          setBusy(false)
          return
        }
      }
      console.error('Intervals OAuth init failed:', e)
      setError(true)
      setBusy(false)
    }
  }

  if (needsBotStart) {
    const href = needsBotStart.bot_username
      ? `https://t.me/${needsBotStart.bot_username}?start=fromwidget`
      : null
    return (
      <Layout maxWidth="480px">
        <div className="-mx-4 -mt-4 md:-mb-8 min-h-screen bg-halo-bg px-4 font-sans text-halo-ink">
        <div className="flex flex-col items-center text-center px-6 py-12">
          <div aria-hidden="true" className="text-5xl mb-4">💬</div>
          <h1 className="text-2xl font-semibold tracking-tight mb-3 text-halo-ink">{t('onboarding.start_bot_title')}</h1>
          <p className="text-sm text-halo-ink-dim leading-relaxed mb-8 max-w-[320px]">
            {t('onboarding.start_bot_description')}
          </p>
          {href ? (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center justify-center gap-2 w-full max-w-[320px] py-3.5 bg-halo-ink text-white text-center rounded-chip text-[15px] font-semibold no-underline font-sans"
            >
              {t('onboarding.start_bot_cta')}
            </a>
          ) : (
            <p className="text-[12px] text-halo-ink-dim">{t('onboarding.start_bot_no_username')}</p>
          )}
          <p className="text-[11px] text-halo-ink-dim mt-6 max-w-[320px] leading-snug">
            {t('onboarding.start_bot_after_hint')}
          </p>
        </div>
        </div>
      </Layout>
    )
  }

  // Prototype `BIntervalsConnect` (direction-b-extras.jsx :726-805): step
  // indicator, service card + scope checklist, privacy note, Connect CTA.
  // `startOAuth`/`busy`/`error`/`needsBotStart` logic unchanged. The mock's
  // "try demo mode" sub-CTA is intentionally dropped — the user is already
  // authed (not demo); "try demo" would be a misleading dead action here.
  const scopes: [string, string][] = [
    ['activity:write', t('onboarding.scope_activity')],
    ['wellness:read', t('onboarding.scope_wellness')],
    ['calendar:write', t('onboarding.scope_calendar')],
    ['settings:write', t('onboarding.scope_settings')],
  ]
  return (
    <Layout maxWidth="480px">
      <div
        className="-mx-4 -mt-4 md:-mb-8 flex min-h-screen flex-col bg-halo-bg px-4 font-sans text-halo-ink"
        style={{ background: 'radial-gradient(ellipse at top, var(--color-brand-light) 0%, var(--color-bg) 60%)' }}
      >
        <div className="flex items-center justify-between px-1 pt-[18px]">
          <div className="text-[11px] font-bold uppercase tracking-[0.6px] text-halo-brand-dark">
            {t('onboarding.step')}
          </div>
          <div className="flex gap-1">
            {[1, 2, 3].map(i => (
              <div
                key={i}
                className={`h-1 rounded-sm ${i === 2 ? 'w-[22px] bg-halo-brand' : i < 2 ? 'w-2.5 bg-halo-brand' : 'w-2.5 bg-halo-surface-2'}`}
              />
            ))}
          </div>
        </div>

        <div className="px-5 pb-3 pt-6">
          <h1 className="text-[26px] font-semibold leading-tight tracking-[-0.6px] text-halo-ink">
            {t('onboarding.connect_title')}
          </h1>
          <p className="mt-2.5 text-sm leading-relaxed text-halo-ink-dim">{t('onboarding.connect_intro')}</p>
        </div>

        <div className="flex flex-col gap-3 px-4">
          <div className="rounded-card border border-halo-border bg-halo-surface p-[18px] shadow-card">
            <div className="flex items-center gap-3.5">
              <span
                className="flex h-12 w-12 shrink-0 items-center justify-center rounded-chip text-[22px] font-bold tracking-[-0.5px] text-white"
                style={{ background: '#f97316' }}
              >
                i.
              </span>
              <div className="min-w-0 flex-1">
                <div className="text-[15px] font-semibold text-halo-ink">Intervals.icu</div>
                <div className="mt-0.5 text-[12px] text-halo-ink-dim">{t('onboarding.service_sub')}</div>
              </div>
              <span className="rounded-pill bg-halo-surface-2 px-2.5 py-1 text-[10px] font-bold uppercase tracking-[0.4px] text-halo-ink-dim">
                {t('onboarding.not_connected')}
              </span>
            </div>
            <div className="mt-4 border-t border-halo-border pt-3.5">
              <div className="mb-2.5 text-[11px] font-bold uppercase tracking-[0.5px] text-halo-ink-dim">
                {t('onboarding.scope_title')}
              </div>
              {scopes.map(([code, desc]) => (
                <div key={code} className="flex items-start gap-2.5 py-1.5">
                  <span className="shrink-0 text-sm leading-snug text-halo-brand">✓</span>
                  <div className="min-w-0 flex-1">
                    <div className="text-[13px] font-semibold text-halo-ink">{desc}</div>
                    <code className="font-mono text-[10px] tracking-[0.2px] text-halo-ink-dimmer">{code}</code>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="flex items-start gap-2.5 rounded-card border border-dashed border-halo-border p-3.5 text-[12px] leading-relaxed text-halo-ink-dim">
            <span aria-hidden="true" className="text-sm">🔒</span>
            <span>{t('onboarding.privacy')}</span>
          </div>
        </div>

        <div className="mt-auto flex flex-col gap-2.5 px-4 pb-6 pt-5">
          <button
            type="button"
            onClick={startOAuth}
            disabled={busy}
            className="flex items-center justify-center gap-2.5 rounded-card border-none bg-halo-ink py-3.5 text-[15px] font-semibold text-white cursor-pointer font-sans disabled:cursor-not-allowed disabled:opacity-60"
          >
            {busy ? (
              <span className="inline-block h-4 w-4 animate-spin rounded-full border-2 border-white/30 border-t-white" />
            ) : (
              <span
                className="inline-flex h-[18px] w-[18px] items-center justify-center rounded text-[11px] font-bold text-white"
                style={{ background: '#f97316' }}
              >
                i.
              </span>
            )}
            {busy ? t('onboarding.redirecting') : t('onboarding.cta')}
          </button>
          {error && <p className="text-center text-[12px] text-halo-coral">{t('onboarding.error')}</p>}
          <div className="text-center text-[11px] text-halo-ink-dimmer">
            {t('onboarding.signup_prefix')}{' '}
            <a
              href="https://intervals.icu"
              target="_blank"
              rel="noopener noreferrer"
              className="font-semibold text-halo-brand-dark no-underline"
            >
              {t('onboarding.signup_link')}
            </a>
          </div>
        </div>
      </div>
    </Layout>
  )
}
