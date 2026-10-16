/**
 * Backend connectivity, `/system` status and realtime (WebSocket) connection state.
 */
import { create } from 'zustand'

import { ApiError, toApiError } from '../api/client'
import { getSystemStatus, loadEngine as requestEngineLoad } from '../api/system'
import type { SystemStatus } from '../api/types'
import type { RealtimeStatus } from '../api/ws'
import { toast } from './toastStore'

interface SystemState {
  system: SystemStatus | null
  /** `null` until the first request settles. */
  reachable: boolean | null
  lastError: ApiError | null
  lastUpdatedAt: number | null
  realtime: RealtimeStatus
  engineLoadPending: boolean
  /** Bumped whenever the UI asks for an immediate reconnect/retry. */
  retrySignal: number

  setSystem: (system: SystemStatus) => void
  setReachable: (reachable: boolean, error?: ApiError) => void
  setRealtime: (status: RealtimeStatus) => void
  refresh: () => Promise<boolean>
  loadEngine: () => Promise<void>
  requestRetry: () => void
}

export const useSystemStore = create<SystemState>()((set, get) => ({
  system: null,
  reachable: null,
  lastError: null,
  lastUpdatedAt: null,
  realtime: { state: 'idle', attempt: 0, nextRetryAt: null },
  engineLoadPending: false,
  retrySignal: 0,

  setSystem: (system) => set({ system, lastUpdatedAt: Date.now(), reachable: true, lastError: null }),

  setReachable: (reachable, error) => {
    const current = get()
    if (current.reachable === reachable && (reachable || current.lastError === (error ?? null))) return
    set({ reachable, lastError: reachable ? null : (error ?? current.lastError) })
  },

  setRealtime: (realtime) => set({ realtime }),

  refresh: async () => {
    try {
      const system = await getSystemStatus()
      get().setSystem(system)
      return true
    } catch (error) {
      const apiError = await toApiError(error)
      if (!apiError.isUnreachable) set({ lastError: apiError })
      return false
    }
  },

  loadEngine: async () => {
    set({ engineLoadPending: true })
    try {
      const system = await requestEngineLoad()
      get().setSystem(system)
      toast.info('Loading model', 'TRELLIS.2 is loading in the background.')
    } catch (error) {
      const apiError = await toApiError(error)
      toast.error('Could not start model load', apiError.message)
    } finally {
      set({ engineLoadPending: false })
    }
  },

  requestRetry: () => set((state) => ({ retrySignal: state.retrySignal + 1 })),
}))

/** Convenience selectors. */
export const selectCapabilities = (state: SystemState) => state.system?.engine.capabilities ?? null
export const selectLimits = (state: SystemState) => state.system?.limits ?? null
export const selectEngineState = (state: SystemState) => state.system?.engine.state ?? null
