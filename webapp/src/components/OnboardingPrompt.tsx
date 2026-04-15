import { useTranslation } from 'react-i18next'
import Layout from './Layout'

/**
 * Empty state for authenticated users without a connected Intervals.icu
 * account. Shown on Today (and anywhere else that needs athlete data) while
 * the user hasn't finished onboarding.
 *
 * The big CTA is a plain `<a href>` to the OAuth initiation endpoint — browser
 * handles the 302 redirect to Intervals.icu. We deliberately do NOT use
 * `apiFetch` here because OAuth is a full-page navigation, not an XHR.
 */
export default function OnboardingPrompt() {
  const { t } = useTranslation()
  return (
    <Layout maxWidth="480px">
      <div className="flex flex-col items-center text-center px-6 py-12">
        <div aria-hidden="true" className="text-5xl mb-4">🏊‍♂️ 🚴 🏃</div>
        <h1 className="text-xl font-bold mb-3">{t('onboarding.title')}</h1>
        <p className="text-sm text-text-dim leading-relaxed mb-8 max-w-[320px]">
          {t('onboarding.description')}
        </p>
        <a
          href="/api/intervals/auth/connect"
          className="block w-full max-w-[320px] py-3.5 bg-accent text-white text-center rounded-xl text-[15px] font-semibold no-underline font-sans"
        >
          {t('onboarding.cta')}
        </a>
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
