import { createContext, type ReactNode, useCallback, useContext, useMemo, useState } from 'react'
import { ApiConfigurationError, VerifierForgeClient, configuredApiBaseUrl } from '../api/client'

const STORAGE_KEY = 'verifierforge.invitation.session.v1'

interface AuthState {
  invitation: string | null
  client: VerifierForgeClient | null
  configurationError: string | null
  setInvitation: (value: string) => void
  clearInvitation: () => void
}

const AuthContext = createContext<AuthState | null>(null)

function storedInvitation(): string | null {
  try { return window.sessionStorage.getItem(STORAGE_KEY) } catch { return null }
}

export function AuthProvider({ children }: { children: ReactNode }) {
  const [invitation, setInvitationState] = useState<string | null>(storedInvitation)
  const setInvitation = useCallback((value: string) => {
    const clean = value.trim()
    if (!clean) return
    window.sessionStorage.setItem(STORAGE_KEY, clean)
    setInvitationState(clean)
  }, [])
  const clearInvitation = useCallback(() => {
    window.sessionStorage.removeItem(STORAGE_KEY)
    setInvitationState(null)
  }, [])
  const state = useMemo<AuthState>(() => {
    try {
      const baseUrl = configuredApiBaseUrl()
      return {
        invitation,
        client: new VerifierForgeClient({ baseUrl, invitation: () => invitation, onUnauthorized: clearInvitation }),
        configurationError: null,
        setInvitation,
        clearInvitation,
      }
    } catch (error) {
      return {
        invitation,
        client: null,
        configurationError: error instanceof ApiConfigurationError ? error.message : 'API configuration is invalid',
        setInvitation,
        clearInvitation,
      }
    }
  }, [clearInvitation, invitation, setInvitation])
  return <AuthContext.Provider value={state}>{children}</AuthContext.Provider>
}

// The provider and hook intentionally share the private context.
// eslint-disable-next-line react-refresh/only-export-components
export function useAuth(): AuthState {
  const value = useContext(AuthContext)
  if (!value) throw new Error('useAuth must be used inside AuthProvider')
  return value
}
