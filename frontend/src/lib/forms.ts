/**
 * Shared input forms of every UI mode (UI_DESIGN.md §3.3): the prompt form, the input source and the part options,
 * with their defaults and the sanitizers the persisted stores run in `merge` (`img3d.input`, `img3d.partOptions`).
 *
 * The sanitizers drop unknown keys and corrupt values (falling back to the defaults), so a broken or old saved value
 * can never put a store into an impossible state. Backend limits (prompt length, sizes, models) are applied later by
 * the request builders against `/features`; storage only enforces fixed safety bounds.
 *
 * PURE (UI_DESIGN §1.5): relative `.ts` value imports only; no React, zustand, axios or browser globals.
 */
import type { ImageGenerationRequest, PromptPreset, PromptStyle, SegProfile, SegSubject } from '../api/partAwareTypes.ts'
import { FALLBACK_LIMITS, PROMPT_PRESETS, PROMPT_STYLES, SEG_SUBJECTS } from './featureFlags.ts'

export type InputSource = 'image' | 'prompt'
export type PromptFlow = 'review' | 'one_shot'

export interface PromptForm {
  /** The real limit comes from `/features`; storage truncates anything over `PROMPT_STORE_MAX_CHARS`. */
  prompt: string
  preset: PromptPreset
  /** `null` = no style clause. */
  style: PromptStyle | null
  partHint: boolean
  flow: PromptFlow
  /** Review flow only; clamped to `features.prompt.maxImages` when the request is built. */
  numCandidates: 1 | 2 | 3 | 4
  seed: number
  randomizeSeed: boolean
  /** `null` = backend default model. */
  modelId: string | null
  /** `null` = preset default; must be one of `features.prompt.sizes`. */
  size: [number, number] | null
  /** Only shown and sent when the model supports negative prompts. */
  negativePrompt: string
}

export interface PartOptionsForm {
  /** `false` → every request is today's (golden fixture G1). */
  enabled: boolean
  /** `null` = the backend's `default_strategy` (not sent). */
  strategy: string | null
  /** `null` = the groups the backend marks `default_selected` (sent resolved). */
  separate: string[] | null
  subject: SegSubject
  profile: SegProfile
  extraTerms: string[]
  /** `null` = the backend's `default_views` (not sent). */
  views: number | null
  /** `true` → the workflow runs its own segmentation step before reconstruction (`segmentation` in G2a). */
  previewSegmentation: boolean
  /** "Separate part files" → `parts.export.per_part_glbs`. */
  perPartGlbs: boolean
}

export const INPUT_SOURCES: readonly InputSource[] = Object.freeze(['image', 'prompt'])
export const PROMPT_FLOWS: readonly PromptFlow[] = Object.freeze(['review', 'one_shot'])
export const SEG_PROFILES: readonly SegProfile[] = Object.freeze(['auto', 'baseline', 'anime', 'realistic'])

/** Storage bound for the prompt and negative prompt text (the backend limit is far lower). */
export const PROMPT_STORE_MAX_CHARS = 8000
/** Longest "Also look for" term (UI_DESIGN §2.8, §6.2). */
export const EXTRA_TERM_MAX_CHARS = 40
/** Storage bound for the number of extra terms (the request is capped to `features.segmentation.maxExtraTerms`). */
const EXTRA_TERMS_STORE_MAX = 64
const ID_MAX_CHARS = 128
const MAX_CANDIDATES = 4
/** Same bound as `settingsStore.MAX_SEED` (the store is not importable from pure modules). */
const MAX_SEED = 2_147_483_647

export const DEFAULT_PROMPT_FORM: Readonly<PromptForm> = Object.freeze({
  prompt: '',
  preset: 'character',
  style: null,
  partHint: true,
  flow: 'review',
  numCandidates: 2,
  seed: 0,
  randomizeSeed: true,
  modelId: null,
  size: null,
  negativePrompt: '',
})

export const DEFAULT_PART_OPTIONS_FORM: Readonly<PartOptionsForm> = Object.freeze({
  enabled: false,
  strategy: null,
  separate: null,
  subject: 'auto',
  profile: 'auto',
  extraTerms: [],
  views: null,
  previewSegmentation: false,
  perPartGlbs: false,
})

type Json = Record<string, unknown>

const isObject = (value: unknown): value is Json => typeof value === 'object' && value !== null && !Array.isArray(value)

const member = <T extends string>(value: unknown, allowed: readonly T[], fallback: T): T =>
  typeof value === 'string' && (allowed as readonly string[]).includes(value) ? (value as T) : fallback

const boolOr = (value: unknown, fallback: boolean): boolean => (typeof value === 'boolean' ? value : fallback)

const text = (value: unknown, fallback: string): string =>
  typeof value === 'string' ? value.slice(0, PROMPT_STORE_MAX_CHARS) : fallback

/** A short identifier-like string, or `null`. */
function idOrNull(value: unknown): string | null {
  if (typeof value !== 'string') return null
  const trimmed = value.trim()
  return trimmed && trimmed.length <= ID_MAX_CHARS ? trimmed : null
}

function clampInt(value: unknown, min: number, max: number, fallback: number): number {
  if (typeof value !== 'number' || !Number.isFinite(value)) return fallback
  return Math.min(max, Math.max(min, Math.round(value)))
}

function sizeOrNull(value: unknown): [number, number] | null {
  if (!Array.isArray(value) || value.length !== 2) return null
  const [w, h] = value
  const ok = (n: unknown): n is number => typeof n === 'number' && Number.isInteger(n) && n > 0 && n <= 8192
  return ok(w) && ok(h) ? [w, h] : null
}

/**
 * Trimmed, de-duplicated (case-insensitively, first spelling kept) terms of at most `EXTRA_TERM_MAX_CHARS` characters,
 * at most `max` of them. Used by the sanitizer and by the request builder (with the backend's `maxExtraTerms`).
 */
export function normalizeExtraTerms(value: unknown, max: number): string[] {
  const out: string[] = []
  const seen = new Set<string>()
  for (const item of Array.isArray(value) ? value : []) {
    if (out.length >= max) break
    if (typeof item !== 'string') continue
    const term = item.trim().slice(0, EXTRA_TERM_MAX_CHARS).trim()
    const key = term.toLowerCase()
    if (!term || seen.has(key)) continue
    seen.add(key)
    out.push(term)
  }
  return out
}

/** Persisted / imported prompt form → a valid form (unknown keys dropped, corrupt values → defaults). */
export function sanitizePromptForm(raw: unknown): PromptForm {
  const d = DEFAULT_PROMPT_FORM
  if (!isObject(raw)) return { ...d }
  const style = raw.style === null ? null : member<PromptStyle | 'none'>(raw.style, PROMPT_STYLES, 'none')
  return {
    prompt: text(raw.prompt, d.prompt),
    preset: member(raw.preset, PROMPT_PRESETS, d.preset),
    style: style === 'none' ? d.style : style,
    partHint: boolOr(raw.partHint, d.partHint),
    flow: member(raw.flow, PROMPT_FLOWS, d.flow),
    numCandidates: clampInt(raw.numCandidates, 1, MAX_CANDIDATES, d.numCandidates) as PromptForm['numCandidates'],
    seed: clampInt(raw.seed, 0, MAX_SEED, d.seed),
    randomizeSeed: boolOr(raw.randomizeSeed, d.randomizeSeed),
    modelId: idOrNull(raw.modelId),
    size: sizeOrNull(raw.size),
    negativePrompt: text(raw.negativePrompt, d.negativePrompt),
  }
}

/** Persisted part options → a valid form (unknown keys dropped, corrupt values → defaults). */
export function sanitizePartOptionsForm(raw: unknown): PartOptionsForm {
  const d = DEFAULT_PART_OPTIONS_FORM
  if (!isObject(raw)) return { ...d, extraTerms: [] }
  let separate: string[] | null = null
  if (Array.isArray(raw.separate)) {
    separate = []
    for (const item of raw.separate) {
      const id = idOrNull(item)
      if (id !== null && !separate.includes(id)) separate.push(id)
    }
  }
  return {
    enabled: boolOr(raw.enabled, d.enabled),
    strategy: idOrNull(raw.strategy),
    separate,
    subject: member(raw.subject, SEG_SUBJECTS, d.subject),
    profile: member(raw.profile, SEG_PROFILES, d.profile),
    extraTerms: normalizeExtraTerms(raw.extraTerms, EXTRA_TERMS_STORE_MAX),
    views: typeof raw.views === 'number' && Number.isFinite(raw.views) ? clampInt(raw.views, 0, FALLBACK_LIMITS.viewsMax, 0) : null,
    previewSegmentation: boolOr(raw.previewSegmentation, d.previewSegmentation),
    perPartGlbs: boolOr(raw.perPartGlbs, d.perPartGlbs),
  }
}

export function sanitizeInputSource(raw: unknown): InputSource {
  return member(raw, INPUT_SOURCES, 'image')
}

/** `img3d.input` persisted state `{source, prompt}` → valid values. */
export function sanitizeInputState(raw: unknown): { source: InputSource; prompt: PromptForm } {
  const state = isObject(raw) ? raw : {}
  return { source: sanitizeInputSource(state.source), prompt: sanitizePromptForm(state.prompt) }
}

/** `img3d.partOptions` persisted state `{form}` → a valid form. */
export function sanitizePartOptionsState(raw: unknown): { form: PartOptionsForm } {
  return { form: sanitizePartOptionsForm(isObject(raw) ? raw.form : null) }
}

/**
 * A request echoed by the backend (`ImageGenerationOut.options`, a workflow's `source.generation`) → a prompt form,
 * for "Reuse prompt" / "Regenerate from prompt". Unknown values fall back to the defaults; the flow is kept.
 */
export function promptFormFromRequest(g: ImageGenerationRequest, flow: PromptFlow = DEFAULT_PROMPT_FORM.flow): PromptForm {
  const width = typeof g.width === 'number' ? g.width : null
  const height = typeof g.height === 'number' ? g.height : null
  return sanitizePromptForm({
    prompt: g.prompt,
    preset: g.preset,
    style: g.style ?? null,
    partHint: g.part_hint,
    flow,
    // One-shot runs always ask for one image; keep the review default instead of storing that 1.
    numCandidates: flow === 'review' ? g.num_images : DEFAULT_PROMPT_FORM.numCandidates,
    seed: g.seed,
    randomizeSeed: g.randomize_seed,
    modelId: g.model_id ?? null,
    size: width !== null && height !== null ? [width, height] : null,
    negativePrompt: g.negative_prompt ?? '',
  })
}
