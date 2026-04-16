import { useState, useEffect, useCallback, useRef, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import type { AuthRole } from '../auth/AuthProvider'
import { apiFetch } from '../api/client'
import EnduraiLogo from '../components/EnduraiLogo'
import type { AuthMeResponse } from '../api/types'

interface TelegramUser {
  id: number
  first_name: string
  last_name?: string
  username?: string
  photo_url?: string
  auth_date: number
  hash: string
}

declare global {
  interface Window {
    onTelegramAuth?: (user: TelegramUser) => void
  }
}

export default function Login() {
  const { t } = useTranslation()
  const { isAuthenticated, setJwt } = useAuth()
  const navigate = useNavigate()
  const [code, setCode] = useState('')
  const [message, setMessage] = useState('')
  const [msgType, setMsgType] = useState<'error' | 'success' | ''>('')
  const [submitting, setSubmitting] = useState(false)
  const [alreadyAuth, setAlreadyAuth] = useState(false)
  const [botUsername, setBotUsername] = useState<string | null>(null)
  const widgetContainerRef = useRef<HTMLDivElement>(null)
  const [demoPassword, setDemoPassword] = useState('')
  const [demoSubmitting, setDemoSubmitting] = useState(false)

  useEffect(() => {
    if (isAuthenticated) {
      apiFetch<AuthMeResponse>('/api/auth/me')
        .then(data => {
          if (data.authenticated) setAlreadyAuth(true)
        })
        .catch(() => {})
    }
  }, [isAuthenticated])

  useEffect(() => {
    apiFetch<{ bot_username?: string }>('/api/auth/telegram-widget-config')
      .then(data => {
        if (data.bot_username) setBotUsername(data.bot_username)
      })
      .catch(() => {})
  }, [])

  // After a successful login, route new users (no athlete_id) straight to
  // Settings so they immediately see the "Подключить Intervals.icu" section.
  // Existing athletes go to the main dashboard as before. Called from both
  // the Telegram widget callback and the one-time code form.
  const routeAfterLogin = useCallback(async () => {
    try {
      const me = await apiFetch<AuthMeResponse>('/api/auth/me')
      const needsOnboarding = !me.intervals?.athlete_id
      navigate(needsOnboarding ? '/settings' : '/')
    } catch {
      navigate('/')  // fall back to home on any auth/me failure
    }
  }, [navigate])

  useEffect(() => {
    window.onTelegramAuth = async (user: TelegramUser) => {
      setSubmitting(true)
      setMessage('')
      try {
        const data = await apiFetch<{ token: string; role: string }>('/api/auth/telegram-widget', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(user),
        })
        setJwt(data.token, data.role as AuthRole)
        setMessage(t('login.success'))
        setMsgType('success')
        setTimeout(routeAfterLogin, 500)
      } catch (err) {
        setMessage(err instanceof Error ? err.message : t('common.error'))
        setMsgType('error')
      } finally {
        setSubmitting(false)
      }
    }
    return () => {
      delete window.onTelegramAuth
    }
  }, [setJwt, t, routeAfterLogin])

  useEffect(() => {
    if (!botUsername || !widgetContainerRef.current) return
    const container = widgetContainerRef.current
    container.innerHTML = ''
    const script = document.createElement('script')
    script.async = true
    script.src = 'https://telegram.org/js/telegram-widget.js?22'
    script.setAttribute('data-telegram-login', botUsername)
    script.setAttribute('data-size', 'large')
    script.setAttribute('data-radius', '12')
    script.setAttribute('data-onauth', 'onTelegramAuth(user)')
    container.appendChild(script)
    return () => {
      container.innerHTML = ''
    }
  }, [botUsername])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (code.length !== 6) {
      setMessage(t('login.enter_code'))
      setMsgType('error')
      return
    }

    setSubmitting(true)
    setMessage('')

    try {
      const data = await apiFetch<{ token: string; role: string }>('/api/auth/verify-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
      })
      setJwt(data.token, data.role as AuthRole)
      setMessage(t('login.success'))
      setMsgType('success')
      setTimeout(routeAfterLogin, 500)
    } catch (err) {
      setMessage(err instanceof Error ? err.message : t('common.error'))
      setMsgType('error')
      setCode('')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="bg-surface border border-border rounded-2xl px-8 py-10 max-w-[400px] w-full text-center">
        <div className="flex justify-center mb-6">
          <EnduraiLogo height={56} />
        </div>
        <p className="text-text-dim text-sm mb-6 leading-relaxed">
          {t('login.instructions')}
        </p>

        {botUsername && (
          <div className="mb-6">
            <div ref={widgetContainerRef} className="flex justify-center" />
          </div>
        )}

        <details className="text-left">
          <summary className="text-text-dim text-sm cursor-pointer mb-4 select-none hover:text-text transition">
            {t('login.or_use_code')}
          </summary>
          <form onSubmit={handleSubmit}>
            <input
              type="text"
              value={code}
              onChange={e => {
                setCode(e.target.value.replace(/\D/g, '').slice(0, 6))
                setMessage('')
                setMsgType('')
              }}
              maxLength={6}
              inputMode="numeric"
              placeholder="000000"
              className="w-full py-3.5 px-4 text-2xl font-semibold tracking-[8px] text-center bg-surface-2 border border-border rounded-xl text-text outline-none transition-colors focus:border-accent placeholder:tracking-[4px] placeholder:text-base placeholder:font-normal placeholder:text-text-dim"
            />
            <button
              type="submit"
              disabled={submitting}
              className="w-full py-3.5 mt-4 text-[15px] font-semibold bg-accent text-white border-none rounded-xl cursor-pointer transition-opacity hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed font-sans"
            >
              {submitting ? t('login.checking') : t('login.submit')}
            </button>
          </form>
          <div className="mt-6 pt-4 border-t border-border">
            <p className="text-text-dim text-[13px] mb-2">{t('login.step1')}</p>
            <p className="text-text-dim text-[13px] mb-2">{t('login.step2')}</p>
            <p className="text-text-dim text-[13px] mb-2">{t('login.step3')}</p>
            <p className="text-text-dim text-[13px]">{t('login.code_expires')}</p>
          </div>
        </details>

        <div className="mt-6 pt-4 border-t border-border">
          <p className="text-text-dim text-sm mb-3">{t('login.demo_title')}</p>
          <form onSubmit={async (e) => {
            e.preventDefault()
            if (!demoPassword.trim()) return
            setDemoSubmitting(true)
            setMessage('')
            try {
              const data = await apiFetch<{ token: string; role: string }>('/api/auth/demo', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ password: demoPassword }),
              })
              setJwt(data.token, data.role as 'demo')
              setMessage(t('login.success'))
              setMsgType('success')
              setTimeout(() => navigate('/'), 500)
            } catch (err) {
              setMessage(err instanceof Error ? err.message : t('common.error'))
              setMsgType('error')
            } finally {
              setDemoSubmitting(false)
            }
          }} className="flex gap-2">
            <input
              type="password"
              value={demoPassword}
              onChange={e => setDemoPassword(e.target.value)}
              placeholder={t('login.demo_placeholder')}
              className="flex-1 py-2.5 px-3 text-sm bg-surface-2 border border-border rounded-lg text-text outline-none focus:border-accent font-sans"
            />
            <button
              type="submit"
              disabled={demoSubmitting}
              className="py-2.5 px-4 text-sm font-semibold bg-surface-2 border border-border text-text rounded-lg cursor-pointer hover:bg-border disabled:opacity-50 font-sans"
            >
              {demoSubmitting ? t('login.demo_checking') : t('login.demo_submit')}
            </button>
          </form>
        </div>

        {message && (
          <div className={`mt-4 text-sm ${msgType === 'error' ? 'text-red' : 'text-green'}`}>
            {message}
          </div>
        )}

        {alreadyAuth && (
          <div className="mt-4 text-[13px] text-text-dim">
            {t('login.already_auth')}{' '}
            <a href="/" className="text-accent no-underline">{t('login.go_home')}</a>
          </div>
        )}

      </div>
    </div>
  )
}
