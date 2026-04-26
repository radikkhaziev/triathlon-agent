import { useState } from 'react'
import { useTranslation } from 'react-i18next'
import Layout from './Layout'
import { apiFetch, ApiError } from '../api/client'

/**
 * Empty state for authenticated users without a connected Intervals.icu
 * account. Shown on Today (and anywhere else that needs athlete data) while
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
        <div className="flex flex-col items-center text-center px-6 py-12">
          <div aria-hidden="true" className="text-5xl mb-4">💬</div>
          <h1 className="text-xl font-bold mb-3">{t('onboarding.start_bot_title')}</h1>
          <p className="text-sm text-text-dim leading-relaxed mb-8 max-w-[320px]">
            {t('onboarding.start_bot_description')}
          </p>
          {href ? (
            <a
              href={href}
              target="_blank"
              rel="noopener noreferrer"
              className="flex items-center justify-center gap-2 w-full max-w-[320px] py-3.5 bg-accent text-white text-center rounded-xl text-[15px] font-semibold no-underline font-sans"
            >
              {t('onboarding.start_bot_cta')}
            </a>
          ) : (
            <p className="text-[12px] text-text-dim">{t('onboarding.start_bot_no_username')}</p>
          )}
          <p className="text-[11px] text-text-dim mt-6 max-w-[320px] leading-snug">
            {t('onboarding.start_bot_after_hint')}
          </p>
        </div>
      </Layout>
    )
  }

  return (
    <Layout maxWidth="480px">
      <div className="flex flex-col items-center text-center px-6 py-12">
        <div aria-hidden="true" className="text-5xl mb-4">🏊‍♂️ 🚴 🏃</div>
        <h1 className="text-xl font-bold mb-3">{t('onboarding.title')}</h1>
        <p className="text-sm text-text-dim leading-relaxed mb-8 max-w-[320px]">
          {t('onboarding.description')}
        </p>
        <button
          type="button"
          onClick={startOAuth}
          disabled={busy}
          className="flex items-center justify-center gap-2 w-full max-w-[320px] py-3.5 bg-accent text-white text-center rounded-xl text-[15px] font-semibold border-none cursor-pointer font-sans disabled:opacity-60 disabled:cursor-not-allowed"
        >
          {busy && <span className="inline-block w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />}
          {busy ? t('onboarding.redirecting') : t('onboarding.cta')}
        </button>
        {error && (
          <p className="text-[12px] text-red mt-3 max-w-[320px]">
            {t('onboarding.error')}
          </p>
        )}
        <p className="text-[11px] text-text-dim mt-6 max-w-[320px] leading-snug">
          {t('onboarding.no_account_hint')}{' '}
          <a
            href="https://intervals.icu"
            target="_blank"
            rel="noopener noreferrer"
            className="text-accent no-underline"
          >
            intervals.icu
          </a>
        </p>
      </div>
    </Layout>
  )
}
