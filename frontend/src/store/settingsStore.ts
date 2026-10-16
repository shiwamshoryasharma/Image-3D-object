/**
 * Generation settings form (persisted in localStorage). Defaults come from `/system.defaults` the first time
 * the system status is known; values are clamped to the engine capabilities whenever they change.
 */
import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import {
  SAMPLER_KEYS,
  type AdvancedSettings,
  type EngineCapabilities,
  type GenerationSettings,
  type NormalizedGenerationSettings,
  type ReferenceMode,
  type Resolution,
  type SamplerKey,
  type SamplerSettings,
  type SystemDefaults,
  type TextureSize,
} from '../api/types'
import { clamp } from '../lib/format'
import { STORAGE_KEYS } from '../lib/storage'

export const MAX_SEED = 2_147_483_647

export type SamplerField = keyof Required<SamplerSettings>

export const SAMPLER_FIELDS: readonly SamplerField[] = ['steps', 'guidance_strength', 'guidance_rescale', 'rescale_t']

/** Request bounds from the contract (§7.4). */
export const SAMPLER_BOUNDS: Record<SamplerField, { min: number; max: number; step: number }> = {
  steps: { min: 1, max: 50, step: 1 },
  guidance_strength: { min: 0, max: 10, step: 0.1 },
  guidance_rescale: { min: 0, max: 1, step: 0.05 },
  rescale_t: { min: 1, max: 6, step: 0.1 },
}

/** Upstream TRELLIS.2 sampler defaults (contract §5). Used as placeholders; `null` = engine default. */
export const SAMPLER_DEFAULTS: Record<SamplerKey, Required<SamplerSettings>> = {
  sparse_structure: { steps: 12, guidance_strength: 7.5, guidance_rescale: 0.7, rescale_t: 5.0 },
  shape: { steps: 12, guidance_strength: 7.5, guidance_rescale: 0.5, rescale_t: 3.0 },
  texture: { steps: 12, guidance_strength: 1.0, guidance_rescale: 0.0, rescale_t: 3.0 },
}

export type SamplerForm = Record<SamplerField, number | null>

export interface SettingsForm {
  resolution: Resolution
  seed: number
  randomize_seed: boolean
  polygon_target: number
  texture_size: TextureSize
  remove_background: boolean
  advanced: Record<SamplerKey, SamplerForm>
}

const emptySampler = (): SamplerForm => ({ steps: null, guidance_strength: null, guidance_rescale: null, rescale_t: null })

const emptyAdvanced = (): Record<SamplerKey, SamplerForm> => ({
  sparse_structure: emptySampler(),
  shape: emptySampler(),
  texture: emptySampler(),
})

/** Contract defaults (§7.2 `defaults`), used until `/system` responds. */
const CONTRACT_DEFAULTS: Omit<SettingsForm, 'advanced'> = {
  resolution: 1024,
  seed: 0,
  randomize_seed: true,
  polygon_target: 500_000,
  texture_size: 2048,
  remove_background: true,
}

function formFromDefaults(defaults: SystemDefaults | null): SettingsForm {
  const base = defaults
    ? {
        resolution: defaults.resolution,
        seed: defaults.seed,
        randomize_seed: defaults.randomize_seed,
        polygon_target: defaults.polygon_target,
        texture_size: defaults.texture_size,
        remove_background: defaults.remove_background,
      }
    : CONTRACT_DEFAULTS
  return { ...base, advanced: emptyAdvanced() }
}

function nearest<T extends number>(value: number, options: readonly T[], fallback: T): T {
  if (options.length === 0) return fallback
  if ((options as readonly number[]).includes(value)) return value as T
  return options.reduce((best, option) => (Math.abs(option - value) < Math.abs(best - value) ? option : best), options[0])
}

/** Clamp a form to what the engine supports. */
export function clampToCapabilities(form: SettingsForm, capabilities: EngineCapabilities): SettingsForm {
  const { polygon_target: range } = capabilities
  return {
    ...form,
    resolution: nearest(form.resolution, capabilities.resolutions, form.resolution),
    texture_size: nearest(form.texture_size, capabilities.texture_sizes, form.texture_size),
    polygon_target: Math.round(clamp(form.polygon_target, range.min, range.max)),
    seed: Math.round(clamp(form.seed, 0, MAX_SEED)),
  }
}

function hasAdvancedOverrides(advanced: Record<SamplerKey, SamplerForm>): boolean {
  return SAMPLER_KEYS.some((key) => SAMPLER_FIELDS.some((field) => advanced[key][field] !== null))
}

/** Build the request `settings` object (only explicit advanced overrides are sent). */
export function toRequestSettings(form: SettingsForm, referenceMode: ReferenceMode): GenerationSettings {
  const settings: GenerationSettings = {
    resolution: form.resolution,
    seed: form.seed,
    randomize_seed: form.randomize_seed,
    polygon_target: form.polygon_target,
    texture_size: form.texture_size,
    remove_background: form.remove_background,
    reference_mode: referenceMode,
  }
  if (hasAdvancedOverrides(form.advanced)) {
    const advanced: AdvancedSettings = {}
    for (const key of SAMPLER_KEYS) {
      const sampler: SamplerSettings = {}
      for (const field of SAMPLER_FIELDS) {
        const value = form.advanced[key][field]
        if (value !== null) sampler[field] = value
      }
      if (Object.keys(sampler).length > 0) advanced[key] = sampler
    }
    settings.advanced = advanced
  }
  return settings
}

/** Convert a job's normalized settings back into a form (used by "reuse settings"). */
export function formFromJobSettings(settings: NormalizedGenerationSettings, seed: number): SettingsForm {
  const advanced = emptyAdvanced()
  for (const key of SAMPLER_KEYS) {
    const sampler = settings.advanced?.[key]
    if (!sampler) continue
    for (const field of SAMPLER_FIELDS) {
      const value = sampler[field]
      if (typeof value === 'number') advanced[key][field] = value
    }
  }
  return {
    resolution: settings.resolution,
    seed,
    randomize_seed: false,
    polygon_target: settings.polygon_target,
    texture_size: settings.texture_size,
    remove_background: settings.remove_background,
    advanced,
  }
}

interface SettingsState {
  form: SettingsForm
  /** True once defaults from the server were applied (or the user edited something). */
  initialized: boolean
  advancedOpen: boolean

  update: (patch: Partial<Omit<SettingsForm, 'advanced'>>) => void
  updateSampler: (key: SamplerKey, field: SamplerField, value: number | null) => void
  resetSampler: (key: SamplerKey) => void
  resetAdvanced: () => void
  resetAll: (defaults: SystemDefaults | null, capabilities: EngineCapabilities | null) => void
  replaceForm: (form: SettingsForm, capabilities: EngineCapabilities | null) => void
  syncWithSystem: (defaults: SystemDefaults, capabilities: EngineCapabilities) => void
  setAdvancedOpen: (open: boolean) => void
}

export const useSettingsStore = create<SettingsState>()(
  persist(
    (set, get) => ({
      form: formFromDefaults(null),
      initialized: false,
      advancedOpen: false,

      update: (patch) => set((state) => ({ form: { ...state.form, ...patch }, initialized: true })),

      updateSampler: (key, field, value) =>
        set((state) => ({
          initialized: true,
          form: {
            ...state.form,
            advanced: { ...state.form.advanced, [key]: { ...state.form.advanced[key], [field]: value } },
          },
        })),

      resetSampler: (key) =>
        set((state) => ({ form: { ...state.form, advanced: { ...state.form.advanced, [key]: emptySampler() } } })),

      resetAdvanced: () => set((state) => ({ form: { ...state.form, advanced: emptyAdvanced() } })),

      resetAll: (defaults, capabilities) => {
        const form = formFromDefaults(defaults)
        set({ form: capabilities ? clampToCapabilities(form, capabilities) : form, initialized: true })
      },

      replaceForm: (form, capabilities) =>
        set({ form: capabilities ? clampToCapabilities(form, capabilities) : form, initialized: true }),

      syncWithSystem: (defaults, capabilities) => {
        const { form, initialized } = get()
        const base = initialized ? form : formFromDefaults(defaults)
        const next = clampToCapabilities(base, capabilities)
        if (!initialized || JSON.stringify(next) !== JSON.stringify(form)) set({ form: next, initialized: true })
      },

      setAdvancedOpen: (advancedOpen) => set({ advancedOpen }),
    }),
    {
      name: STORAGE_KEYS.settings,
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ form: state.form, initialized: state.initialized, advancedOpen: state.advancedOpen }),
      merge: (persisted, current) => {
        const saved = (persisted ?? {}) as Partial<Pick<SettingsState, 'form' | 'initialized' | 'advancedOpen'>>
        if (!saved.form) return current
        const advanced = emptyAdvanced()
        for (const key of SAMPLER_KEYS) {
          const savedSampler = saved.form.advanced?.[key]
          if (!savedSampler) continue
          for (const field of SAMPLER_FIELDS) {
            const value = savedSampler[field]
            advanced[key][field] = typeof value === 'number' && Number.isFinite(value) ? value : null
          }
        }
        return {
          ...current,
          form: { ...current.form, ...saved.form, advanced },
          initialized: saved.initialized ?? false,
          advancedOpen: saved.advancedOpen ?? false,
        }
      },
    },
  ),
)

export function randomSeed(): number {
  const buffer = new Uint32Array(1)
  crypto.getRandomValues(buffer)
  return buffer[0] % (MAX_SEED + 1)
}
