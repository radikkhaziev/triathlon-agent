import { useEffect, useState } from 'react'
import { apiFetch } from '../api/client'
import type { ChangelogLatest } from '../api/types'
import { useAuth } from '../auth/useAuth'

/**
 * Shared changelog state — сейчас единственный потребитель `HaloSidebar`
 * (desktop), но shared-promise/TTL-кэш сохранены: long-lived tab + future
 * surfaces не должны дёргать /api/changelog/latest повторно на каждый mount.
 *
 * Module-level singleton: первый компонент инициирует fetch, все последующие
 * подписываются на тот же Promise. Кэш с TTL — long-lived tab (атлет открыл
 * webapp в субботу, оставил, в воскресенье 15:00 опубликовался Discussion)
 * получит свежие данные при возврате к табу через `visibilitychange` без
 * full-page reload. Logout вызывает full-page navigation
 * (`window.location.href = '/login'` в AuthProvider) → модульный singleton
 * GC'ится, новый user видит свежий fetch независимо от TTL.
 */

const LAST_SEEN_KEY = 'changelog.last_seen_url'
const STALE_AFTER_MS = 15 * 60 * 1000 // 15 min — балансит между «свежесть» и шумом

let _inFlight: Promise<ChangelogLatest | null> | null = null
let _fetchedAt = 0

function readLastSeen(): string | null {
  // localStorage.getItem может бросить в storage-disabled средах (Safari
  // private legacy, sandboxed iframes). Treat as «непрочитан» при ошибке —
  // в худшем случае атлет увидит уже знакомую ссылку, что лучше чем crash.
  try {
    return localStorage.getItem(LAST_SEEN_KEY)
  } catch {
    return null
  }
}

function isStale(): boolean {
  return _fetchedAt > 0 && Date.now() - _fetchedAt > STALE_AFTER_MS
}

function fetchOnce(): Promise<ChangelogLatest | null> {
  if (_inFlight && !isStale()) return _inFlight
  _fetchedAt = Date.now()
  _inFlight = apiFetch<ChangelogLatest>('/api/changelog/latest').catch(() => {
    // M3 — без сброса первый 503/network blip фиксировал бы null навсегда:
    // следующий mount получал бы тот же resolved-to-null promise, ссылка
    // не возникала бы до full-page reload. Очищаем — следующий mount
    // или visibilitychange запустит fresh fetch.
    _inFlight = null
    _fetchedAt = 0
    return null
  })
  return _inFlight
}

export function useChangelog(): {
  changelog: ChangelogLatest | null
  unread: boolean
  markRead: () => void
} {
  const { isAuthenticated } = useAuth()
  const [changelog, setChangelog] = useState<ChangelogLatest | null>(null)
  const [unread, setUnread] = useState(false)

  useEffect(() => {
    // H1 — без auth-gate fetch на /login возвращает 401 → центральный
    // apiFetch handler force-redirect на /login, ломая login flow.
    if (!isAuthenticated) return
    let cancelled = false

    const refresh = () => {
      fetchOnce().then(cl => {
        if (cancelled || !cl) return
        setChangelog(cl)
        setUnread(cl.url !== readLastSeen())
      })
    }

    refresh()

    // visibilitychange — атлет вернулся к табу спустя N времени; если кэш
    // протух, сбрасываем singleton чтоб следующий fetchOnce пошёл за свежей
    // ссылкой. Cheap: invocation only fires when tab visibility flips.
    const onVisibility = () => {
      if (document.visibilityState !== 'visible') return
      if (!isStale()) return
      _inFlight = null
      refresh()
    }
    document.addEventListener('visibilitychange', onVisibility)

    return () => {
      cancelled = true
      document.removeEventListener('visibilitychange', onVisibility)
    }
  }, [isAuthenticated])

  const markRead = () => {
    // H2 — Safari private mode и переполненный quota бросают QuotaExceededError
    // на setItem; глотаем — unread-state можно потерять, но page не должна
    // падать из-за storage failure.
    try {
      if (changelog) localStorage.setItem(LAST_SEEN_KEY, changelog.url)
    } catch {
      // ignore
    }
    setUnread(false)
  }

  return { changelog, unread, markRead }
}
