import { useEffect } from 'react'
import { Routes, Route, Navigate } from 'react-router-dom'
import { getTelegramWebApp } from './auth/telegram'
import Landing from './pages/Landing'
import Login from './pages/Login'
import Report from './pages/Report'
import Wellness from './pages/Wellness'
import Plan from './pages/Plan'
import Activities from './pages/Activities'
import Activity from './pages/Activity'
import Dashboard from './pages/Dashboard'

export default function App() {
  useEffect(() => {
    const tg = getTelegramWebApp()
    if (tg) {
      tg.ready()
      tg.expand()
    }
  }, [])

  return (
    <Routes>
      <Route path="/" element={<Landing />} />
      <Route path="/login" element={<Login />} />
      <Route path="/report" element={<Report />} />
      <Route path="/wellness" element={<Wellness />} />
      <Route path="/plan" element={<Plan />} />
      <Route path="/activities" element={<Activities />} />
      <Route path="/activity/:id" element={<Activity />} />
      <Route path="/dashboard" element={<Dashboard />} />
      <Route path="*" element={<Navigate to="/" />} />
    </Routes>
  )
}
