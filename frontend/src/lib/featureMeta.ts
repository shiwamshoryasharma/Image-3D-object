/**
 * UI vocabulary shared by every UI mode (UI_DESIGN.md §2.11, §4.1 rule 3, §5.2, §6.4): feature titles, workflow step
 * labels, warning texts and the labels of the backend enum values.
 *
 * This is the only `src/` file allowed to name taxonomy groups or part kinds as display data (UI14). Everything the
 * backend reports (model names, group labels, strategy labels, reasons) is shown as reported; the tables here only
 * cover fixed vocabulary and fall-backs.
 *
 * PURE (UI_DESIGN §1.5): relative `.ts` value imports only; no React, zustand, axios or browser globals.
 */
import type { CommercialUse, ModelUiState, PromptPreset, PromptStyle, SegProfile, SegSubject, WorkflowStepId } from '../api/partAwareTypes.ts'
import type { PartViewKind } from './partsModel.ts'

// ------------------------------------------------------------------------------------------------
// Feature titles (same words in every shell, WCAG 3.2.4)
// ------------------------------------------------------------------------------------------------

export type FeatureId = 'create' | 'viewer' | 'parts' | 'library' | 'appSettings' | 'generate'

export interface FeatureMeta {
  readonly title: string
  /** Label for navigation items with little room (rail, floating nav). */
  readonly shortTitle: string
  readonly description: string
}

export const FEATURE_META: Readonly<Record<FeatureId, FeatureMeta>> = Object.freeze({
  create: { title: 'Create', shortTitle: 'Create', description: 'Reference images or a prompt, and generation settings.' },
  viewer: { title: 'Viewer', shortTitle: 'Viewer', description: 'The generated 3D model.' },
  parts: { title: 'Parts', shortTitle: 'Parts', description: 'Parts of the viewed model, their details and downloads.' },
  library: { title: 'Library', shortTitle: 'Library', description: 'Earlier generations on this backend.' },
  appSettings: { title: 'App settings', shortTitle: 'Settings', description: 'UI style, density, motion, features and models.' },
  generate: { title: 'Generate', shortTitle: 'Generate', description: 'Start a generation with the current inputs.' },
})

// ------------------------------------------------------------------------------------------------
// Runs and workflow steps
// ------------------------------------------------------------------------------------------------

/** Workflow step labels (DESIGN §4.7.3 step ids). Run steps (`RunStepOut`) carry their own label from the payload. */
export const STEP_LABELS: Readonly<Record<WorkflowStepId, string>> = Object.freeze({
  text_to_image: 'Text to image',
  import_image: 'Import image',
  segmentation: 'Segmentation',
  reconstruction: 'Image to 3D',
  part_extraction: 'Part extraction',
  export: 'Export',
})

/** Label of a workflow step id; an unknown (future) id is shown humanised instead of failing. */
export function stepLabel(id: string): string {
  if (Object.hasOwn(STEP_LABELS, id)) return STEP_LABELS[id as WorkflowStepId]
  const words = id.replace(/[_\s]+/g, ' ').trim()
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : 'Step'
}

export type RunLabelKind = 'job' | 'workflow' | 'image_generation' | 'segmentation' | 'part_analysis'

export const RUN_KIND_LABELS: Readonly<Record<RunLabelKind, string>> = Object.freeze({
  job: 'Generation',
  workflow: 'Generation',
  image_generation: 'Image generation',
  segmentation: 'Segmentation',
  part_analysis: 'Part analysis',
})

/** One text per DESIGN §6.8 warning code; `readiness.issues` use the same `t2i_*` strings (DESIGN §4.4.5). */
export const WARNING_TEXT: Readonly<Record<string, string>> = Object.freeze({
  t2i_touches_border: 'Touches the image border',
  t2i_multiple_subjects: 'Several subjects',
  t2i_coverage_low: 'Subject too small',
  t2i_coverage_high: 'Subject too large',
  t2i_aspect_mismatch: 'Wider than tall',
  profile_degraded: 'A detector profile was unavailable; a simpler one was used',
  frame_unverified: 'Image alignment could not be verified',
  head_pass_low_res: 'Face details were too small to refine',
  segmentation_rerun_for_alignment:
    "The parts step segmented the model's own view again (the image had no real transparency)",
  camera_fit_low_iou: 'Camera alignment was uncertain; labels have lower confidence',
  too_small_for_geometry: 'Too small to cut; kept as a surface region',
  max_parts_exceeded: 'Too many parts; the smallest were kept as regions',
  expected_part_missing: 'A part asked for in the prompt was not found',
  charts_cross_boundary: 'Texture charts cross part borders',
  low_confidence: 'Low label confidence',
  floater_candidates: 'Small loose pieces were kept on the body',
  gpu_defer_timeout: 'Waited the maximum time behind image-to-3D jobs, then continued',
})

/** Display text of a warning or readiness code; unknown codes show the server message (or the code itself). */
export function warningText(code: string, serverMessage?: string | null): string {
  if (Object.hasOwn(WARNING_TEXT, code)) return WARNING_TEXT[code]
  return serverMessage?.trim() || code
}

/** Readiness chip for a candidate without issues (UI_DESIGN §5.3). */
export const READY_FOR_3D_TEXT = 'Looks 3D-ready'

// ------------------------------------------------------------------------------------------------
// Parts
// ------------------------------------------------------------------------------------------------

/** Kind badge text (never colour alone, WCAG 1.4.1). */
export const PART_KIND_LABELS: Readonly<Record<PartViewKind, string>> = Object.freeze({
  base: 'Base',
  physical: 'Physical',
  deferred: 'Geometry later',
  region: 'Region',
})

/** Display order of the part list groups (UI_DESIGN §6.4); `base` collects the base record, unknown groups go to `other`. */
export const PART_GROUP_ORDER = Object.freeze([
  'base',
  'eyes',
  'eyebrows',
  'eyelashes',
  'hair',
  'clothes',
  'accessories',
  'detachable',
  'face',
  'body',
  'other',
] as const)
export type PartDisplayGroup = (typeof PART_GROUP_ORDER)[number]

/** Used only when `/features` does not label a group (its `separable_groups` / `region_groups` labels win). */
export const PART_GROUP_FALLBACK_LABELS: Readonly<Record<PartDisplayGroup, string>> = Object.freeze({
  base: 'Base',
  eyes: 'Eyes',
  eyebrows: 'Eyebrows',
  eyelashes: 'Eyelashes',
  hair: 'Hair',
  clothes: 'Clothes',
  accessories: 'Accessories',
  detachable: 'Detachable objects',
  face: 'Face',
  body: 'Body',
  other: 'Other',
})

/** Inspector geometry fact for `geometry.occluded_geometry_missing` (UI_DESIGN §6.1 `geometryNote`). */
export const OCCLUDED_GEOMETRY_NOTE = 'Hidden geometry not generated'

// ------------------------------------------------------------------------------------------------
// Prompt and segmentation options
// ------------------------------------------------------------------------------------------------

export const PROMPT_PRESET_LABELS: Readonly<Record<PromptPreset, string>> = Object.freeze({
  character: 'Character',
  object: 'Object',
  raw: 'Raw prompt',
})

export const PROMPT_STYLE_LABELS: Readonly<Record<PromptStyle, string>> = Object.freeze({
  realistic: 'Realistic',
  stylized: 'Stylized',
  anime: 'Anime',
})

export const SEG_SUBJECT_LABELS: Readonly<Record<SegSubject, string>> = Object.freeze({
  auto: 'Auto',
  anime_character: 'Anime character',
  realistic_human: 'Realistic human',
  object: 'Object',
})

export const SEG_PROFILE_LABELS: Readonly<Record<SegProfile, string>> = Object.freeze({
  auto: 'Auto',
  baseline: 'Baseline',
  anime: 'Anime',
  realistic: 'Realistic',
})

// ------------------------------------------------------------------------------------------------
// Models and licences (App settings ▸ Features & models)
// ------------------------------------------------------------------------------------------------

export const MODEL_STATE_TEXT: Readonly<Record<ModelUiState, string>> = Object.freeze({
  loaded: 'In memory',
  installed: 'Installed',
  loading: 'Loading…',
  not_installed: 'Not installed',
  unavailable: 'Unavailable',
  error: 'Error',
})

export const LICENSE_BADGE_TEXT: Readonly<Record<CommercialUse, string>> = Object.freeze({
  allowed: 'Permissive',
  allowed_with_conditions: 'Permissive with conditions',
  revenue_capped: 'Revenue-capped',
  non_commercial: 'Non-commercial',
  research_only: 'Research only',
  unknown: 'Unknown',
})

/** Text of the paused tuner / refinement rows in App settings (UI_DESIGN §3.8). */
export const PAUSED_FEATURE_TEXT = 'Paused until you approve it'
