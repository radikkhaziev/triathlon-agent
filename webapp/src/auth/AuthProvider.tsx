import { createContext, useState, useCallback, useEffect, useMemo, useRef, type ReactNode } from 'react'
import { getInitData, persistInitData } from './telegram'
import { setAuthHeaderProvider } from '../api/client'

export type AuthRole = 'owner' | 'viewer' | 'demo' | 'anonymous'

export interface AuthState {
  role: AuthRole
  isAuthenticated: boolean
  isDemo: boolean
  authHeader: string
  logout: () => void
  setJwt: (token: string, role?: AuthRole) => void
}

export const AuthContext = createContext<AuthState>({
  role: 'anonymous',
  isAuthenticated: false,
  isDemo: false,
  authHeader: '',
  logout: () => {},
  setJwt: () => {},
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [jwt, setJwtState] = useState<string | null>(() => localStorage.getItem('auth_token'))
  const [role, setRole] = useState<AuthRole>(() => (localStorage.getItem('auth_role') as AuthRole) || 'anonymous')

  useEffect(() => {
    persistInitData()
  }, [])

  const authHeader = useMemo(() => {
    const initData = getInitData()
    if (initData) return initData
    if (jwt) return `Bearer ${jwt}`
    return ''
  }, [jwt])

  const isAuthenticated = authHeader.length > 0

  // Wire up API client auth header — must be synchronous (before child effects)
  const authRef = useRef(authHeader)
  authRef.current = authHeader
  setAuthHeaderProvider(() => authRef.current)

  const logout = useCallback(() => {
    localStorage.removeItem('auth_token')
    localStorage.removeItem('auth_role')
    setJwtState(null)
    setRole('anonymous')
  }, [])

  const setJwt = useCallback((token: string, newRole: AuthRole = 'viewer') => {
    localStorage.setItem('auth_token', token)
    localStorage.setItem('auth_role', newRole)
    setRole(newRole)
    setJwtState(token)
  }, [])

  // When auth comes from Telegram initData (not JWT), ignore stored role —
  // a previous demo session must not bleed into the real Telegram app.
  const viaInitData = isAuthenticated && !jwt
  const effectiveRole = isAuthenticated ? (viaInitData ? 'viewer' : role || 'viewer') : 'anonymous'

  const value = useMemo(() => ({
    role: effectiveRole as AuthRole,
    isAuthenticated,
    isDemo: effectiveRole === 'demo',
    authHeader,
    logout,
    setJwt,
  }), [effectiveRole, isAuthenticated, authHeader, logout, setJwt])

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  )
}
