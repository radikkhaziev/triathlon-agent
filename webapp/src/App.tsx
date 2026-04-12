import { useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Routes, Route, Navigate } from 'react-router-dom'
import { apiFetch } from './api/client'
import { getTelegramWebApp } from './auth/telegram'
import { useAuth } from './auth/useAuth'
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

  useEffect(() => {
    if (!isAuthenticated) return
    apiFetch<{ language?: string }>('/api/auth/me')
      .then(data => {
        if (data.language && data.language !== i18n.language) {
          i18n.changeLanguage(data.language)
        }
      })
      .catch(() => {})
  }, [isAuthenticated])

  return (
    <Routes>
      <Route path="/" element={isAuthenticated ? <Today /> : <Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/report" element={<Navigate to="/wellness" />} />
      <Route path="/wellness" element={<Wellness />} />
      <Route path="/plan" element={<Plan />} />
      <Route path="/activities" element={<Activities />} />
      <Route path="/activity/:id" element={<Activity />} />
      <Route path="/progress" element={<Progress />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="/settings" element={<Settings />} />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
  )
}
