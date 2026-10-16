/**
 * Generation settings form types and the request `settings` builder (UI_DESIGN.md §1.3, §2.8).
 *
 * Moved verbatim from `store/settingsStore.ts`, which re-exports every symbol so existing imports compile unchanged.
 * The output of `toRequestSettings` is the `settings` object of the unchanged `POST /jobs` body (golden fixture G1).
 *
 * PURE (UI_DESIGN §1.5): relative `.ts` value imports only; no React, zustand, axios or browser globals.
 */
import {
  SAMPLER_KEYS,
  type AdvancedSettings,
  type GenerationSettings,
  type ReferenceMode,
  type Resolution,
  type SamplerKey,
  type SamplerSettings,
  type TextureSize,
} from '../api/types.ts'

export type SamplerField = keyof Required<SamplerSettings>

export const SAMPLER_FIELDS: readonly SamplerField[] = ['steps', 'guidance_strength', 'guidance_rescale', 'rescale_t']

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

export function hasAdvancedOverrides(advanced: Record<SamplerKey, SamplerForm>): boolean {
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
