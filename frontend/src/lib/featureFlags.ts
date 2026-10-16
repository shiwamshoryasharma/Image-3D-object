/**
 * `/features` normalisation (UI_DESIGN.md §2.5): one immutable `FeaturesSnapshot` every mode reads, plus the
 * LOADING / ERROR / LEGACY snapshots used before (or instead of) a real answer.
 *
 * `normalizeFeatures` is tolerant: a missing or malformed sub-object becomes that capability
 * `{available: false, code: 'missing'}`, never an exception, so a partly broken payload cannot break the UI.
 * Values the backend may add later are ignored. Nothing here is placeholder data: unknown lists stay empty.
 *
 * PURE (UI_DESIGN §1.5): relative `.ts` value imports only; no React, zustand, axios or browser globals.
 */
import type {
  FeatureNodeOut,
  ModelOut,
  ModelUiState,
  PartGroupInfoOut,
  ProfileOut,
  PromptPreset,
  PromptStyle,
  SegSubject,
  StrategyOut,
  TaxonomyClassOut,
} from '../api/partAwareTypes.ts'
import type { JobStage } from '../api/types.ts'

export type FeaturesStatus = 'loading' | 'ready' | 'legacy' | 'error'

export interface CapabilityView {
  readonly available: boolean
  readonly code: string
  readonly reason: string | null
}

export interface FeaturesSnapshot {
  readonly status: FeaturesStatus
  readonly apiVersion: string | null
  readonly flags: Readonly<{
    partAware: boolean
    imageGeneration: boolean
    segmentation: boolean
    partAnalysis: boolean
    workflows: boolean
    meshTuning: boolean
    partRefinement: boolean
  }>
  readonly imageTo3d: CapabilityView
  readonly prompt: CapabilityView & {
    readonly maxChars: number
    readonly presets: readonly PromptPreset[]
    readonly styles: readonly PromptStyle[]
    readonly supportsNegativePrompt: boolean
    readonly negativeMaxChars: number
    readonly defaultModelId: string | null
    readonly defaultModelState: ModelUiState | null
    readonly coldStartS: number | null
    readonly maxImages: number
    readonly sizes: readonly (readonly [number, number])[]
  }
  readonly segmentation: CapabilityView & {
    readonly subjects: readonly SegSubject[]
    readonly profiles: readonly ProfileOut[]
    readonly maxExtraTerms: number
  }
  readonly parts: CapabilityView & {
    readonly strategies: readonly StrategyOut[]
    readonly defaultStrategy: string | null
    readonly separableGroups: readonly PartGroupInfoOut[]
    readonly regionGroups: readonly PartGroupInfoOut[]
    readonly maxViews: number
    readonly defaultViews: number
    readonly rebuild: boolean
  }
  readonly workflow: CapabilityView & {
    readonly catalogEtag: string | null
    readonly maxNodes: number
    readonly maxEdges: number
    readonly graphMaxBytes: number
    readonly legacyJobNodeMap: Readonly<Record<string, string>>
  }
  readonly nodes: readonly FeatureNodeOut[]
  readonly meshTuning: Readonly<{ enabled: boolean; reason: string | null }>
  readonly partRefinement: Readonly<{ enabled: boolean; reason: string | null }>
  readonly models: readonly ModelOut[]
  readonly taxonomy: Readonly<{ version: number; groups: readonly string[]; classes: readonly TaxonomyClassOut[] }>
  readonly runQueue: Readonly<{ pending: number; runningId: string | null; maxSize: number }> | null
}

/** DESIGN §4.7.2 `LEGACY_JOB_NODE_MAP`, verbatim (used when neither `/features` nor the catalog provides it). */
export const FALLBACK_LEGACY_JOB_NODE_MAP: Readonly<Record<JobStage, string>> = Object.freeze({
  queued: 'image_to_3d',
  waiting_for_model: 'image_to_3d',
  preprocessing: 'input_image',
  conditioning: 'image_to_3d',
  sparse_structure: 'image_to_3d',
  shape: 'image_to_3d',
  texture: 'image_to_3d',
  decoding: 'image_to_3d',
  exporting: 'export',
  finalizing: 'export',
  done: 'export',
})

/** DESIGN limits used when `/features` does not report them. */
export const FALLBACK_LIMITS = {
  promptMaxChars: 2000,
  negativeMaxChars: 1000,
  extraTermsMax: 16,
  viewsMax: 24,
  maxNodes: 32,
  maxEdges: 64,
  graphMaxBytes: 65536,
} as const

/** DESIGN §6.1 values without a `FALLBACK_LIMITS` entry (candidate indices are `^[0-3]$`; default 8 extra views). */
const FALLBACK_MAX_IMAGES = 4
const FALLBACK_DEFAULT_VIEWS = 8

/** DESIGN v1 enum values (the type unions of `partAwareTypes.ts`); payload lists are filtered to these. */
export const PROMPT_PRESETS: readonly PromptPreset[] = Object.freeze(['character', 'object', 'raw'])
export const PROMPT_STYLES: readonly PromptStyle[] = Object.freeze(['realistic', 'stylized', 'anime'])
export const SEG_SUBJECTS: readonly SegSubject[] = Object.freeze(['auto', 'anime_character', 'realistic_human', 'object'])
const PROFILE_IDS: readonly ProfileOut['id'][] = ['baseline', 'anime', 'realistic']
const MODEL_UI_STATES: readonly ModelUiState[] = ['not_installed', 'installed', 'loading', 'loaded', 'error', 'unavailable']
const GROUP_KINDS: readonly PartGroupInfoOut['default_kind'][] = ['physical', 'physical_deferred', 'region']
const CLASS_KINDS: readonly TaxonomyClassOut['default_kind'][] = ['base', 'physical', 'physical_deferred', 'region']

const MISSING_REASON = 'The backend did not report this capability.'

// ------------------------------------------------------------------------------------------------
// Fixed snapshots
// ------------------------------------------------------------------------------------------------

function deepFreeze<T>(value: T): T {
  if (value !== null && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value)
    for (const child of Object.values(value)) deepFreeze(child)
  }
  return value
}

const OFF = Object.freeze({ partAware: false, imageGeneration: false, segmentation: false, partAnalysis: false, workflows: false, meshTuning: false, partRefinement: false })

/** A snapshot in which every part-aware capability carries `capability`; image-to-3D (the existing path) stays usable. */
function unavailableSnapshot(status: FeaturesStatus, capability: CapabilityView): FeaturesSnapshot {
  return deepFreeze<FeaturesSnapshot>({
    status,
    apiVersion: null,
    flags: OFF,
    imageTo3d: { available: true, code: 'ok', reason: null },
    prompt: {
      ...capability,
      maxChars: FALLBACK_LIMITS.promptMaxChars,
      presets: [],
      styles: [],
      supportsNegativePrompt: false,
      negativeMaxChars: FALLBACK_LIMITS.negativeMaxChars,
      defaultModelId: null,
      defaultModelState: null,
      coldStartS: null,
      maxImages: FALLBACK_MAX_IMAGES,
      sizes: [],
    },
    segmentation: { ...capability, subjects: [], profiles: [], maxExtraTerms: FALLBACK_LIMITS.extraTermsMax },
    parts: {
      ...capability,
      strategies: [],
      defaultStrategy: null,
      separableGroups: [],
      regionGroups: [],
      maxViews: FALLBACK_LIMITS.viewsMax,
      defaultViews: FALLBACK_DEFAULT_VIEWS,
      rebuild: false,
    },
    workflow: {
      ...capability,
      catalogEtag: null,
      maxNodes: FALLBACK_LIMITS.maxNodes,
      maxEdges: FALLBACK_LIMITS.maxEdges,
      graphMaxBytes: FALLBACK_LIMITS.graphMaxBytes,
      legacyJobNodeMap: FALLBACK_LEGACY_JOB_NODE_MAP,
    },
    nodes: [],
    meshTuning: { enabled: false, reason: capability.reason },
    partRefinement: { enabled: false, reason: capability.reason },
    models: [],
    taxonomy: { version: 0, groups: [], classes: [] },
    runQueue: null,
  })
}

/** Initial snapshot until the first `/features` answer. */
export const LOADING_FEATURES: FeaturesSnapshot = unavailableSnapshot('loading', {
  available: false,
  code: 'loading',
  reason: 'Checking backend features…',
})

/** `/features` failed and no earlier `ready` snapshot exists (retried on the next poll, `hello` or sync). */
export const ERROR_FEATURES: FeaturesSnapshot = unavailableSnapshot('error', {
  available: false,
  code: 'error',
  reason: 'Could not read backend features. Retrying…',
})

/** `/features` → 404: an older backend with image-to-3D only. */
export const LEGACY_FEATURES: FeaturesSnapshot = unavailableSnapshot('legacy', {
  available: false,
  code: 'legacy_backend',
  reason: 'Needs a newer Img3D backend.',
})

// ------------------------------------------------------------------------------------------------
// Tolerant readers
// ------------------------------------------------------------------------------------------------

type Json = Record<string, unknown>

const isObject = (value: unknown): value is Json => typeof value === 'object' && value !== null && !Array.isArray(value)
const str = (value: unknown): string | null => (typeof value === 'string' ? value : null)
const bool = (value: unknown): boolean => value === true
const finite = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)
const count = (value: unknown, fallback: number): number => {
  const n = finite(value)
  return n !== null && n >= 0 ? Math.floor(n) : fallback
}
const member = <T extends string>(value: unknown, allowed: readonly T[]): T | null =>
  typeof value === 'string' && (allowed as readonly string[]).includes(value) ? (value as T) : null
const list = (value: unknown): unknown[] => (Array.isArray(value) ? value : [])

/** Members of `allowed` in payload order, unknown values dropped. */
function memberList<T extends string>(value: unknown, allowed: readonly T[]): T[] {
  const out: T[] = []
  for (const item of list(value)) {
    const known = member(item, allowed)
    if (known !== null && !out.includes(known)) out.push(known)
  }
  return out
}

function objectList<T>(value: unknown, read: (item: Json) => T | null): T[] {
  const out: T[] = []
  for (const item of list(value)) {
    if (!isObject(item)) continue
    const parsed = read(item)
    if (parsed !== null) out.push(parsed)
  }
  return out
}

/** `{available, code, reason}`; a malformed entry (no boolean `available`) is reported as missing. */
function capability(value: unknown): CapabilityView {
  if (!isObject(value) || typeof value.available !== 'boolean') {
    return { available: false, code: 'missing', reason: MISSING_REASON }
  }
  const available = value.available
  const code = str(value.code)
  if (available) return { available, code: code ?? 'ok', reason: str(value.reason) }
  // DESIGN §6.1 sends a reason with every unavailable entry; tolerate a backend that only sends the code.
  return { available, code: code ?? 'missing', reason: str(value.reason) ?? (code ? `Not available (${code}).` : MISSING_REASON) }
}

function readProfile(item: Json): ProfileOut | null {
  const id = member(item.id, PROFILE_IDS)
  return id === null ? null : { id, ...capability(item) }
}

function readStrategy(item: Json): StrategyOut | null {
  const id = str(item.id)
  if (!id) return null
  return { id, label: str(item.label) ?? id, requires_segmentation: bool(item.requires_segmentation), ...capability(item) }
}

function readGroup(item: Json): PartGroupInfoOut | null {
  const id = str(item.id)
  if (!id) return null
  return {
    id,
    label: str(item.label) ?? id,
    default_kind: member(item.default_kind, GROUP_KINDS) ?? 'region',
    default_selected: bool(item.default_selected),
    note: str(item.note),
  }
}

function readNode(item: Json): FeatureNodeOut | null {
  const type = str(item.type)
  if (!type) return null
  return {
    type,
    title: str(item.title) ?? type,
    category: str(item.category) ?? 'other',
    locked: bool(item.locked),
    hidden_by_default: bool(item.hidden_by_default),
    ...capability(item),
  }
}

/** Models are shown in App settings and used for licence notices; an entry without its core fields is skipped. */
function readModel(item: Json): ModelOut | null {
  const license = item.license
  if (!str(item.id) || !str(item.kind) || !member(item.state, MODEL_UI_STATES) || !isObject(license)) return null
  if (!Array.isArray(license.conditions) || !license.conditions.every((c) => typeof c === 'string')) return null
  // A copy, so freezing the snapshot never freezes the caller's payload.
  return structuredClone(item) as unknown as ModelOut
}

function readTaxonomyClass(item: Json): TaxonomyClassOut | null {
  const value = finite(item.value)
  const id = str(item.id)
  if (value === null || !id) return null
  return {
    value,
    id,
    group: str(item.group) ?? 'other',
    label: str(item.label) ?? id,
    default_kind: member(item.default_kind, CLASS_KINDS) ?? 'region',
    attach_to: list(item.attach_to).filter((v): v is string => typeof v === 'string'),
    symmetric_with: str(item.symmetric_with),
    sided: bool(item.sided),
  }
}

function readSizes(value: unknown): [number, number][] {
  const out: [number, number][] = []
  for (const item of list(value)) {
    if (!Array.isArray(item) || item.length !== 2) continue
    const [w, h] = item.map(finite)
    if (w !== null && h !== null && w > 0 && h > 0) out.push([w, h])
  }
  return out
}

function readJobNodeMap(value: unknown): Readonly<Record<string, string>> {
  if (!isObject(value)) return FALLBACK_LEGACY_JOB_NODE_MAP
  const out: Record<string, string> = {}
  for (const [stage, node] of Object.entries(value)) if (typeof node === 'string') out[stage] = node
  return Object.keys(out).length > 0 ? out : FALLBACK_LEGACY_JOB_NODE_MAP
}

function toggle(value: unknown): { enabled: boolean; reason: string | null } {
  return isObject(value) ? { enabled: bool(value.enabled), reason: str(value.reason) } : { enabled: false, reason: null }
}

/** `GET /features` 200 body → snapshot (status `ready`). Never throws. */
export function normalizeFeatures(raw: unknown): FeaturesSnapshot {
  const root = isObject(raw) ? raw : {}
  const api = isObject(root.api) ? root.api : {}
  const flags = isObject(root.flags) ? root.flags : {}
  const pipeline = isObject(root.pipeline) ? root.pipeline : {}
  const limits = isObject(root.limits) ? root.limits : {}
  const prompt = isObject(pipeline.prompt_to_3d) ? pipeline.prompt_to_3d : {}
  const segmentation = isObject(pipeline.segmentation) ? pipeline.segmentation : {}
  const parts = isObject(pipeline.part_extraction) ? pipeline.part_extraction : {}
  const workflow = isObject(root.workflow) ? root.workflow : {}
  const taxonomy = isObject(root.taxonomy) ? root.taxonomy : {}
  const runs = isObject(root.queues) && isObject(root.queues.runs) ? root.queues.runs : null
  const maxImages = count(prompt.max_images, FALLBACK_MAX_IMAGES)

  return deepFreeze<FeaturesSnapshot>({
    status: 'ready',
    apiVersion: str(api.version),
    flags: {
      partAware: bool(flags.part_aware),
      imageGeneration: bool(flags.image_generation),
      segmentation: bool(flags.segmentation),
      partAnalysis: bool(flags.part_analysis),
      workflows: bool(flags.workflows),
      meshTuning: bool(flags.mesh_tuning),
      partRefinement: bool(flags.part_refinement),
    },
    imageTo3d: capability(pipeline.image_to_3d),
    prompt: {
      ...capability(pipeline.prompt_to_3d),
      maxChars: count(prompt.max_prompt_chars, count(limits.prompt_max_chars, FALLBACK_LIMITS.promptMaxChars)),
      presets: memberList(prompt.presets, PROMPT_PRESETS),
      styles: memberList(prompt.styles, PROMPT_STYLES),
      supportsNegativePrompt: bool(prompt.supports_negative_prompt),
      negativeMaxChars: count(limits.negative_prompt_max_chars, FALLBACK_LIMITS.negativeMaxChars),
      defaultModelId: str(prompt.default_model_id),
      defaultModelState: member(prompt.default_model_state, MODEL_UI_STATES),
      coldStartS: finite(prompt.cold_start_s),
      maxImages: maxImages > 0 ? maxImages : 1,
      sizes: readSizes(prompt.sizes),
    },
    segmentation: {
      ...capability(pipeline.segmentation),
      subjects: memberList(segmentation.subjects, SEG_SUBJECTS),
      profiles: objectList(segmentation.profiles, readProfile),
      maxExtraTerms: count(segmentation.max_extra_terms, count(limits.extra_terms_max, FALLBACK_LIMITS.extraTermsMax)),
    },
    parts: {
      ...capability(pipeline.part_extraction),
      strategies: objectList(parts.strategies, readStrategy),
      defaultStrategy: str(parts.default_strategy),
      separableGroups: objectList(parts.separable_groups, readGroup),
      regionGroups: objectList(parts.region_groups, readGroup),
      maxViews: count(parts.max_views, count(limits.views_max, FALLBACK_LIMITS.viewsMax)),
      defaultViews: count(parts.default_views, FALLBACK_DEFAULT_VIEWS),
      rebuild: bool(parts.rebuild),
    },
    workflow: {
      ...capability(root.workflow),
      catalogEtag: str(workflow.catalog_etag),
      maxNodes: count(workflow.max_nodes, FALLBACK_LIMITS.maxNodes),
      maxEdges: count(workflow.max_edges, FALLBACK_LIMITS.maxEdges),
      graphMaxBytes: count(workflow.graph_max_bytes, count(limits.workflow_graph_max_bytes, FALLBACK_LIMITS.graphMaxBytes)),
      legacyJobNodeMap: readJobNodeMap(workflow.legacy_job_node_map),
    },
    nodes: objectList(root.nodes, readNode),
    meshTuning: toggle(root.mesh_tuning),
    partRefinement: toggle(root.part_refinement),
    models: objectList(root.models, readModel),
    taxonomy: {
      version: count(taxonomy.version, 0),
      groups: list(taxonomy.groups).filter((g): g is string => typeof g === 'string'),
      classes: objectList(taxonomy.classes, readTaxonomyClass),
    },
    runQueue: runs
      ? { pending: count(runs.pending, 0), runningId: str(runs.running_id), maxSize: count(runs.max_size, 0) }
      : null,
  })
}

// ------------------------------------------------------------------------------------------------
// Derived questions
// ------------------------------------------------------------------------------------------------

/** The paused tuner UI is shown only with the build flag AND the backend's approval switch (UI_DESIGN §3.9). */
export function isTunerVisible(buildFlag: boolean, f: FeaturesSnapshot): boolean {
  return buildFlag && f.status === 'ready' && f.meshTuning.enabled === true
}

const USABLE_STATES: ReadonlySet<ModelUiState> = new Set<ModelUiState>(['installed', 'loading', 'loaded'])

const isUsable = (m: ModelOut): boolean => m.approved === true && USABLE_STATES.has(m.state)

/** Text-to-image models the prompt form may offer (approved and installed, loading or in memory). */
export function selectableT2IModels(f: FeaturesSnapshot): readonly ModelOut[] {
  return f.models.filter((m) => m.kind === 'text_to_image' && isUsable(m))
}

/** `separate` default: the separable groups the backend marks `default_selected`, in `/features` order. */
export function defaultSeparate(f: FeaturesSnapshot): readonly string[] {
  return f.parts.separableGroups.filter((g) => g.default_selected).map((g) => g.id)
}

export interface LicenceNotice {
  readonly modelId: string
  readonly name: string
  readonly conditions: readonly string[]
}

/** Usable segmentation models whose licence lists conditions (no model id is hard-coded). */
export function licenceNotices(f: FeaturesSnapshot): readonly LicenceNotice[] {
  return f.models
    .filter((m) => m.kind === 'segmentation' && isUsable(m) && m.license.conditions.length > 0)
    .map((m) => ({ modelId: m.id, name: m.license.name || m.display_name, conditions: m.license.conditions }))
}
