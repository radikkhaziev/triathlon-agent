import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Link, Navigate } from 'react-router-dom'
import Layout from '../components/Layout'
import LoadingSpinner from '../components/LoadingSpinner'
import ErrorMessage from '../components/ErrorMessage'
import PersonalCard from '../components/PersonalCard'
import { useAuth } from '../auth/useAuth'
import { apiFetch } from '../api/client'
import type { AuthMeResponse } from '../api/types'

type Profile = {
  age?: number | null
  lthr_run?: number | null
  lthr_bike?: number | null
  ftp?: number | null
  css?: number | null
  weight?: number | null
  vo2max?: number | null
  hr_max?: { run?: number | null; bike?: number | null; swim?: number | null } | null
}

/**
 * `/settings/personal/edit` — focused page for the Personal card (Halo-v3
 * prototype `Редактировать` affordance, `direction-b-personal-edit.jsx`).
 * Renders the same `<PersonalCard/>` Settings uses (single source of truth);
 * keeps its own fetch + optimistic-with-rollback PATCH so it works standalone
 * if the user lands here from a deep link or browser refresh.
 *
 * The prototype's manual-override / popover-slider / 90d-history flow is
 * backend-blocked and deferred — see SPEC §10.4 deferred story #4 (G1=B
 * precedent). Today only Age is writable; Weight + per-sport HR-max stay
 * read-only with `BpSource` provenance.
 */
export default function PersonalEdit() {
  // The H1/back-chevron text + the `Edit ›` link на Settings — все три
  // English-литеральные, согласно §10.3 «Settings chrome literal-EN»
  // директиве пользователя; не локализуется намеренно (не i18n-бага).
  const { t } = useTranslation()
  const { isAuthenticated, isDemo } = useAuth()

  const [profile, setProfile] = useState<Profile | null>(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [saveError, setSaveError] = useState<string | null>(null)

  // Focused page = no graceful unauth shell (unlike Settings). Bounce anon
  // deep-links straight to /login instead of leaving an infinite spinner.
  useEffect(() => {
    if (!isAuthenticated) return
    apiFetch<AuthMeResponse & { profile?: Profile }>('/api/auth/me')
      .then(data => setProfile(data.profile ?? null))
      .catch(e => setLoadError(e instanceof Error ? e.message : String(e)))
      .finally(() => setLoading(false))
  }, [isAuthenticated])

  if (!isAuthenticated) return <Navigate to="/login" replace />

  // Optimistic update + rollback — mirrors Settings.patchProfile so the two
  // surfaces behave identically (a save here is bit-for-bit equivalent).
  const patchProfile = async (patch: { age?: number | null }) => {
    const prev = profile
    setProfile(curr => (curr ? { ...curr, ...patch } : curr))
    setSaveError(null)
    try {
      await apiFetch('/api/athlete/profile', {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(patch),
      })
    } catch (e) {
      setProfile(prev)
      const msg = e instanceof Error ? e.message : String(e)
      setSaveError(msg || t('settings.profile.save_failed'))
    }
  }

  return (
    <Layout maxWidth="480px">
      <div className="-mx-4 -mt-4 md:-mb-8 min-h-screen bg-halo-bg px-4 md:px-9 font-sans text-halo-ink">
        <header className="flex items-center px-1 pt-[18px] pb-2.5">
          <Link
            to="/settings"
            className="inline-flex items-center gap-1.5 py-1.5 pl-1 pr-2.5 text-sm font-medium text-halo-ink-dim no-underline"
          >
            <span className="text-lg leading-none">‹</span> Settings
          </Link>
        </header>

        <div className="px-1 pb-2">
          <h1 className="m-0 text-[22px] font-semibold tracking-[-0.3px] text-halo-ink">Personal</h1>
          <p className="mt-1 text-[13px] text-halo-ink-dim">
            {t('personal_edit.subtitle')}
          </p>
        </div>

        {loading && <LoadingSpinner />}
        {loadError && <ErrorMessage message={loadError} />}

        {!loading && !loadError && profile && (
          <div className="mt-3 rounded-card border border-halo-border bg-halo-surface p-5 shadow-card">
            <PersonalCard
              age={profile.age ?? null}
              weight={profile.weight ?? null}
              hrMax={profile.hr_max ?? null}
              disabled={isDemo}
              saveError={saveError}
              onSaveAge={next => patchProfile({ age: next })}
            />
          </div>
        )}

        {!loading && !loadError && !profile && (
          <div className="px-1 py-8 text-[14px] text-halo-ink-dim">{t('personal_edit.no_profile')}</div>
        )}
      </div>
    </Layout>
  )
}
