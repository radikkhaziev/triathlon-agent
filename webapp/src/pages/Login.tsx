import { useState, useEffect, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../auth/useAuth'
import { apiFetch } from '../api/client'
import type { AuthMeResponse } from '../api/types'

export default function Login() {
  const { isAuthenticated, setJwt } = useAuth()
  const navigate = useNavigate()
  const [code, setCode] = useState('')
  const [message, setMessage] = useState('')
  const [msgType, setMsgType] = useState<'error' | 'success' | ''>('')
  const [submitting, setSubmitting] = useState(false)
  const [alreadyAuth, setAlreadyAuth] = useState(false)

  useEffect(() => {
    if (isAuthenticated) {
      apiFetch<AuthMeResponse>('/api/auth/me')
        .then(data => {
          if (data.authenticated) setAlreadyAuth(true)
        })
        .catch(() => {})
    }
  }, [isAuthenticated])

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    if (code.length !== 6) {
      setMessage('Введите 6-значный код')
      setMsgType('error')
      return
    }

    setSubmitting(true)
    setMessage('')

    try {
      const res = await fetch('/api/auth/verify-code', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ code }),
      })

      if (!res.ok) {
        const err = await res.json().catch(() => ({}))
        throw new Error(err.detail || 'Ошибка авторизации')
      }

      const data = await res.json()
      setJwt(data.token)
      setMessage('Успешно! Перенаправляю...')
      setMsgType('success')
      setTimeout(() => navigate('/'), 500)
    } catch (err) {
      setMessage(err instanceof Error ? err.message : 'Ошибка')
      setMsgType('error')
      setSubmitting(false)
      setCode('')
    }
  }

  return (
    <div className="min-h-screen flex items-center justify-center p-6">
      <div className="bg-surface border border-border rounded-2xl px-8 py-10 max-w-[400px] w-full text-center">
        <div className="text-5xl mb-4">🏊‍♂️🚴‍♂️🏃‍♂️</div>
        <h1 className="text-xl font-semibold mb-2">TriCoach AI</h1>
        <p className="text-text-dim text-sm mb-8 leading-relaxed">
          Отправьте <code className="bg-surface-2 px-1.5 py-0.5 rounded text-[13px] text-accent">/web</code> боту в Telegram,
          <br />затем введите полученный код
        </p>

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
            autoFocus
            className="w-full py-3.5 px-4 text-2xl font-semibold tracking-[8px] text-center bg-surface-2 border border-border rounded-xl text-text outline-none transition-colors focus:border-accent placeholder:tracking-[4px] placeholder:text-base placeholder:font-normal placeholder:text-text-dim"
          />
          <button
            type="submit"
            disabled={submitting}
            className="w-full py-3.5 mt-4 text-[15px] font-semibold bg-accent text-white border-none rounded-xl cursor-pointer transition-opacity hover:opacity-90 disabled:opacity-50 disabled:cursor-not-allowed font-sans"
          >
            {submitting ? 'Проверяю...' : 'Войти'}
          </button>
        </form>

        {message && (
          <div className={`mt-4 text-sm ${msgType === 'error' ? 'text-red' : 'text-green'}`}>
            {message}
          </div>
        )}

        <div className="mt-8 pt-6 border-t border-border text-left">
          <p className="text-text-dim text-[13px] mb-2">1. Откройте Telegram-бота</p>
          <p className="text-text-dim text-[13px] mb-2">
            2. Отправьте команду <code className="bg-surface-2 px-1.5 py-0.5 rounded text-[13px] text-accent">/web</code>
          </p>
          <p className="text-text-dim text-[13px] mb-2">3. Введите 6-значный код выше</p>
          <p className="text-text-dim text-[13px]">Код действует 5 минут</p>
        </div>

        {alreadyAuth && (
          <div className="mt-4 text-[13px] text-text-dim">
            Вы уже авторизованы.{' '}
            <a href="/" className="text-accent no-underline">Перейти на главную</a>
          </div>
        )}

      </div>
    </div>
  )
}
