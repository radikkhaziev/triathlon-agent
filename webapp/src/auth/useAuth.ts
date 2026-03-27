import { useContext } from 'react'
import { AuthContext, type AuthState } from './AuthProvider'

export function useAuth(): AuthState {
  return useContext(AuthContext)
}
