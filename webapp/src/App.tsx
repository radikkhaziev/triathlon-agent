import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Routes, Route, Navigate } from 'react-router-dom'
import { apiFetch } from './api/client'
import { getTelegramWebApp } from './auth/telegram'
import { useAuth } from './auth/useAuth'
import type { AuthMeResponse, SportTag } from './api/types'
import LoadingSpinner from './components/LoadingSpinner'
import Layout from './components/Layout'
import OnboardingPrompt from './components/OnboardingPrompt'
import SportsPicker from './components/SportsPicker'
import BotChatBanner from './components/BotChatBanner'
import Landing from './pages/Landing'
import Login from './pages/Login'
import Wellness from './pages/Wellness'
import Coach from './pages/Coach'
import MetricDetail from './pages/MetricDetail'
import RecoveryTrend from './pages/RecoveryTrend'
import SleepTrend from './pages/SleepTrend'
import BodyTrend from './pages/BodyTrend'
import LoadDetail from './pages/LoadDetail'
import EnduranceDetail from './pages/EnduranceDetail'
import WellnessHistory from './pages/WellnessHistory'
import PersonalEdit from './pages/PersonalEdit'
import PlanScreen from './pages/PlanScreen'
import ScheduledWorkout from './pages/ScheduledWorkout'
import Activity from './pages/Activity'
import Dashboard from './pages/Dashboard'
import Settings from './pages/Settings'
import WeeklyReport from './pages/WeeklyReport'
import WeeklyReports from './pages/WeeklyReports'

export default function App() {
  useEffect(() => {
    const tg = getTelegramWebApp()
    if (tg) {
      tg.ready()
      tg.expand()
    }
  }, [])

  const { isAuthenticated } = useAuth()
  const { i18n } = useTranslation()
  // 'checking' = fetch in flight, 'yes' = has athlete, 'no' = needs onboarding
  const [athleteState, setAthleteState] = useState<'checking' | 'yes' | 'no'>('checking')
  // Sports gate (USER_SPORTS_SPEC §6): null = athlete hasn't picked yet →
  // show <SportsPicker/> after they finish Intervals OAuth. Empty array
  // never reaches the frontend (server enforces ≥1 entry).
  const [sports, setSports] = useState<SportTag[] | null | 'checking'>('checking')
  // Issue #266: a Login Widget signup never opened a bot chat, so notifications
  // would 400. Banner stays visible across every page until /start unsticks it.
  const [botChatInitialized, setBotChatInitialized] = useState<boolean | null>(null)
  const [botUsername, setBotUsername] = useState<string | null>(null)

  // Global auth gate: fetch /api/auth/me once on login → check if user has
  // a linked Intervals.icu athlete. If not, data routes are replaced by
  // OnboardingPrompt. This prevents viewer-without-athlete from seeing
  // anyone else's data (issue #185). Settings and Login stay accessible so
  // the user can complete OAuth onboarding.
  //
  // While the check is in flight ('checking'), data routes show a loading
  // spinner — no window where unauthenticated data can flash (C1 fix).
  // On /api/auth/me failure we stay in 'checking' and show spinner instead
  // of falsely gating to onboarding (C3 fix — transient error ≠ no athlete).
  useEffect(() => {
    if (!isAuthenticated) {
      setAthleteState('checking')
      setSports('checking')
      return
    }
    apiFetch<AuthMeResponse>('/api/auth/me')
      .then(data => {
        if (data.language && data.language !== i18n.language) {
          i18n.changeLanguage(data.language)
        }
        setAthleteState(data.intervals?.athlete_id ? 'yes' : 'no')
        // Field-presence guard: `sports` may be missing on old API
        // responses (partial deploy). When the key is absent, assume
        // already-set so we don't lock existing users out. When present,
        // require an actual array — defends against a buggy/malicious
        // server returning ``sports: ""`` or ``sports: 0``, which would
        // both be `=== null`-comparable but not `Array.isArray`-true,
        // and silently bypass the gate.
        setSports(
          'sports' in data
            ? Array.isArray(data.sports) ? data.sports : null
            : (['swim', 'ride', 'run'] as SportTag[]),
        )
        // Default to true so an old server (no field) doesn't show a bogus
        // banner; only an explicit ``false`` from a fresh API triggers it.
        setBotChatInitialized(data.bot_chat_initialized ?? true)
        setBotUsername(data.bot_username ?? null)
      })
      .catch(() => {
        // Network/server error — don't gate to onboarding, keep spinner.
        // User can reload; if it persists, the data endpoints will also
        // fail and show their own error states.
        setAthleteState('checking')
        setSports('checking')
        setBotChatInitialized(true)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated])

  // Listen for Settings-page sports updates so the App-level state mirrors
  // the latest selection without a full /api/auth/me refetch. Settings
  // dispatches `sports-updated` after a successful PUT.
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail
      if (Array.isArray(detail)) setSports(detail as SportTag[])
    }
    window.addEventListener('sports-updated', handler)
    return () => window.removeEventListener('sports-updated', handler)
  }, [])

  // Decide what to show for data routes based on athlete state.
  // Order: still loading → spinner; no Intervals → OnboardingPrompt;
  // no sports picked → SportsPicker; everything ready → page.
  const showLoading =
    isAuthenticated && (athleteState === 'checking' || sports === 'checking')
  const gated = isAuthenticated && athleteState === 'no'
  const needsSports = isAuthenticated && athleteState === 'yes' && sports === null

  const LoadingPage = () => <Layout maxWidth="480px"><LoadingSpinner /></Layout>

  // Helper: pick the right element for a data route.
  const dataRoute = (Page: React.ComponentType) =>
    showLoading ? <LoadingPage /> :
    gated ? <OnboardingPrompt /> :
    needsSports ? <SportsPicker onSaved={setSports} /> :
    <Page />

  const showBotChatBanner = isAuthenticated && botChatInitialized === false

  return (
    <>
      {showBotChatBanner && <BotChatBanner botUsername={botUsername} />}
    <Routes>
      <Route path="/" element={
        !isAuthenticated ? <Landing /> :
        <Navigate to="/wellness" replace />
      } />
      <Route path="/login" element={<Login />} />
      <Route path="/settings" element={<Settings />} />
      <Route path="/settings/personal/edit" element={<PersonalEdit />} />
      <Route path="/report" element={<Navigate to="/wellness" />} />
      {/* Halo-v3 tab rename (2026-05-23): `/plan` → `/calendar`, `/dashboard`
          → `/trends`. Legacy paths preserved as redirects so Telegram WebApp
          kbd buttons (e.g. race-plan PR4 button), bookmarks, and any in-flight
          morning-report links keep working. ``replace`` keeps history clean. */}
      <Route path="/plan" element={<Navigate to="/calendar" replace />} />
      <Route path="/dashboard" element={<Navigate to="/trends" replace />} />
      <Route path="/wellness" element={dataRoute(Wellness)} />
      <Route path="/wellness/recovery" element={dataRoute(RecoveryTrend)} />
      <Route path="/wellness/sleep" element={dataRoute(SleepTrend)} />
      <Route path="/wellness/body" element={dataRoute(BodyTrend)} />
      <Route path="/wellness/load" element={dataRoute(LoadDetail)} />
      <Route path="/wellness/endurance" element={dataRoute(EnduranceDetail)} />
      <Route path="/wellness/history" element={dataRoute(WellnessHistory)} />
      <Route path="/wellness/:metric" element={dataRoute(MetricDetail)} />
      <Route path="/coach" element={dataRoute(Coach)} />
      <Route path="/calendar" element={dataRoute(PlanScreen)} />
      <Route path="/workout/:id" element={dataRoute(ScheduledWorkout)} />
      {/* `/activities` list-route retired 2026-05-23 — Week tab now hosts the
          weekly activities (merged with plan). Detail-route `/activity/:id`
          remains as a deep-link (from Week day-cards, morning report, etc.). */}
      <Route path="/activities" element={<Navigate to="/calendar" replace />} />
      <Route path="/activity/:id" element={dataRoute(Activity)} />
      <Route path="/trends" element={dataRoute(Dashboard)} />
      <Route path="/weekly" element={dataRoute(WeeklyReports)} />
      <Route path="/weekly/:weekStart" element={dataRoute(WeeklyReport)} />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
    </>
  )
}
