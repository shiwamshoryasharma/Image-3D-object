/**
 * Types mirroring the Img3D REST/WebSocket contract (api-contract.md §3, §4, §7, §8).
 * Field names are kept in snake_case exactly as sent by the backend.
 * Enumerations are string-literal unions backed by `as const` tuples (TS enums are not allowed here).
 */

// ------------------------------------------------------------------------------------------------
// Domain enums (§3)
// ------------------------------------------------------------------------------------------------

export const REFERENCE_SLOTS = ['front', 'back', 'left', 'right', 'face_front', 'face_side', 'detail'] as const
export type ReferenceSlot = (typeof REFERENCE_SLOTS)[number]

/** Slots that hold at most one image. */
export const SINGLE_SLOTS = ['front', 'back', 'left', 'right', 'face_front', 'face_side'] as const
export type SingleSlot = (typeof SINGLE_SLOTS)[number]

/** Order used to auto-promote a new primary image. */
export const SLOT_PRIMARY_PRIORITY = [
  'front',
  'face_front',
  'left',
  'right',
  'back',
  'face_side',
  'detail',
] as const satisfies readonly ReferenceSlot[]

export const JOB_STATUSES = ['queued', 'running', 'completed', 'failed', 'cancelled'] as const
export type JobStatus = (typeof JOB_STATUSES)[number]

export const TERMINAL_STATUSES: ReadonlySet<JobStatus> = new Set<JobStatus>(['completed', 'failed', 'cancelled'])

export const JOB_STAGES = [
  'queued',
  'waiting_for_model',
  'preprocessing',
  'conditioning',
  'sparse_structure',
  'shape',
  'texture',
  'decoding',
  'exporting',
  'finalizing',
  'done',
] as const
export type JobStage = (typeof JOB_STAGES)[number]

export const ENGINE_STATES = ['not_loaded', 'loading', 'ready', 'error', 'unavailable'] as const
export type EngineState = (typeof ENGINE_STATES)[number]

export const REFERENCE_MODES = ['primary_only', 'multi_view'] as const
export type ReferenceMode = (typeof REFERENCE_MODES)[number]

export type Resolution = 512 | 1024 | 1536
export type TextureSize = 512 | 1024 | 2048 | 4096

/** ISO-8601 UTC timestamp (`...Z`). */
export type IsoDateTime = string

// ------------------------------------------------------------------------------------------------
// Errors (§4)
// ------------------------------------------------------------------------------------------------

export interface ApiErrorPayload {
  code: string
  message: string
  details: unknown
  request_id: string | null
}

export interface ApiErrorBody {
  error: ApiErrorPayload
}

// ------------------------------------------------------------------------------------------------
// Auth (§7.1)
// ------------------------------------------------------------------------------------------------

export interface AuthStatus {
  auth_required: boolean
  authenticated: boolean
  username: string | null
}

export interface LoginRequest {
  username: string
  password: string
}

export interface LoginResponse {
  token: string
  token_type: 'bearer'
  expires_at: IsoDateTime
}

// ------------------------------------------------------------------------------------------------
// System (§7.2)
// ------------------------------------------------------------------------------------------------

export interface HealthResponse {
  status: 'ok'
  app: string
  version: string
  uptime_seconds: number
  time: IsoDateTime
}

export interface AppInfo {
  name: string
  version: string
  environment: 'development' | 'production'
}

export interface LoadProgress {
  loaded: number
  total: number
  current: string | null
}

export interface PolygonTargetRange {
  min: number
  max: number
  default: number
}

export interface EngineCapabilities {
  resolutions: Resolution[]
  texture_sizes: TextureSize[]
  polygon_target: PolygonTargetRange
  max_reference_images: number
  reference_modes: ReferenceMode[]
  supports_cancel: boolean
  supports_texture: boolean
}

export interface EngineInfo {
  name: string
  state: EngineState
  message: string | null
  load_progress: LoadProgress | null
  error: string | null
  loaded_at: IsoDateTime | null
  details: Record<string, unknown>
  capabilities: EngineCapabilities
}

export interface DeviceInfo {
  cuda_available: boolean
  name: string | null
  compute_capability: string | null
  vram_total_mb: number | null
  vram_used_mb: number | null
  vram_free_mb: number | null
  torch_version: string | null
  cuda_version: string | null
}

export interface QueueInfo {
  pending: number
  running_job_id: string | null
  max_size: number
}

export interface SystemLimits {
  max_upload_mb: number
  allowed_mime_types: string[]
  max_detail_images: number
  min_image_side: number
  slots: ReferenceSlot[]
}

export interface SystemDefaults {
  resolution: Resolution
  seed: number
  randomize_seed: boolean
  polygon_target: number
  texture_size: TextureSize
  remove_background: boolean
  reference_mode: ReferenceMode
}

export interface SystemStatus {
  app: AppInfo
  engine: EngineInfo
  /** `null` when torch is not importable on the backend. */
  device: DeviceInfo | null
  queue: QueueInfo
  limits: SystemLimits
  defaults: SystemDefaults
  auth_required: boolean
}

// ------------------------------------------------------------------------------------------------
// Reference sets (§7.3)
// ------------------------------------------------------------------------------------------------

export interface ReferenceImageUrls {
  file: string
  thumbnail: string
}

export interface ReferenceImage {
  id: string
  set_id: string
  slot: ReferenceSlot
  label: string | null
  /** Contract example shows a string; the backend schema allows `null` (e.g. uploads without a filename). */
  original_filename: string | null
  mime_type: string
  width: number
  height: number
  has_alpha: boolean
  size_bytes: number
  sha256: string
  is_primary: boolean
  created_at: IsoDateTime
  urls: ReferenceImageUrls
}

export type SlotLimits = Record<ReferenceSlot, number>

export interface ReferenceSet {
  id: string
  name: string | null
  created_at: IsoDateTime
  updated_at: IsoDateTime
  primary_image_id: string | null
  /** Ordered by slot order then `created_at`. */
  images: ReferenceImage[]
  slot_limits: SlotLimits
}

export interface ReferenceSetList {
  items: ReferenceSet[]
}

export interface CreateReferenceSetRequest {
  name?: string | null
}

export interface UploadReferenceImageResponse {
  image: ReferenceImage
  set: ReferenceSet
}

export interface UpdateReferenceImageRequest {
  slot?: ReferenceSlot
  label?: string | null
}

export interface SetPrimaryImageRequest {
  image_id: string
}

// ------------------------------------------------------------------------------------------------
// Jobs (§7.4)
// ------------------------------------------------------------------------------------------------

export interface SamplerSettings {
  /** 1..50 */
  steps?: number
  /** 0..10 */
  guidance_strength?: number
  /** 0..1 */
  guidance_rescale?: number
  /** 1..6 */
  rescale_t?: number
}

export const SAMPLER_KEYS = ['sparse_structure', 'shape', 'texture'] as const
export type SamplerKey = (typeof SAMPLER_KEYS)[number]

export type AdvancedSettings = Partial<Record<SamplerKey, SamplerSettings | null>>

/** Request body settings — every field optional, the backend fills defaults. */
export interface GenerationSettings {
  resolution?: Resolution
  seed?: number
  randomize_seed?: boolean
  polygon_target?: number
  texture_size?: TextureSize
  remove_background?: boolean
  reference_mode?: ReferenceMode
  advanced?: AdvancedSettings | null
}

/** Settings echoed on a Job: as submitted, normalized with defaults. */
export interface NormalizedGenerationSettings {
  resolution: Resolution
  seed: number
  randomize_seed: boolean
  polygon_target: number
  texture_size: TextureSize
  remove_background: boolean
  reference_mode: ReferenceMode
  advanced?: AdvancedSettings | null
}

export interface CreateJobRequest {
  reference_set_id: string
  primary_image_id: string | null
  settings: GenerationSettings
}

export interface JobReference {
  image_id: string
  slot: ReferenceSlot
  is_primary: boolean
}

export interface JobProgress {
  stage: JobStage
  stage_label: string
  stage_index: number
  stage_count: number
  step: number | null
  total_steps: number | null
  percent: number
  message: string | null
  elapsed_seconds: number | null
  eta_seconds: number | null
  updated_at: IsoDateTime
}

export interface JobError {
  code: string
  message: string
  details: unknown
}

export type Vec3 = [number, number, number]

export interface MeshStats {
  vertices: number
  faces: number
  materials: number
  textures: number
  texture_size: number | null
  has_uv: boolean
  bbox_min: Vec3
  bbox_max: Vec3
  dimensions: Vec3
  file_size_bytes: number
}

/** Seconds per stage (keys are JobStage values) plus `total`. Keys may be missing. */
export type JobTimings = { [key in JobStage | 'total']?: number }

export interface JobResult {
  model_url: string
  download_url: string
  preview_url: string | null
  stats: MeshStats
  timings: JobTimings
  seed: number
  resolution: Resolution
  effective_resolution: number
  texture_size: TextureSize
  polygon_target: number
}

export interface Job {
  id: string
  status: JobStatus
  engine: string
  created_at: IsoDateTime
  started_at: IsoDateTime | null
  finished_at: IsoDateTime | null
  reference_set_id: string
  primary_image_id: string | null
  references: JobReference[]
  settings: NormalizedGenerationSettings
  /** Resolved seed actually used (set at creation). */
  seed: number
  /** 1-based while queued, else null. */
  queue_position: number | null
  progress: JobProgress
  error: JobError | null
  result: JobResult | null
}

export interface JobList {
  items: Job[]
}

export interface ListJobsParams {
  limit?: number
  status?: JobStatus
}

// ------------------------------------------------------------------------------------------------
// Blender runtime (Auto Mesh Tuner DESIGN.md §2, §6)
// ------------------------------------------------------------------------------------------------

export const BLENDER_RUNTIME_MODES = ['auto', 'system', 'embedded'] as const
export type BlenderRuntimeMode = (typeof BLENDER_RUNTIME_MODES)[number]

export const BLENDER_RUNTIME_STATES = ['detecting', 'ready', 'needs_download', 'downloading', 'error'] as const
export type BlenderRuntimeState = (typeof BLENDER_RUNTIME_STATES)[number]

export type BlenderInstallKind = 'system' | 'embedded'
export type BlenderVersionCycle = 'release' | 'rc' | 'beta' | 'alpha'

/** One detected Blender installation (`BlenderInstallOut`). Version fields are null when the probe failed. */
export interface BlenderInstall {
  id: string
  executable: string
  kind: BlenderInstallKind
  /** Where it was found: "program_files", "registry_uninstall", "path", "embedded_dir", … */
  origins: string[]
  version: string | null
  /** "5.2.2 LTS", "5.3.0 Beta". */
  version_label: string | null
  version_tuple: [number, number, number] | null
  cycle: BlenderVersionCycle | null
  is_lts: boolean | null
  is_stable: boolean | null
  build_hash?: string | null
  build_date?: string | null
  architecture: string | null
  /** Executed and its version parsed. */
  valid: boolean
  /** valid ∧ version ≥ min ∧ runnable architecture ∧ console executable. */
  compatible: boolean
  incompatible_reason: string | null
  /** Probe failure detail. */
  error: string | null
  probe_ms?: number | null
  selected: boolean
}

export const BLENDER_DOWNLOAD_STATES = [
  'idle',
  'resolving',
  'downloading',
  'verifying',
  'extracting',
  'validating',
  'completed',
  'failed',
  'cancelled',
] as const
export type BlenderDownloadState = (typeof BLENDER_DOWNLOAD_STATES)[number]

/** Portable Blender download progress (`DownloadOut`). */
export interface BlenderDownload {
  state: BlenderDownloadState
  version: string | null
  file_name: string | null
  url: string | null
  bytes_done: number
  bytes_total: number | null
  percent: number | null
  speed_bps: number | null
  eta_s: number | null
  resumed: boolean
  /** sha256 verified against the official checksum file; null until verified. */
  verified: boolean | null
  error: string | null
  error_code: string | null
  install_dir: string | null
  started_at: IsoDateTime | null
  updated_at: IsoDateTime
}

/** Official portable release resolved from download.blender.org (`BlenderReleaseOut`). */
export interface BlenderRelease {
  version: string
  series: string
  file_name: string
  url: string
  size_bytes: number | null
  sha256: string | null
  checksum_url: string | null
  architecture: string
  is_lts: boolean | null
  release_date: string | null
  /** Host serving the ZIP (backend addition, e.g. "download.blender.org"). */
  host?: string | null
}

/** `BlenderRuntimeOut` — full runtime snapshot (REST and `blender.updated`). */
export interface BlenderRuntimeStatus {
  state: BlenderRuntimeState
  mode: BlenderRuntimeMode
  /** Minimum supported version, e.g. "5.1". */
  min_version: string
  selected: BlenderInstall | null
  selection_reason: string | null
  /** All detected installs, version descending. */
  installations: BlenderInstall[]
  /** User prompt (exact backend wording), e.g. the needs_download question. */
  message: string | null
  warnings: string[]
  runtime_root: string
  can_download: boolean
  download: BlenderDownload | null
  release: BlenderRelease | null
  last_scan_at: IsoDateTime | null
  scan_duration_ms: number | null
  error: string | null
}

export interface BlenderSelectionRequest {
  mode: BlenderRuntimeMode
  executable?: string | null
}

export interface BlenderDownloadRequest {
  confirm: true
  version?: string | null
}

// ------------------------------------------------------------------------------------------------
// Auto Mesh Tuner runs (DESIGN.md §3, §4, §6)
// ------------------------------------------------------------------------------------------------

export const TUNING_PRESETS = ['conservative', 'balanced', 'strong'] as const
export type TuningPreset = (typeof TUNING_PRESETS)[number]

export const TUNING_SYMMETRY_MODES = ['auto', 'off', 'repair', 'symmetrize'] as const
export type TuningSymmetry = (typeof TUNING_SYMMETRY_MODES)[number]

export type TuningSubdivision = 'auto' | 'off' | 'modifier'

/** Fully resolved tuning options (as echoed on a run: effective values). */
export interface TuningOptionValues {
  preset: TuningPreset
  remove_inner_shell: boolean
  fill_holes: boolean
  /** 8..5000 */
  max_hole_edges: number
  remove_islands: boolean
  /** 0..0.05 (fraction of total faces) */
  island_min_ratio: number
  /** 0..1 feature-preserving denoise strength. */
  smoothing: number
  refine_face: boolean
  eye_correction: boolean
  symmetry: TuningSymmetry
  refine_density: boolean
  quads: boolean
  subdivision: TuningSubdivision
  optimize_rig: boolean
  render_previews: boolean
  save_blend: boolean
}

/** Request body of `POST /jobs/{id}/tunings`: every field optional, the preset sets the defaults. */
export type TuningOptions = Partial<TuningOptionValues>

export const TUNING_STEP_STATUSES = ['pending', 'running', 'done', 'warning', 'skipped', 'failed'] as const
export type TuningStepStatus = (typeof TUNING_STEP_STATUSES)[number]

export interface TuningStep {
  /** load · analyze · geometry_repair · face_refinement · topology_tuning · rig_optimization · validation · export */
  id: string
  label: string
  status: TuningStepStatus
  message: string | null
  duration_s: number | null
}

/** Before/after mesh metric. Data-driven: new keys need no frontend change. */
export interface TuningMetric {
  key: string
  label: string
  unit: string | null
  before: number | null
  after: number | null
  better: 'lower' | 'higher' | 'neutral'
}

export type TuningCheckStatus = 'pass' | 'warn' | 'fail'

export interface TuningValidationCheck {
  id: string
  label: string
  status: TuningCheckStatus
  value: number | string | boolean | null
  threshold: number | string | boolean | null
  message: string | null
}

export interface TuningValidation {
  passed: boolean
  checks: TuningValidationCheck[]
}

export interface TuningProgress {
  /** 0..100 */
  percent: number
  step_id: string | null
  step_index: number
  step_count: number
  message: string | null
  updated_at: IsoDateTime
}

export interface TuningPreview {
  name: string
  /** front · three_quarter · side · back · face · face_texture */
  view: string
  /** before · after */
  variant: string
  /** matcap · texture (Blender-side addition; absent on older runs) */
  shading?: string
  url: string
}

export interface TuningResult {
  /** Unique per run (`/api/v1/tunings/{id}/model.glb`). */
  model_url: string
  download_url: string
  report_url: string
  log_url: string
  previews: TuningPreview[]
  stats: MeshStats | null
}

export interface TuningBlenderInfo {
  executable: string
  version: string
  kind: BlenderInstallKind
}

/** `TuningRunOut`. Status reuses the job lifecycle values. */
export interface TuningRun {
  id: string
  job_id: string
  status: JobStatus
  created_at: IsoDateTime
  started_at: IsoDateTime | null
  finished_at: IsoDateTime | null
  options: TuningOptionValues
  progress: TuningProgress
  steps: TuningStep[]
  metrics: TuningMetric[]
  warnings: string[]
  validation: TuningValidation | null
  blender: TuningBlenderInfo | null
  result: TuningResult | null
  error: JobError | null
}

export interface TuningRunList {
  items: TuningRun[]
}

// ------------------------------------------------------------------------------------------------
// WebSocket (§8)
// ------------------------------------------------------------------------------------------------

export interface WsHelloMessage {
  type: 'hello'
  ts: IsoDateTime
  data: { system: SystemStatus }
}

export interface WsJobUpdatedMessage {
  type: 'job.updated'
  ts: IsoDateTime
  data: Job
}

export interface WsSystemUpdatedMessage {
  type: 'system.updated'
  ts: IsoDateTime
  data: SystemStatus
}

export interface WsPingMessage {
  type: 'ping'
  ts: IsoDateTime
  data: Record<string, never>
}

/** Blender runtime snapshot (detection, selection, download progress — progress frames may be dropped). */
export interface WsBlenderUpdatedMessage {
  type: 'blender.updated'
  ts: IsoDateTime
  data: BlenderRuntimeStatus
}

/** Full tuning run snapshot (progress frames may be dropped, status changes are not). */
export interface WsTuningUpdatedMessage {
  type: 'tuning.updated'
  ts: IsoDateTime
  data: TuningRun
}

export type WsServerMessage =
  | WsHelloMessage
  | WsJobUpdatedMessage
  | WsSystemUpdatedMessage
  | WsBlenderUpdatedMessage
  | WsTuningUpdatedMessage
  | WsPingMessage

export interface WsClientMessage {
  type: 'pong'
}

/** Close code sent by the server when WebSocket authentication fails. */
export const WS_CLOSE_AUTH_FAILED = 4401
/** Close code sent by the server on shutdown. */
export const WS_CLOSE_GOING_AWAY = 1001

// ------------------------------------------------------------------------------------------------
// Part-aware additions (part-aware DESIGN.md §6.7; APPEND-ONLY, this file stays import-free)
// The other part-aware wire types live in `partAwareTypes.ts`.
// ------------------------------------------------------------------------------------------------

/** Provenance of a reference image imported from a text-to-image candidate (`…/candidates/{index}/use`). */
export interface ImageSourceOut {
  kind: 't2i'
  image_generation_id: string
  candidate_index: number
  model_id: string
  model_revision: string | null
  license_id: string
  seed: number
}

/** Interface merging with {@link ReferenceImage} above (additive response field). */
export interface ReferenceImage {
  /** `null` for uploads; absent on older backends. */
  source?: ImageSourceOut | null
}
