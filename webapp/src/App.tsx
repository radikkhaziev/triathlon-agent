import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Routes, Route, Navigate } from 'react-router-dom'
import { apiFetch } from './api/client'
import { getTelegramWebApp } from './auth/telegram'
import { useAuth } from './auth/useAuth'
import type { AuthMeResponse } from './api/types'
import LoadingSpinner from './components/LoadingSpinner'
import Layout from './components/Layout'
import OnboardingPrompt from './components/OnboardingPrompt'
import BotChatBanner from './components/BotChatBanner'
import Landing from './pages/Landing'
import Login from './pages/Login'
import Today from './pages/Today'
import Wellness from './pages/Wellness'
import Plan from './pages/Plan'
import Activities from './pages/Activities'
import Activity from './pages/Activity'
import Dashboard from './pages/Dashboard'
import Settings from './pages/Settings'
import Progress from './pages/Progress'

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
  // Issue #266: a Login Widget signup never opened a bot chat, so notifications
  // would 400. Banner stays visible across every page until /start unsticks it.
  const [botChatInitialized, setBotChatInitialized] = useState<boolean | null>(null)
  const [botUsername, setBotUsername] = useState<string | null>(null)

  // Global auth gate: fetch /api/auth/me once on login → check if user has
  // a linked Intervals.icu athlete. If not, ALL data routes are replaced by
  // OnboardingPrompt. This prevents viewer-without-athlete from seeing
  // anyone else's data (issue #185). Settings and Login stay accessible
  // so the user can complete OAuth onboarding.
  //
  // While the check is in flight ('checking'), data routes show a loading
  // spinner — no window where unauthenticated data can flash (C1 fix).
  // On /api/auth/me failure we stay in 'checking' and show spinner instead
  // of falsely gating to onboarding (C3 fix — transient error ≠ no athlete).
  useEffect(() => {
    if (!isAuthenticated) {
      setAthleteState('checking')
      return
    }
    apiFetch<AuthMeResponse>('/api/auth/me')
      .then(data => {
        if (data.language && data.language !== i18n.language) {
          i18n.changeLanguage(data.language)
        }
        setAthleteState(data.intervals?.athlete_id ? 'yes' : 'no')
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
        setBotChatInitialized(true)
      })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [isAuthenticated])

  // Decide what to show for data routes based on athlete state.
  const showLoading = isAuthenticated && athleteState === 'checking'
  const gated = isAuthenticated && athleteState === 'no'

  const LoadingPage = () => <Layout maxWidth="480px"><LoadingSpinner /></Layout>

  // Helper: pick the right element for a data route.
  const dataRoute = (Page: React.ComponentType) =>
    showLoading ? <LoadingPage /> :
    gated ? <OnboardingPrompt /> :
    <Page />

  const showBotChatBanner = isAuthenticated && botChatInitialized === false

  return (
    <>
      {showBotChatBanner && <BotChatBanner botUsername={botUsername} />}
    <Routes>
      <Route path="/" element={
        !isAuthenticated ? <Landing /> :
        showLoading ? <LoadingPage /> :
        gated ? <OnboardingPrompt /> :
        <Today />
      } />
      <Route path="/login" element={<Login />} />
      <Route path="/settings" element={<Settings />} />
      <Route path="/report" element={<Navigate to="/wellness" />} />
      <Route path="/wellness" element={dataRoute(Wellness)} />
      <Route path="/plan" element={dataRoute(Plan)} />
      <Route path="/activities" element={dataRoute(Activities)} />
      <Route path="/activity/:id" element={dataRoute(Activity)} />
      <Route path="/progress" element={dataRoute(Progress)} />
      <Route path="/dashboard" element={dataRoute(Dashboard)} />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
    </>
  )
}
