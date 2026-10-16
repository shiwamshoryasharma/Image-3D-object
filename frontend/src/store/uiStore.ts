/** UI preferences: mobile tab, history drawer and viewer options (viewer options persisted). */
import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import { STORAGE_KEYS } from '../lib/storage'

export type MobileTab = 'inputs' | 'settings' | 'viewer' | 'history'
export type ViewMode = 'pbr' | 'clay' | 'normals'

export interface ViewerOptions {
  viewMode: ViewMode
  wireframe: boolean
  grid: boolean
  shadows: boolean
  environment: boolean
  autoRotate: boolean
}

export const DEFAULT_VIEWER_OPTIONS: ViewerOptions = {
  viewMode: 'pbr',
  wireframe: false,
  grid: true,
  shadows: true,
  environment: true,
  autoRotate: false,
}

interface UiState {
  mobileTab: MobileTab
  historyOpen: boolean
  viewer: ViewerOptions
  setMobileTab: (tab: MobileTab) => void
  setHistoryOpen: (open: boolean) => void
  setViewerOption: <K extends keyof ViewerOptions>(key: K, value: ViewerOptions[K]) => void
}

export const useUiStore = create<UiState>()(
  persist(
    (set) => ({
      mobileTab: 'inputs',
      historyOpen: false,
      viewer: DEFAULT_VIEWER_OPTIONS,
      setMobileTab: (mobileTab) => set({ mobileTab }),
      setHistoryOpen: (historyOpen) => set({ historyOpen }),
      setViewerOption: (key, value) => set((state) => ({ viewer: { ...state.viewer, [key]: value } })),
    }),
    {
      name: STORAGE_KEYS.ui,
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ viewer: state.viewer }),
      merge: (persisted, current) => {
        const saved = (persisted ?? {}) as Partial<Pick<UiState, 'viewer'>>
        return { ...current, viewer: { ...DEFAULT_VIEWER_OPTIONS, ...saved.viewer } }
      },
    },
  ),
)
