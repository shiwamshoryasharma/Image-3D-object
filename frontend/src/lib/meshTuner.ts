/**
 * Auto Mesh Tuner / Blender runtime: display constants, preset defaults (DESIGN.md §4) and pure helpers.
 * Kept out of component files so `react/only-export-components` stays quiet.
 */
import type {
  BlenderDownload,
  BlenderDownloadState,
  BlenderInstall,
  BlenderInstallKind,
  BlenderRuntimeMode,
  BlenderRuntimeState,
  TuningMetric,
  TuningOptionValues,
  TuningOptions,
  TuningPreset,
  TuningPreview,
  TuningStep,
  TuningStepStatus,
  TuningSymmetry,
} from '../api/types'
import { formatBytes, formatInteger } from './format'

// ------------------------------------------------------------------------------------------------
// Runtime
// ------------------------------------------------------------------------------------------------

export const RUNTIME_STATE_LABEL: Record<BlenderRuntimeState, string> = {
  detecting: 'Detecting',
  ready: 'Ready',
  needs_download: 'Setup needed',
  downloading: 'Installing',
  error: 'Error',
}

export const RUNTIME_MODE_LABEL: Record<BlenderRuntimeMode, string> = {
  system: 'Use System Blender',
  embedded: 'Use Embedded Blender',
  auto: 'Auto',
}

export const RUNTIME_MODE_HINT: Record<BlenderRuntimeMode, string> = {
  auto: 'Prefers the newest stable compatible system Blender, then an embedded (downloaded) build.',
  system: 'Uses a Blender installed on this PC. Pick a specific install with “Change Runtime”.',
  embedded: 'Uses the portable Blender downloaded by Img3D into its own runtime folder.',
}

export const INSTALL_KIND_LABEL: Record<BlenderInstallKind, string> = {
  system: 'System',
  embedded: 'Embedded',
}

export const DOWNLOAD_STATE_LABEL: Record<BlenderDownloadState, string> = {
  idle: 'Waiting',
  resolving: 'Resolving release',
  downloading: 'Downloading',
  verifying: 'Verifying SHA-256',
  extracting: 'Extracting',
  validating: 'Validating',
  completed: 'Installed',
  failed: 'Failed',
  cancelled: 'Cancelled',
}

const ACTIVE_DOWNLOAD_STATES: ReadonlySet<BlenderDownloadState> = new Set<BlenderDownloadState>([
  'resolving',
  'downloading',
  'verifying',
  'extracting',
  'validating',
])

/** A download is running (resolving … validating; not idle/completed/failed/cancelled). */
export function isDownloadActive(download: BlenderDownload | null | undefined): boolean {
  return !!download && ACTIVE_DOWNLOAD_STATES.has(download.state)
}

/** Phases shown as a checklist under the download progress bar. */
export const DOWNLOAD_PHASES: ReadonlyArray<{ state: BlenderDownloadState; label: string }> = [
  { state: 'downloading', label: 'Download' },
  { state: 'verifying', label: 'Verify SHA-256' },
  { state: 'extracting', label: 'Extract' },
  { state: 'validating', label: 'Validate' },
]

const PHASE_ORDER: Record<BlenderDownloadState, number> = {
  idle: 0,
  resolving: 0,
  downloading: 1,
  verifying: 2,
  extracting: 3,
  validating: 4,
  completed: 5,
  failed: -1,
  cancelled: -1,
}

export type PhaseState = 'done' | 'active' | 'pending'

function bytesComplete(download: BlenderDownload): boolean {
  return !!download.bytes_total && download.bytes_done >= download.bytes_total
}

/**
 * Visual state of a download phase. For failed/cancelled downloads only the byte transfer can be known to be
 * finished (the bytes are complete); later phases are reported as pending.
 */
export function downloadPhaseState(download: BlenderDownload, phase: BlenderDownloadState): PhaseState {
  const current = PHASE_ORDER[download.state]
  const target = PHASE_ORDER[phase]
  if (current < 0) return phase === 'downloading' && bytesComplete(download) ? 'done' : 'pending'
  if (current > target) return 'done'
  return current === target ? 'active' : 'pending'
}

function bytePercent(download: BlenderDownload): number | null {
  if (download.percent !== null) return download.percent
  if (download.bytes_total) return (download.bytes_done / download.bytes_total) * 100
  return null
}

/**
 * Bar value: bytes while downloading, indeterminate (null) during resolve/verify/extract/validate, and a static
 * value once the download ended (completed 100, failed/cancelled at the bytes reached — never animated).
 */
export function downloadPercent(download: BlenderDownload): number | null {
  switch (download.state) {
    case 'completed':
      return 100
    case 'downloading':
      return bytePercent(download)
    case 'failed':
    case 'cancelled':
      return bytePercent(download) ?? 0
    default:
      return null
  }
}

export function formatSpeed(bytesPerSecond: number | null | undefined): string {
  if (bytesPerSecond === null || bytesPerSecond === undefined || !Number.isFinite(bytesPerSecond) || bytesPerSecond <= 0) {
    return '—'
  }
  return `${formatBytes(bytesPerSecond)}/s`
}

/** "download.blender.org" from a release URL (falls back to the raw value). */
export function hostOf(url: string | null | undefined): string {
  if (!url) return '—'
  try {
    return new URL(url).host
  } catch {
    return url
  }
}

/** "5.2.2" without the LTS suffix (the LTS tag is rendered separately). */
export function installVersion(install: BlenderInstall): string {
  return install.version ?? install.version_label?.replace(/\s+LTS$/i, '') ?? 'Unknown version'
}

/** Non-release build cycle tag ("Beta", "RC", "Alpha"), null for stable releases. */
export function cycleTag(install: BlenderInstall): string | null {
  switch (install.cycle) {
    case 'alpha':
      return 'Alpha'
    case 'beta':
      return 'Beta'
    case 'rc':
      return 'RC'
    default:
      return null
  }
}

/** Short reason an install cannot be used. */
export function incompatibleText(install: BlenderInstall): string {
  if (install.compatible) return ''
  return install.incompatible_reason ?? install.error ?? (install.valid ? 'Not compatible' : 'Could not run this executable')
}

// ------------------------------------------------------------------------------------------------
// Tuning options (DESIGN.md §4)
// ------------------------------------------------------------------------------------------------

export type TuningOverrideKey = Exclude<keyof TuningOptionValues, 'preset'>
export type TuningOverrides = Partial<Omit<TuningOptionValues, 'preset'>>

const BALANCED: TuningOptionValues = {
  preset: 'balanced',
  remove_inner_shell: true,
  fill_holes: true,
  max_hole_edges: 300,
  remove_islands: true,
  island_min_ratio: 0.001,
  smoothing: 0.5,
  refine_face: true,
  eye_correction: true,
  symmetry: 'auto',
  refine_density: true,
  quads: false,
  subdivision: 'auto',
  optimize_rig: true,
  render_previews: true,
  save_blend: false,
}

/** Mirror of the backend preset table; used to display effective values (the backend stays authoritative). */
export const PRESET_DEFAULTS: Record<TuningPreset, TuningOptionValues> = {
  conservative: { ...BALANCED, preset: 'conservative', smoothing: 0.25, refine_density: false, max_hole_edges: 120 },
  balanced: BALANCED,
  strong: { ...BALANCED, preset: 'strong', smoothing: 0.8, max_hole_edges: 1000, island_min_ratio: 0.003 },
}

export const PRESET_OPTIONS: ReadonlyArray<{ value: TuningPreset; label: string; title: string }> = [
  { value: 'conservative', label: 'Conservative', title: 'Light smoothing, small holes only, no density refinement' },
  { value: 'balanced', label: 'Balanced', title: 'Recommended defaults for TRELLIS.2 characters' },
  { value: 'strong', label: 'Strong', title: 'Heavier denoise, larger hole filling, more aggressive island removal' },
]

export const PRESET_HINT: Record<TuningPreset, string> = {
  conservative: 'Minimal changes: light denoise, fills only small holes, keeps the original density.',
  balanced: 'Recommended: removes the hidden inner shell, fills holes, denoises and refines the face.',
  strong: 'Aggressive cleanup for noisy models: stronger smoothing and larger hole filling.',
}

/** Symmetry choices offered in the UI (the API also accepts "symmetrize"). */
export const SYMMETRY_OPTIONS: ReadonlyArray<{ value: TuningSymmetry; label: string; title: string }> = [
  { value: 'auto', label: 'Auto', title: 'Repair asymmetries only when the model is mirror-symmetric' },
  { value: 'off', label: 'Off', title: 'Never change symmetry' },
  { value: 'repair', label: 'Repair', title: 'Always repair local asymmetric defects across the X mirror plane' },
]

export const SMOOTHING_BOUNDS = { min: 0, max: 1, step: 0.05 } as const

const OVERRIDE_TYPES: Record<TuningOverrideKey, 'boolean' | 'number' | 'string'> = {
  remove_inner_shell: 'boolean',
  fill_holes: 'boolean',
  max_hole_edges: 'number',
  remove_islands: 'boolean',
  island_min_ratio: 'number',
  smoothing: 'number',
  refine_face: 'boolean',
  eye_correction: 'boolean',
  symmetry: 'string',
  refine_density: 'boolean',
  quads: 'boolean',
  subdivision: 'string',
  optimize_rig: 'boolean',
  render_previews: 'boolean',
  save_blend: 'boolean',
}

/** Keep only well-typed, known override keys (used when restoring persisted state). */
export function sanitizeOverrides(value: unknown): TuningOverrides {
  if (typeof value !== 'object' || value === null) return {}
  const result: Record<string, unknown> = {}
  for (const [key, type] of Object.entries(OVERRIDE_TYPES)) {
    const entry = (value as Record<string, unknown>)[key]
    if (typeof entry === type && (type !== 'number' || Number.isFinite(entry))) result[key] = entry
  }
  return result as TuningOverrides
}

/** Effective values shown in the form: explicit overrides win over the preset defaults. */
export function effectiveOptions(preset: TuningPreset, overrides: TuningOverrides): TuningOptionValues {
  return { ...PRESET_DEFAULTS[preset], ...overrides, preset }
}

/** Request body: the preset plus only the explicitly changed fields (the backend fills the rest). */
export function toTuningRequest(preset: TuningPreset, overrides: TuningOverrides): TuningOptions {
  const body: TuningOptions = { preset }
  const defaults = PRESET_DEFAULTS[preset]
  for (const key of Object.keys(overrides) as TuningOverrideKey[]) {
    const value = overrides[key]
    if (value !== undefined && value !== defaults[key]) Object.assign(body, { [key]: value })
  }
  return body
}

/** Number of overrides that differ from the preset defaults. */
export function overrideCount(preset: TuningPreset, overrides: TuningOverrides): number {
  return Object.keys(toTuningRequest(preset, overrides)).length - 1
}

// ------------------------------------------------------------------------------------------------
// Steps
// ------------------------------------------------------------------------------------------------

/** Fixed step plan (DESIGN.md §3) shown before the backend reports its own step list. */
export const TUNING_STEP_PLAN: readonly TuningStep[] = [
  { id: 'load', label: 'Load mesh', status: 'pending', message: null, duration_s: null },
  { id: 'analyze', label: 'Mesh analysis', status: 'pending', message: null, duration_s: null },
  { id: 'geometry_repair', label: 'Geometry repair', status: 'pending', message: null, duration_s: null },
  { id: 'face_refinement', label: 'Face refinement', status: 'pending', message: null, duration_s: null },
  { id: 'topology_tuning', label: 'Topology tuning', status: 'pending', message: null, duration_s: null },
  { id: 'rig_optimization', label: 'Rig optimization', status: 'pending', message: null, duration_s: null },
  { id: 'validation', label: 'Validation', status: 'pending', message: null, duration_s: null },
  { id: 'export', label: 'Save results', status: 'pending', message: null, duration_s: null },
]

export const STEP_STATUS_TEXT: Record<TuningStepStatus, string> = {
  pending: 'pending',
  running: 'in progress',
  done: 'done',
  warning: 'done with warnings',
  skipped: 'skipped',
  failed: 'failed',
}

// ------------------------------------------------------------------------------------------------
// Metrics & previews
// ------------------------------------------------------------------------------------------------

const significant = new Intl.NumberFormat('en-US', { maximumSignificantDigits: 4 })

function isPercentUnit(metric: Pick<TuningMetric, 'key' | 'unit'>): boolean {
  return metric.unit === '%' || metric.key.endsWith('_pct')
}

/** Format one metric value (integers grouped, decimals to 4 significant digits, units appended). */
export function formatMetricValue(metric: Pick<TuningMetric, 'key' | 'unit'>, value: number | null): string {
  if (value === null || !Number.isFinite(value)) return '—'
  const number = Number.isInteger(value) ? formatInteger(value) : significant.format(value)
  if (isPercentUnit(metric)) return `${number}%`
  return metric.unit ? `${number} ${metric.unit}` : number
}

export type MetricTrend = 'better' | 'worse' | 'same' | 'neutral' | 'unknown'

export interface MetricChange {
  /** Signed absolute change ("−18,342", "+0.12%"), "no change" or "—". */
  text: string
  /** Relative change of non-percentage metrics ("−100%"), when meaningful. */
  relative: string | null
  trend: MetricTrend
}

/** Signed change between before/after, classified by the metric's `better` direction. */
export function metricChange(metric: TuningMetric): MetricChange {
  const { before, after } = metric
  if (before === null || after === null || !Number.isFinite(before) || !Number.isFinite(after)) {
    return { text: '—', relative: null, trend: 'unknown' }
  }
  const delta = after - before
  if (Math.abs(delta) < 1e-12) return { text: 'no change', relative: null, trend: 'same' }
  const sign = delta > 0 ? '+' : '−'
  const relative =
    !isPercentUnit(metric) && before !== 0 ? `${sign}${significant.format(Math.abs(delta / before) * 100)}%` : null
  let trend: MetricTrend = 'neutral'
  if (metric.better === 'lower') trend = delta < 0 ? 'better' : 'worse'
  else if (metric.better === 'higher') trend = delta > 0 ? 'better' : 'worse'
  return { text: `${sign}${formatMetricValue(metric, Math.abs(delta))}`, relative, trend }
}

export const PREVIEW_VIEW_LABEL: Record<string, string> = {
  front: 'Front',
  three_quarter: '3/4',
  side: 'Side',
  back: 'Back',
  face: 'Face',
  face_texture: 'Face (texture)',
}

export interface PreviewPair {
  view: string
  label: string
  before: TuningPreview | null
  after: TuningPreview | null
}

const VIEW_ORDER = ['front', 'three_quarter', 'side', 'back', 'face', 'face_texture']

/** Group previews into before/after pairs per view (known views first, in a stable order). */
export function pairPreviews(previews: readonly TuningPreview[]): PreviewPair[] {
  const pairs = new Map<string, PreviewPair>()
  for (const preview of previews) {
    const pair = pairs.get(preview.view) ?? {
      view: preview.view,
      label: PREVIEW_VIEW_LABEL[preview.view] ?? preview.view.replace(/_/g, ' '),
      before: null,
      after: null,
    }
    if (preview.variant === 'before') pair.before = preview
    else pair.after = preview
    pairs.set(preview.view, pair)
  }
  const rank = (view: string) => {
    const index = VIEW_ORDER.indexOf(view)
    return index === -1 ? VIEW_ORDER.length : index
  }
  return [...pairs.values()].sort((a, b) => rank(a.view) - rank(b.view))
}

/** Value/threshold of a validation check as text. */
export function formatCheckValue(value: number | string | boolean | null): string {
  if (value === null) return '—'
  if (typeof value === 'boolean') return value ? 'yes' : 'no'
  if (typeof value === 'number') return Number.isInteger(value) ? formatInteger(value) : significant.format(value)
  return value
}
