/**
 * Authentication state. The app only renders protected UI once `phase === 'ready'`.
 *
 * phase:
 *  - `checking`    first `/auth/status` request in flight
 *  - `unreachable` backend could not be reached before auth status was known (retry loop runs)
 *  - `login`       auth required and not authenticated → login screen
 *  - `ready`       auth not required, or authenticated
 */
import { create } from 'zustand'

import { getAuthStatus, login as requestLogin, logout as requestLogout } from '../api/auth'
import { ApiError, getAuthToken, setAuthToken, toApiError } from '../api/client'
import { toast } from './toastStore'

export type AuthPhase = 'checking' | 'unreachable' | 'login' | 'ready'

interface AuthState {
  phase: AuthPhase
  authRequired: boolean
  username: string | null
  checkError: ApiError | null
  loginPending: boolean
  loginError: string | null
  /** Shown on the login screen after a forced logout (expired/revoked token). */
  notice: string | null

  check: () => Promise<void>
  login: (username: string, password: string) => Promise<boolean>
  logout: () => Promise<void>
  expire: (message?: string) => void
}

export const useAuthStore = create<AuthState>()((set, get) => ({
  phase: 'checking',
  authRequired: false,
  username: null,
  checkError: null,
  loginPending: false,
  loginError: null,
  notice: null,

  check: async () => {
    try {
      const status = await getAuthStatus()
      if (status.auth_required && !status.authenticated) {
        if (getAuthToken()) setAuthToken(null)
        set({ phase: 'login', authRequired: true, username: null, checkError: null })
        return
      }
      set({
        phase: 'ready',
        authRequired: status.auth_required,
        username: status.username,
        checkError: null,
        notice: null,
      })
    } catch (error) {
      const apiError = await toApiError(error)
      // Keep an already-ready session running; the connectivity banner handles outages.
      if (get().phase === 'ready') return
      set({ phase: get().phase === 'login' ? 'login' : 'unreachable', checkError: apiError })
    }
  },

  login: async (username, password) => {
    set({ loginPending: true, loginError: null })
    try {
      const response = await requestLogin({ username, password })
      setAuthToken(response.token)
      set({ loginPending: false, notice: null })
      await get().check()
      return get().phase === 'ready'
    } catch (error) {
      const apiError = await toApiError(error)
      const message =
        apiError.status === 401
          ? 'Invalid username or password.'
          : apiError.isUnreachable
            ? 'Cannot reach the backend. Check that it is running and try again.'
            : apiError.message
      set({ loginPending: false, loginError: message })
      return false
    }
  },

  logout: async () => {
    try {
      await requestLogout()
    } catch {
      // Token may already be invalid; logging out locally is what matters.
    }
    setAuthToken(null)
    set({ phase: get().authRequired ? 'login' : 'ready', username: null, notice: null, loginError: null })
    if (!get().authRequired) void get().check()
  },

  expire: (message = 'Your session has expired. Please sign in again.') => {
    setAuthToken(null)
    if (get().phase === 'login') return
    set({ phase: 'login', authRequired: true, username: null, notice: message })
    toast.warning('Signed out', message)
  },
}))
