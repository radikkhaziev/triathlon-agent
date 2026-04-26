let getAuthHeaderFn: () => string = () => ''

export function setAuthHeaderProvider(fn: () => string) {
  getAuthHeaderFn = fn
}

/**
 * Error thrown by ``apiFetch`` for any non-2xx response **except 401**.
 * 401 has its own special path (clear token + redirect to /login) and
 * throws a plain ``Error('Unauthorized')`` so callers don't accidentally
 * pattern-match on ``ApiError.status === 401`` and bypass the redirect.
 *
 * Carries the original ``status`` and parsed ``detail`` so callers can branch
 * on structured payloads (e.g. ``detail = {error: "...", bot_username: ...}``
 * for the 412 bot-chat-not-initialized response, see issue #266).
 *
 * The default ``Error.message`` is still a human-readable string for code
 * paths that only log the failure, so existing callers stay correct.
 */
export class ApiError extends Error {
  status: number
  detail: unknown
  constructor(status: number, detail: unknown, fallback: string) {
    const msg =
      typeof detail === 'string'
        ? detail
        : detail && typeof detail === 'object' && 'error' in detail && typeof (detail as { error: unknown }).error === 'string'
          ? (detail as { error: string }).error
          : fallback
    super(msg)
    this.status = status
    this.detail = detail
    this.name = 'ApiError'
  }
}

/**
 * Thin ``fetch`` wrapper for our JSON API.
 *
 * - Attaches ``Authorization`` from ``setAuthHeaderProvider`` (initData / JWT).
 * - 401 → clear ``auth_token`` + redirect to ``/login`` + throw plain ``Error('Unauthorized')``.
 * - Any other non-2xx → throw ``ApiError`` carrying ``status`` and parsed ``detail``.
 * - 2xx → returns ``res.json()`` typed as ``T``.
 */
export async function apiFetch<T>(endpoint: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers as Record<string, string> || {}) }
  const auth = getAuthHeaderFn()
  if (auth) headers['Authorization'] = auth

  const res = await fetch(endpoint, { ...opts, headers })

  if (res.status === 401) {
    localStorage.removeItem('auth_token')
    if (window.location.pathname !== '/login') {
      window.location.href = '/login'
    }
    throw new Error('Unauthorized')
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new ApiError(res.status, body.detail, `API error: ${res.status}`)
  }

  return res.json()
}
