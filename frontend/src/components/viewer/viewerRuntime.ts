/**
 * Runtime state of the 3D viewer (download progress, ready, error). Written from inside the R3F canvas,
 * read by the DOM overlays. The "secondary" model is the Auto Tune result mounted next to the original.
 */
import { create } from 'zustand'

export type ViewerLoadStatus = 'idle' | 'loading' | 'ready' | 'error'

interface ViewerRuntimeState {
  status: ViewerLoadStatus
  loadedBytes: number
  totalBytes: number
  error: string | null
  secondaryStatus: ViewerLoadStatus
  secondaryLoadedBytes: number
  secondaryTotalBytes: number
  secondaryError: string | null
  /** Registered by the lazy viewer while a secondary model is mounted (clears the loader cache and reloads it). */
  retrySecondary: (() => void) | null
  start: () => void
  setBytes: (loaded: number, total: number) => void
  setReady: () => void
  setError: (message: string) => void
  startSecondary: () => void
  resetSecondary: () => void
  setSecondaryBytes: (loaded: number, total: number) => void
  setSecondaryReady: () => void
  setSecondaryError: (message: string) => void
  setRetrySecondary: (retry: (() => void) | null) => void
}

export const useViewerRuntime = create<ViewerRuntimeState>()((set) => ({
  status: 'idle',
  loadedBytes: 0,
  totalBytes: 0,
  error: null,
  secondaryStatus: 'idle',
  secondaryLoadedBytes: 0,
  secondaryTotalBytes: 0,
  secondaryError: null,
  retrySecondary: null,
  start: () => set({ status: 'loading', loadedBytes: 0, totalBytes: 0, error: null }),
  setBytes: (loadedBytes, totalBytes) => set({ loadedBytes, totalBytes }),
  setReady: () => set({ status: 'ready', error: null }),
  setError: (error) => set({ status: 'error', error }),
  startSecondary: () =>
    set({ secondaryStatus: 'loading', secondaryLoadedBytes: 0, secondaryTotalBytes: 0, secondaryError: null }),
  resetSecondary: () =>
    set({ secondaryStatus: 'idle', secondaryLoadedBytes: 0, secondaryTotalBytes: 0, secondaryError: null }),
  setSecondaryBytes: (secondaryLoadedBytes, secondaryTotalBytes) => set({ secondaryLoadedBytes, secondaryTotalBytes }),
  setSecondaryReady: () => set({ secondaryStatus: 'ready', secondaryError: null }),
  setSecondaryError: (secondaryError) => set({ secondaryStatus: 'error', secondaryError }),
  setRetrySecondary: (retrySecondary) => set({ retrySecondary }),
}))
