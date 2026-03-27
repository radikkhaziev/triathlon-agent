import { createContext, useState, useCallback, useEffect, useMemo, type ReactNode } from 'react'
import { getInitData, persistInitData } from './telegram'
import { setAuthHeaderProvider } from '../api/client'

export type AuthRole = 'owner' | 'viewer' | 'anonymous'

export interface AuthState {
  role: AuthRole
  isAuthenticated: boolean
  authHeader: string
  logout: () => void
  setJwt: (token: string) => void
}

export const AuthContext = createContext<AuthState>({
  role: 'anonymous',
  isAuthenticated: false,
  authHeader: '',
  logout: () => {},
  setJwt: () => {},
})

export function AuthProvider({ children }: { children: ReactNode }) {
  const [jwt, setJwtState] = useState<string | null>(() => localStorage.getItem('auth_token'))

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

  // Wire up API client auth header provider once
  const authRef = useMemo(() => ({ current: authHeader }), [authHeader])
  useEffect(() => {
    authRef.current = authHeader
    setAuthHeaderProvider(() => authRef.current)
  }, [authHeader, authRef])

  const logout = useCallback(() => {
    localStorage.removeItem('auth_token')
    setJwtState(null)
  }, [])

  const setJwt = useCallback((token: string) => {
    localStorage.setItem('auth_token', token)
    setJwtState(token)
  }, [])

  const value = useMemo(() => ({
    role: (isAuthenticated ? 'viewer' : 'anonymous') as AuthRole,
    isAuthenticated,
    authHeader,
    logout,
    setJwt,
  }), [isAuthenticated, authHeader, logout, setJwt])

  return (
    <AuthContext.Provider value={value}>
      {children}
    </AuthContext.Provider>
  )
}
