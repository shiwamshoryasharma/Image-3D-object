/**
 * Local UI preferences (UI_DESIGN §3.1; persisted as `img3d.preferences` v1). The persist `merge` runs the pure
 * `sanitizePreferences`, so a corrupt or unknown stored value can never select a mode that does not exist.
 * Hydration from `localStorage` is synchronous inside `create`: the first render already has the saved mode.
 */
import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import { STORAGE_KEYS } from '../lib/storage'
import {
  DEFAULT_PREFERENCES,
  sanitizePreferences,
  type DensityPreference,
  type MotionPreference,
  type Preferences,
  type UiMode,
} from '../lib/uiModes.ts'

export type {
  DensityPreference,
  LinkStyle,
  MotionPreference,
  NodeViewPreference,
  Preferences,
  UiMode,
} from '../lib/uiModes.ts'

type NodePreferences = Preferences['node']

interface PreferencesState extends Preferences {
  setUiMode: (mode: UiMode) => void
  setDensity: (density: DensityPreference) => void
  setMotion: (motion: MotionPreference) => void
  setNodePreference: <K extends keyof NodePreferences>(key: K, value: NodePreferences[K]) => void
  dismissHint: (id: string) => void
  reset: () => void
}

function defaults(): Preferences {
  return {
    uiMode: DEFAULT_PREFERENCES.uiMode,
    density: DEFAULT_PREFERENCES.density,
    motion: DEFAULT_PREFERENCES.motion,
    node: { ...DEFAULT_PREFERENCES.node },
    dismissedHints: [],
  }
}

export const usePreferencesStore = create<PreferencesState>()(
  persist(
    (set) => ({
      ...defaults(),
      setUiMode: (uiMode) => set((state) => (state.uiMode === uiMode ? state : { uiMode })),
      setDensity: (density) => set((state) => (state.density === density ? state : { density })),
      setMotion: (motion) => set((state) => (state.motion === motion ? state : { motion })),
      setNodePreference: (key, value) =>
        set((state) => (state.node[key] === value ? state : { node: { ...state.node, [key]: value } })),
      dismissHint: (id) =>
        set((state) => (state.dismissedHints.includes(id) ? state : { dismissedHints: [...state.dismissedHints, id] })),
      reset: () => set(defaults()),
    }),
    {
      name: STORAGE_KEYS.preferences,
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (state): Preferences => ({
        uiMode: state.uiMode,
        density: state.density,
        motion: state.motion,
        node: state.node,
        dismissedHints: state.dismissedHints,
      }),
      merge: (persisted, current) => ({ ...current, ...sanitizePreferences(persisted) }),
    },
  ),
)
