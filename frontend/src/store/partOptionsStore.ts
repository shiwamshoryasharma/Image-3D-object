/**
 * Part-aware options form shared by every UI mode (UI_DESIGN §3.3; persisted as `img3d.partOptions` v1).
 * `enabled: false` keeps every request exactly today's (G1). The persist `merge` runs `sanitizePartOptionsState`.
 */
import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import { DEFAULT_PART_OPTIONS_FORM, sanitizePartOptionsState, type PartOptionsForm } from '../lib/forms.ts'
import { STORAGE_KEYS } from '../lib/storage'

export type { PartOptionsForm } from '../lib/forms.ts'

interface PartOptionsState {
  form: PartOptionsForm
  /** Merges `patch`; a patch that changes nothing is skipped (no new object, no re-render). */
  update: (patch: Partial<PartOptionsForm>) => void
  reset: () => void
}

const defaultForm = (): PartOptionsForm => ({ ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [] })

export const usePartOptionsStore = create<PartOptionsState>()(
  persist(
    (set) => ({
      form: defaultForm(),
      update: (patch) =>
        set((state) => {
          const changed = (Object.keys(patch) as (keyof PartOptionsForm)[]).some((key) => state.form[key] !== patch[key])
          return changed ? { form: { ...state.form, ...patch } } : state
        }),
      reset: () => set({ form: defaultForm() }),
    }),
    {
      name: STORAGE_KEYS.partOptions,
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ form: state.form }),
      merge: (persisted, current) => ({ ...current, ...sanitizePartOptionsState(persisted) }),
    },
  ),
)
