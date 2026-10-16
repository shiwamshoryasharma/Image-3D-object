/**
 * Wire types of the part-aware API additions (DESIGN.md §3.10–§3.12, §4.3–§4.7, §6; UI_DESIGN.md §2.1).
 * Field names are kept in snake_case exactly as sent by the backend.
 *
 * - Responses are `extra="ignore"` server-side: fields the backend adds later are simply not listed here.
 * - Requests are `extra="forbid"`: a key that is not declared below is a 422 `extra_forbidden`.
 * - Unions are string-literal types (TS enums are not allowed here).
 *
 * PURE (UI_DESIGN §1.5): import-free apart from erased `import type`s, so every layer (and Node's strip-types test
 * runner) may import it.
 */
import type {
  GenerationSettings,
  IsoDateTime,
  JobError,
  JobStage,
  JobStatus,
  MeshStats,
  ReferenceImage,
  ReferenceSet,
  ReferenceSlot,
} from './types.ts'

// ------------------------------------------------------------------------------------------------
// Shared run framework (DESIGN §4.3)
// ------------------------------------------------------------------------------------------------

/** Runs reuse the job lifecycle: queued | running | completed | failed | cancelled. */
export type RunStatus = JobStatus
export type RunKind = 'image_generation' | 'segmentation' | 'part_analysis'
export type RunStepStatus = 'pending' | 'running' | 'done' | 'warning' | 'skipped' | 'failed' | 'cancelled'

export interface RunStepOut {
  id: string
  label: string
  status: RunStepStatus
  message: string | null
  /** 0..1 within the step (unlike `WorkflowStepOut.progress`, which is 0..100). */
  progress: number | null
  duration_s: number | null
  details: Record<string, unknown>
}

export interface RunProgressOut {
  /** 0..100; never decreases and is exactly 100 on `completed`. */
  percent: number
  step_id: string | null
  step_index: number
  step_count: number
  message: string | null
  updated_at: IsoDateTime | null
}

export interface RunWarningOut {
  code: string
  message: string
  step_id: string | null
}

/** A model a run actually used (provenance). */
export interface ModelUseOut {
  id: string
  revision: string | null
  license_id: string
}

/** Common shape of every `ig_` / `sg_` / `pa_` run. `options` is the effective (normalized) request. */
export interface RunOut<K extends RunKind, O, R> {
  id: string
  kind: K
  status: RunStatus
  created_at: IsoDateTime
  started_at: IsoDateTime | null
  finished_at: IsoDateTime | null
  workflow_id: string | null
  options: O
  models: ModelUseOut[]
  progress: RunProgressOut
  steps: RunStepOut[]
  warnings: RunWarningOut[]
  result: R | null
  error: JobError | null
}

/** Envelope of every list route (`GET /models`, `/image-generations`, `/segmentations`, `/workflows`, …; DESIGN §6). */
export interface ItemsOut<T> {
  items: T[]
}

// ------------------------------------------------------------------------------------------------
// Discovery (DESIGN §5.1, §6.1)
// ------------------------------------------------------------------------------------------------

export type ModelKind =
  | 'text_to_image'
  | 'segmentation'
  | 'part_extraction'
  | 'segmentation_3d'
  | 'completion'
  | 'part_generation'

export type ModelUiState = 'not_installed' | 'installed' | 'loading' | 'loaded' | 'error' | 'unavailable'

export type CommercialUse =
  | 'allowed'
  | 'allowed_with_conditions'
  | 'revenue_capped'
  | 'non_commercial'
  | 'research_only'
  | 'unknown'

/** One vocabulary for every `code` in `/features` (pipeline, profiles, strategies, nodes) and `ModelOut.reason_code`. */
export type ReasonCode =
  | 'ok'
  | 'unsupported'
  | 'disabled'
  | 'not_approved'
  | 'license_blocked'
  | 'vendored_source_missing'
  | 'missing_dependency'
  | 'not_installed'
  | 'incompatible'
  | 'insufficient_vram'
  | 'error'
  | 'not_implemented'
  | 'engine_unavailable'

/** Every pipeline, profile, strategy and node entry carries all three fields; `reason` is null when available. */
export interface CapabilityOut {
  available: boolean
  /** A {@link ReasonCode}; unknown future codes are tolerated. */
  code: string
  reason: string | null
}

export interface PartGroupInfoOut {
  id: string
  label: string
  default_kind: 'physical' | 'physical_deferred' | 'region'
  default_selected: boolean
  note: string | null
}

export interface StrategyOut extends CapabilityOut {
  id: string
  label: string
  requires_segmentation: boolean
}

export interface ProfileOut extends CapabilityOut {
  id: 'baseline' | 'anime' | 'realistic'
}

export interface FeatureNodeOut extends CapabilityOut {
  type: string
  title: string
  category: string
  locked: boolean
  hidden_by_default: boolean
}

export interface LicenseOut {
  id: string
  name: string
  url: string
  commercial: CommercialUse
  gated: boolean
  requires_acceptance: boolean
  accepted?: boolean
  conditions: string[]
  attribution?: string | null
}

/** A registry model (`GET /models`, `/features.models`). Read-only: the UI never installs models. */
export interface ModelOut {
  id: string
  kind: ModelKind
  roles: string[]
  display_name: string
  state: ModelUiState
  /** A {@link ReasonCode}. */
  reason_code: string
  reason: string | null
  approved: boolean
  is_default: boolean
  repo_id: string | null
  revision: string | null
  size_bytes: number | null
  vram_estimate_gb: number | null
  ram_gb: number | null
  cold_load_s: number | null
  license: LicenseOut
  dependencies: { distribution: string; requirement: string; installed: string | null; ok: boolean }[]
  vendored_source: string | null
  local_path: string | null
  install: { supported: boolean; hint: string | null; bytes_done: number | null; bytes_total: number | null }
  capabilities: Record<string, unknown>
  loaded_device: 'cpu' | 'cuda' | null
  last_used_at: IsoDateTime | null
  hidden_by_default: boolean
}

export interface TaxonomyClassOut {
  value: number
  id: string
  group: string
  label: string
  default_kind: 'base' | 'physical' | 'physical_deferred' | 'region'
  attach_to: string[]
  symmetric_with: string | null
  sided: boolean
}

/** `GET /features` and the `features.updated` payload (`img3d.features/1`). */
export interface FeaturesOut {
  schema: 'img3d.features/1'
  api: { version: string; workflows: boolean; part_analyses: boolean; image_generations: boolean; segmentations: boolean }
  flags: {
    part_aware: boolean
    image_generation: boolean
    segmentation: boolean
    part_analysis: boolean
    workflows: boolean
    mesh_tuning: boolean
    part_refinement: boolean
    persist_generation_intermediates: boolean
  }
  pipeline: {
    image_to_3d: CapabilityOut
    prompt_to_3d: CapabilityOut & {
      max_prompt_chars: number
      presets: ('character' | 'object' | 'raw')[]
      styles: ('realistic' | 'stylized' | 'anime')[]
      supports_negative_prompt: boolean
      default_model_id: string | null
      default_model_state: ModelUiState | null
      cold_start_s: number | null
      max_images: number
      sizes: [number, number][]
      selection_modes: string[]
    }
    segmentation: CapabilityOut & {
      subjects: string[]
      profiles: ProfileOut[]
      default_profile: string
      max_extra_terms: number
      before_reconstruction: boolean
    }
    part_extraction: CapabilityOut & {
      strategies: StrategyOut[]
      default_strategy: string | null
      separable_groups: PartGroupInfoOut[]
      region_groups: PartGroupInfoOut[]
      max_views: number
      default_views: number
      rebuild: boolean
      per_part_glbs: string
    }
  }
  workflow: CapabilityOut & {
    catalog_url: string
    catalog_etag: string
    max_nodes: number
    max_edges: number
    graph_max_bytes: number
    selection_modes: string[]
    legacy_job_node_map: Partial<Record<JobStage, string>>
  }
  nodes: FeatureNodeOut[]
  mesh_tuning: { enabled: boolean; reason: string | null }
  part_refinement: { enabled: boolean; reason: string | null }
  /** Full `ModelOut` entries; `hidden_by_default` models are excluded (use `GET /models?include_hidden=true`). */
  models: ModelOut[]
  taxonomy: { version: number; groups: string[]; classes: TaxonomyClassOut[] }
  queues: { runs: { pending: number; running_id: string | null; running_kind: RunKind | null; max_size: number } }
  limits: {
    prompt_max_chars: number
    negative_prompt_max_chars: number
    extra_terms_max: number
    views_max: number
    workflow_graph_max_bytes: number
    max_parts: number
  }
}

// ------------------------------------------------------------------------------------------------
// Image generations (DESIGN §4.4, §6.2)
// ------------------------------------------------------------------------------------------------

export type PromptPreset = 'character' | 'object' | 'raw'
export type PromptStyle = 'realistic' | 'stylized' | 'anime'

/** `POST /image-generations` body; also `WorkflowSource.prompt.generation` (where `num_images` must be 1). */
export interface ImageGenerationRequest {
  prompt: string
  negative_prompt?: string | null
  model_id?: string | null
  preset?: PromptPreset
  style?: PromptStyle | null
  part_hint?: boolean
  /** 1..4 */
  num_images?: number
  /** 0..2147483647; candidate i uses (seed + i) mod 2^31. */
  seed?: number
  randomize_seed?: boolean
  width?: number | null
  height?: number | null
  steps?: number | null
  guidance?: number | null
}

export interface ImageCandidateOut {
  index: number
  seed: number
  width: number
  height: number
  /** Payload URL (F route): resolve with `fileUrl`, never build it. */
  url: string
  thumbnail_url: string
  /** `issues` are `t2i_*` warning codes (DESIGN §4.4.5). */
  readiness: { ok: boolean; issues: string[]; method: string }
  used_as: { reference_set_id: string; image_id: string }[]
}

export type ImageGenerationOut = RunOut<
  'image_generation',
  Required<Pick<ImageGenerationRequest, 'prompt' | 'preset' | 'part_hint' | 'num_images' | 'seed'>> & {
    effective_prompt: string
    negative_prompt: string | null
    model_id: string
    style: PromptStyle | null
    width: number
    height: number
    steps: number
    guidance: number
    expected_terms: string[]
    final_prompt_sha256: string
  },
  { candidates: ImageCandidateOut[]; timings: Record<string, number> }
>

/** `POST /image-generations/{id}/candidates/{index}/use` body (server defaults: slot "front", make_primary true). */
export interface UseCandidateRequest {
  reference_set_id?: string | null
  slot?: ReferenceSlot
  make_primary?: boolean
  label?: string | null
}

/** Same body as a reference image upload (contract §7.3). */
export interface UseCandidateOut {
  image: ReferenceImage
  set: ReferenceSet
}

/** Declared in `types.ts` (append-only block) so that `types.ts` stays import-free. */
export type { ImageSourceOut } from './types.ts'

// ------------------------------------------------------------------------------------------------
// Segmentations (DESIGN §4.5, §6.3)
// ------------------------------------------------------------------------------------------------

export type SegProfile = 'auto' | 'baseline' | 'anime' | 'realistic'
export type SegSubject = 'auto' | 'anime_character' | 'realistic_human' | 'object'

/** Shared by `POST /segmentations`, `PartAnalysisOptions.segmentation` and `WorkflowRequest.segmentation`. */
export interface SegmentationOptions {
  profile?: SegProfile
  subject?: SegSubject
  /** null = the whole taxonomy. */
  classes?: string[] | null
  extra_terms?: string[]
  use_prompt_terms?: boolean
  head_pass?: boolean
  /** 0.05..0.95 */
  min_confidence?: number
}

export type SegmentationSource =
  | { kind: 'reference_image'; reference_set_id: string; image_id: string }
  | { kind: 'job'; job_id: string }

export interface SegmentationRequest {
  source: SegmentationSource
  options?: SegmentationOptions
}

export interface SegLabelOut {
  value: number
  category: string
  group: string
  area_px: number
  confidence: number
  bbox: [number, number, number, number]
  side: 'left' | 'right' | 'center' | null
  sources: string[]
  mask_url: string
}

export type SegmentationOut = RunOut<
  'segmentation',
  SegmentationRequest,
  {
    space: 'conditioning' | 'source'
    frame: Record<string, unknown> | null
    subject: SegSubject
    profile: SegProfile
    labels: SegLabelOut[]
    annotations: Record<string, unknown>
    expected_terms_missing: string[]
    overlay_url: string
    labels_url: string
    timings: Record<string, number>
  }
>

// ------------------------------------------------------------------------------------------------
// Part analyses (DESIGN §3.10, §4.6, §6.4)
// ------------------------------------------------------------------------------------------------

export type SeparableGroupId = 'eyes' | 'eyebrows' | 'eyelashes' | 'hair' | 'clothes' | 'accessories' | 'detachable'
export type PartKind = 'base' | 'physical' | 'physical_deferred' | 'region'

/** `parts.export`, for workflows and direct analyses alike (there is no top-level `export`). */
export interface PartExportOptions {
  /** Default false. */
  per_part_glbs?: boolean
  /** Default true. */
  previews?: boolean
}

/** `POST /jobs/{id}/part-analyses` body and `WorkflowRequest.parts`. No `assembled_glb`: model_parts.glb is always written. */
export interface PartAnalysisOptions {
  strategy?: string | null
  /** Direct analyses only; MUST be absent/null inside workflows (422 `conflicting_fields`). */
  segmentation_id?: string | null
  segmentation?: SegmentationOptions | null
  /** DESIGN §2.2 group ids; unselected groups become regions by policy. */
  separate?: string[]
  /** 0..24 */
  views?: number
  /** null (default) → max(50, round(1e-4·F)) server-side. */
  min_part_faces?: number | null
  /** 1..256 */
  max_parts?: number
  split_shells?: boolean
  export?: PartExportOptions
}

export interface PartRefinementOut {
  status: string
  locked: boolean
  eligible: boolean
  needs: string[]
}

export interface PartSummaryOut {
  id: string
  label: string
  category: string
  group: string
  kind: PartKind
  node_name: string | null
  region_value: number | null
  faces: number
  confidence: number | null
  side: 'left' | 'right' | 'center' | null
  /** null for regions and deferred parts. */
  model_url: string | null
  /** null for regions and deferred parts. */
  download_url: string | null
  mask_url: string | null
  thumbnail_url: string | null
  refinement: PartRefinementOut
}

export interface PartAnalysisResultOut {
  status: 'completed' | 'partial'
  manifest_url: string
  /** Never null (DESIGN §4.6.7). */
  assembled_url: string
  /** Never null (DESIGN §4.6.7). */
  download_url: string
  zip_url: string
  /** null for the `components` strategy. */
  overlay_url: string | null
  face_labels_url: string
  /** `{}` when the previews step is skipped. */
  preview_urls: Partial<Record<'front' | 'back' | 'left' | 'right', string>>
  region_attribute: string
  part_count: number
  physical_count: number
  deferred_count: number
  region_count: number
  /** null for the `components` strategy. */
  camera_fit_iou: number | null
  unlabelled_face_ratio: number
  partner_filled_ratio: number
  /** null when no part was cut. */
  cut_partner_consistency: number | null
  parts: PartSummaryOut[]
  stats: MeshStats
  timings: Record<string, number>
}

export type PartAnalysisOut = RunOut<'part_analysis', PartAnalysisOptions, PartAnalysisResultOut> & { job_id: string }

/** Subset of a manifest `img3d.parts/1` part record the UI reads (DESIGN §3.10.1); everything else is ignored. */
export interface PartRecordOut {
  id: string
  category: string
  group: string
  label: string
  kind: PartKind
  side: 'left' | 'right' | 'center' | null
  image_side: string | null
  parent: string | null
  attach_to: string | null
  host: string | null
  symmetric_with: string | null
  node_name: string | null
  mesh_index: number | null
  region: { attribute: string; value: number; face_count: number }
  files: { glb: string | null; mask: string | null; thumbnail: string | null }
  stats: { faces: number; vertices: number; area: number; bbox_min: number[]; bbox_max: number[]; centroid: number[] }
  geometry: {
    status: string
    open_boundary_loops: number
    watertight: boolean
    occluded_geometry_missing: boolean
    components: number
    source: string
    tags: string[]
    /** Always present; non-empty only on the base record (DESIGN §3.10.1, §3.11). */
    floaters: { component: number; faces: number; centroid: number[] }[]
  }
  confidence: number | null
  expected: boolean
  refinement: PartRefinementOut & { lock_reason?: string }
}

export interface RelationOut {
  id: string
  type: string
  a: string
  b: string
  via: string | null
  metrics: Record<string, unknown>
  source: string
  confidence: number
}

/**
 * Subset of the manifest `img3d.parts/1` the UI reads (DESIGN §3.10, §3.10.1); everything else is ignored.
 * `buffers[]` (face_labels, face_owner, face_part, face_flags, …) are listed in the manifest but never fetched by the UI.
 */
export interface PartsManifestOut {
  schema: string
  analysis_id: string
  job_id: string
  taxonomy_version: number
  region_attribute: string
  label_table: { value: number; category: string }[]
  meshes: { index: number; part_id: string; node_name: string; faces: number }[]
  parts: PartRecordOut[]
  relations: RelationOut[]
  seams: { id: string; a: string; b: string; host_opening: boolean }[]
  expectations: { source: string | null; expected_classes: string[]; missing: string[] }
  refinement: { enabled: boolean; policy: string; consumers: unknown[] }
  summary: {
    part_count: number
    physical_count: number
    deferred_count: number
    region_count: number
    unlabelled_face_ratio: number
    partner_filled_ratio: number
    cut_partner_consistency: number | null
    camera_fit_iou: number | null
  }
  warnings: { code: string; message: string; part_id: string | null }[]
}

// ------------------------------------------------------------------------------------------------
// Workflows (DESIGN §4.7, §6.5)
// ------------------------------------------------------------------------------------------------

export type PortTypeId = 'image_set' | 'segmentation' | 'mesh' | 'part_set'

export type WorkflowSource =
  | { kind: 'image'; reference_set_id: string; primary_image_id: string | null }
  | { kind: 'prompt'; generation: ImageGenerationRequest }
  | { kind: 'job'; job_id: string }

export interface WorkflowGraphNode {
  id: string
  type: string
  params: Record<string, unknown>
}

export interface WorkflowGraphEdge {
  from: { node: string; port: string }
  to: { node: string; port: string }
}

export interface WorkflowGraph {
  schema: 'img3d.workflow_graph'
  version: 1
  nodes: WorkflowGraphNode[]
  edges: WorkflowGraphEdge[]
}

/**
 * `POST /workflows` and `POST /workflows/validate` body: the simple form or `graph` (+ opaque `layout`), never both.
 * There is NO top-level `export` (422 `extra_forbidden`): export options live in `parts.export`.
 */
export interface WorkflowRequest {
  name?: string | null
  source?: WorkflowSource
  /** Only together with `parts` (else 422 `conflicting_fields`). */
  segmentation?: SegmentationOptions | null
  reconstruction?: { settings: GenerationSettings } | null
  /** `parts.export` carries the export options; `parts.segmentation_id` is never sent. */
  parts?: PartAnalysisOptions | null
  graph?: WorkflowGraph | null
  layout?: Record<string, unknown> | null
}

export const GRAPH_ISSUE_CODES = [
  'graph_too_large',
  'unknown_node_type',
  'unknown_port',
  'type_mismatch',
  'port_already_connected',
  'missing_required_input',
  'no_source',
  'multiple_sources',
  'cycle',
  'too_many_instances',
  'node_locked',
  'unsupported_topology',
  'capability_unavailable',
  'feature_disabled',
  'invalid_param',
  'not_found',
  'no_sink',
] as const
export type GraphIssueCode = (typeof GRAPH_ISSUE_CODES)[number]

export interface GraphIssueOut {
  severity: 'error' | 'warning'
  code: GraphIssueCode
  message: string
  node_id: string | null
  port: string | null
  edge_index: number | null
  details: Record<string, unknown> | null
}

/** `POST /workflows/validate` response. For `mode: 'job'` the client posts its own `CreateJobRequest` (no copy here). */
export interface ValidationReport {
  valid: boolean
  issues: GraphIssueOut[]
  mode: 'job' | 'workflow' | null
  request: WorkflowRequest | null
}

export type WorkflowStepId =
  | 'text_to_image'
  | 'import_image'
  | 'segmentation'
  | 'reconstruction'
  | 'part_extraction'
  | 'export'

export interface WorkflowStepOut {
  id: WorkflowStepId
  node: string
  /** The graph node that owns the step; null for steps implicit in a simple-form request. */
  node_id: string | null
  status: 'pending' | 'running' | 'done' | 'failed' | 'skipped' | 'cancelled'
  message?: string | null
  /** 0..100 (unlike `RunStepOut.progress`, which is 0..1). */
  progress?: number | null
  /** len(child.warnings), default 0; `done` with > 0 → node state `warning` (DESIGN §4.7.5). */
  warning_count?: number
  resource: {
    kind: 'image_generation' | 'reference_image' | 'segmentation' | 'job' | 'part_analysis'
    id: string
    reference_set_id?: string
    exists: boolean
  } | null
  artifacts: { role: string; url: string }[]
}

export interface WorkflowOut {
  id: string
  name: string | null
  status: RunStatus
  created_at: IsoDateTime
  started_at: IsoDateTime | null
  finished_at: IsoDateTime | null
  /** Normalized simple form (without graph/layout). */
  request: WorkflowRequest
  graph_stored: boolean
  current_step: WorkflowStepId | null
  progress: { percent: number; message: string | null; updated_at: IsoDateTime | null }
  steps: WorkflowStepOut[]
  outputs: {
    reference_set_id: string | null
    image_id: string | null
    segmentation_id: string | null
    job_id: string | null
    part_analysis_id: string | null
    model_url: string | null
    parts_model_url: string | null
    manifest_url: string | null
  }
  error: JobError | null
}

/** `error.details` when `error.code === 'step_failed'` (DESIGN §4.7.4 `StepFailedDetails`); read it via a type guard. */
export interface StepFailedDetails {
  step: WorkflowStepId
  child_kind: 'image_generation' | 'segmentation' | 'job' | 'part_analysis'
  child_id: string | null
  /** `{code, message, details}`: the child's own error, or the refused create call's. */
  child_error: JobError
}

export interface CatalogPortIn {
  id: string
  label: string
  accepts: PortTypeId[]
  required: boolean
  multiple: boolean
}

export interface CatalogNodeTypeOut extends CapabilityOut {
  type: string
  version: number
  category: string
  title: string
  inputs: CatalogPortIn[]
  outputs: { id: string; label: string; type: PortTypeId }[]
  params_schema: { properties?: Record<string, unknown>; [key: string]: unknown }
  defaults: Record<string, unknown>
  compiles_to: string | null
  max_instances: number
  locked: boolean
  hidden_by_default: boolean
}

/** `GET /workflows/catalog` (ETag-cached). */
export interface CatalogOut {
  schema: 'img3d.workflow_catalog/1'
  etag: string
  port_types: { id: PortTypeId; label: string }[]
  node_types: CatalogNodeTypeOut[]
  templates: { id: string; title: string; graph: WorkflowGraph }[]
  legacy_job_node_map: Partial<Record<JobStage, string>>
  limits: { max_nodes: number; max_edges: number; graph_max_bytes: number }
}

// ------------------------------------------------------------------------------------------------
// WebSocket (DESIGN §6.6; envelope `{type, ts, data}`)
// ------------------------------------------------------------------------------------------------

export interface PartAwareEventMap {
  'features.updated': FeaturesOut
  'image_generation.updated': ImageGenerationOut
  'segmentation.updated': SegmentationOut
  /** Job-scoped server-side: delivered because the client connects without `?job_id=`. */
  'part_analysis.updated': PartAnalysisOut
  'workflow.updated': WorkflowOut
}

export type PartAwareWsMessage = {
  [K in keyof PartAwareEventMap]: { type: K; ts: IsoDateTime; data: PartAwareEventMap[K] }
}[keyof PartAwareEventMap]
