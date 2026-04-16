import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Routes, Route, Navigate } from 'react-router-dom'
import { apiFetch } from './api/client'
import { getTelegramWebApp } from './auth/telegram'
import { useAuth } from './auth/useAuth'
import type { AuthMeResponse } from './api/types'
import OnboardingPrompt from './components/OnboardingPrompt'
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
  const [hasAthleteId, setHasAthleteId] = useState<boolean | null>(null)

  // Global auth gate: fetch /api/auth/me once on login → check if user has
  // a linked Intervals.icu athlete. If not, ALL data routes are replaced by
  // OnboardingPrompt. This prevents viewer-without-athlete from seeing
  // anyone else's data (issue #185). Settings and Login stay accessible
  // so the user can complete OAuth onboarding.
  useEffect(() => {
    if (!isAuthenticated) {
      setHasAthleteId(null)
      return
    }
    apiFetch<AuthMeResponse>('/api/auth/me')
      .then(data => {
        if (data.language && data.language !== i18n.language) {
          i18n.changeLanguage(data.language)
        }
        setHasAthleteId(!!data.intervals?.athlete_id)
      })
      .catch(() => setHasAthleteId(false))
  }, [isAuthenticated])

  // Authenticated but no athlete → only settings (for OAuth connect) and
  // onboarding prompt. No data pages, no dashboard, no navigation to data.
  const gated = isAuthenticated && hasAthleteId === false
  const DataRoute = gated ? OnboardingPrompt : undefined

  return (
    <Routes>
      <Route path="/" element={
        !isAuthenticated ? <Landing /> :
        gated ? <OnboardingPrompt /> :
        <Today />
      } />
      <Route path="/login" element={<Login />} />
      <Route path="/settings" element={<Settings />} />
      <Route path="/report" element={<Navigate to="/wellness" />} />
      <Route path="/wellness" element={DataRoute ? <DataRoute /> : <Wellness />} />
      <Route path="/plan" element={DataRoute ? <DataRoute /> : <Plan />} />
      <Route path="/activities" element={DataRoute ? <DataRoute /> : <Activities />} />
      <Route path="/activity/:id" element={DataRoute ? <DataRoute /> : <Activity />} />
      <Route path="/progress" element={DataRoute ? <DataRoute /> : <Progress />} />
      <Route path="/dashboard" element={DataRoute ? <DataRoute /> : <Dashboard />} />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
  )
}
