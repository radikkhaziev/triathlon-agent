let getAuthHeaderFn: () => string = () => ''

export function setAuthHeaderProvider(fn: () => string) {
  getAuthHeaderFn = fn
}

export async function apiFetch<T>(endpoint: string, opts: RequestInit = {}): Promise<T> {
  const headers: Record<string, string> = { ...(opts.headers as Record<string, string> || {}) }
  const auth = getAuthHeaderFn()
  if (auth) headers['Authorization'] = auth

  const res = await fetch(endpoint, { ...opts, headers })

  if (res.status === 401) {
    localStorage.removeItem('auth_token')
    window.location.href = '/login'
    throw new Error('Unauthorized')
  }

  if (!res.ok) {
    const body = await res.json().catch(() => ({}))
    throw new Error(body.detail || `API error: ${res.status}`)
  }

  return res.json()
}
