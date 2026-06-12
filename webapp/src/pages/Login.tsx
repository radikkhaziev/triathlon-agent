import { useState, useEffect, useCallback, useRef, type FormEvent } from 'react'
import { useTranslation } from 'react-i18next'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import type { AuthRole } from '../auth/AuthProvider'
import { apiFetch } from '../api/client'
import { SegmentedCodeInput } from '../components/halo'
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

  // Public passwordless demo — one click mints a 24h read-only token
  // (docs/DEMO_PUBLIC_ACCESS_SPEC.md Phase 3, Option A).
  const handleDemo = async () => {
    setDemoSubmitting(true)
    setMessage('')
    try {
      const data = await apiFetch<{ token: string; role: string }>('/api/auth/demo', {
        method: 'POST',
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
  }

  // Halo BLogin: 3-zone column (brand / sign-in card / legal footer) over a
  // radial cobalt-light backdrop. Auth handlers above are byte-identical.
  return (
    <div
      className="flex min-h-screen flex-col justify-between font-sans text-halo-ink"
      style={{ background: 'radial-gradient(ellipse at top, var(--color-brand-light) 0%, var(--color-bg) 60%)' }}
    >
      {/* Brand */}
      <div className="px-6 pt-14 text-center">
        <img
          src="/endurai-icon.png"
          alt="Endurai"
          className="mx-auto block h-[88px] w-[88px] rounded-[22px]"
          style={{ boxShadow: '0 10px 30px rgba(10,13,24,0.18)' }}
        />
        <div className="mt-[18px] text-[34px] font-semibold tracking-[-1.2px]">Endurai</div>
        <div className="mx-auto mt-2 max-w-[280px] text-sm leading-relaxed text-halo-ink-dim">
          {t('login.brand_tagline')}
        </div>
      </div>

      {/* Sign-in card */}
      <div className="mx-auto w-full max-w-[400px] px-4 pt-5">
        <div className="rounded-[24px] border border-halo-border bg-halo-surface p-[18px] shadow-card">
          <div className="text-[11px] font-semibold uppercase tracking-[0.6px] text-halo-ink-dim">
            {t('login.sign_in')}
          </div>

          {botUsername && <div ref={widgetContainerRef} className="mt-3 flex justify-center" />}

          <div className="my-3.5 flex items-center gap-2.5 text-halo-ink-dimmer">
            <div className="h-px flex-1 bg-halo-border" />
            <span className="text-[11px] font-semibold uppercase tracking-[0.6px]">{t('login.divider_or')}</span>
            <div className="h-px flex-1 bg-halo-border" />
          </div>

          <form onSubmit={handleSubmit}>
            <div className="mb-1.5 text-xs font-semibold text-halo-ink-dim">{t('login.have_code')}</div>
            <SegmentedCodeInput
              value={code}
              onChange={v => {
                setCode(v)
                setMessage('')
                setMsgType('')
              }}
              length={6}
              ariaLabel={t('login.enter_code')}
            />
            <button
              type="submit"
              disabled={submitting}
              className="mt-3 w-full rounded-chip border-none bg-halo-ink py-3 text-sm font-semibold text-white disabled:opacity-50"
            >
              {submitting ? t('login.checking') : t('login.verify_code')}
            </button>
          </form>

          {message && (
            <div className={`mt-3 text-center text-sm ${msgType === 'error' ? 'text-halo-coral' : 'text-halo-status-green'}`}>
              {message}
            </div>
          )}
          {alreadyAuth && (
            <div className="mt-3 text-center text-[13px] text-halo-ink-dim">
              {t('login.already_auth')}{' '}
              <a href="/" className="text-halo-brand no-underline">{t('login.go_home')}</a>
            </div>
          )}
        </div>

        <button
          type="button"
          onClick={handleDemo}
          disabled={demoSubmitting}
          className="mt-3.5 w-full border-none bg-transparent py-3 text-[13px] font-semibold text-halo-ink-dim disabled:opacity-50"
        >
          {demoSubmitting ? t('login.demo_checking') : t('login.try_demo')}
        </button>
      </div>

      {/* Legal footer */}
      <div className="px-6 pb-7 pt-[18px] text-center text-[11px] leading-relaxed tracking-[0.3px] text-halo-ink-dimmer">
        {t('login.legal_1')}
        <br />
        {t('login.legal_2')}
      </div>
    </div>
  )
}
