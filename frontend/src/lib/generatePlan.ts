/**
 * One generation decision table for every UI mode (UI_DESIGN.md §2.9 = DESIGN §6.9 as code). Every Generate trigger
 * (Default card/bar, Mobile FAB, Tablet rail, Node `job` graphs, Ctrl/Cmd+Enter) ends in `planGeneration`.
 *
 * | effective source | parts | prompt flow | plan                                                    |
 * |------------------|-------|-------------|---------------------------------------------------------|
 * | image            | off   | –           | `job` → POST /jobs (G1, the unchanged body)             |
 * | image            | on    | –           | `workflow` → POST /workflows, image source (G2 / G2a)   |
 * | prompt           | any   | one_shot    | `workflow` → POST /workflows, prompt source (G3 / G4)   |
 * | prompt           | any   | review      | `image_generation` → POST /image-generations (G5)       |
 *
 * The stored choices are never rewritten here: an unsupported prompt source or a legacy backend only changes the
 * *effective* values, so nothing can trap the user (UI11).
 *
 * PURE (UI_DESIGN §1.5): relative `.ts` value imports only; no React, zustand, axios or browser globals.
 */
import type { ImageGenerationRequest, WorkflowRequest, WorkflowSource } from '../api/partAwareTypes.ts'
import type { CreateJobRequest, ReferenceMode } from '../api/types.ts'
import type { CapabilityView, FeaturesSnapshot } from './featureFlags.ts'
import type { InputSource, PartOptionsForm, PromptForm } from './forms.ts'
import { buildImageJobRequest } from './imageJobRequest.ts'
import { toRequestSettings, type SettingsForm } from './settingsRequest.ts'
import { buildGeneration, buildImageSource, buildPromptSource, buildWorkflowRequest } from './workflowRequest.ts'

export type EffectiveSource = 'image' | 'prompt'

export interface PlanReferences {
  id: string
  primaryImageId: string | null
  imageCount: number
}

export interface PlanInputs {
  features: FeaturesSnapshot
  source: InputSource
  prompt: PromptForm
  parts: PartOptionsForm
  references: PlanReferences | null
  form: SettingsForm
  referenceMode: ReferenceMode
}

export type GenerationPlan =
  | { kind: 'job'; request: CreateJobRequest; needsEngine: true }
  | { kind: 'workflow'; request: WorkflowRequest; needsEngine: true }
  | { kind: 'image_generation'; request: ImageGenerationRequest; needsEngine: false }
  | { kind: 'blocked'; code: PlanBlockCode; reason: string }

export type PlanBlockCode =
  | 'no_references'
  | 'empty_prompt'
  | 'prompt_too_long'
  | 'parts_unavailable'
  | 'workflows_unavailable'
  | 'features_loading'
  | 'features_error'

/** A backend reason as a clause inside a sentence (no trailing full stop, never empty). */
export function reasonClause(c: CapabilityView): string {
  const text = (c.reason ?? '').trim().replace(/[.\s]+$/, '')
  return text || `not available (${c.code})`
}

/** Shared wording of the plan blocks and the matching availability rules (UI_DESIGN §2.9, §2.10). */
export const PLAN_TEXT = {
  featuresLoading: 'Checking prompt-to-3D availability…',
  featuresError: 'Could not check prompt-to-3D availability. Retrying…',
  emptyPrompt: 'Describe what to generate.',
  noReferences: 'Add at least one reference image.',
  promptTooLong: (maxChars: number) => `The prompt is too long (max ${maxChars} characters).`,
  oneShotNeedsWorkflows: (workflow: CapabilityView) =>
    `Straight to 3D needs workflows: ${reasonClause(workflow)}. Use Review image first.`,
  partsUnavailable: (parts: CapabilityView) => `Part-aware output is unavailable: ${reasonClause(parts)}.`,
  partsNeedWorkflows: (workflow: CapabilityView) => `Part-aware output needs workflows: ${reasonClause(workflow)}.`,
} as const

/**
 * `image` when chosen; also `image` on a legacy backend or when a `ready` backend reports prompt-to-3D unavailable
 * (judge fix: never trap the user). While features are `loading`/`error` the prompt source is kept and blocks.
 */
export function effectiveSource(source: InputSource, f: FeaturesSnapshot): EffectiveSource {
  if (source === 'image') return 'image'
  if (f.status === 'legacy' || (f.status === 'ready' && !f.prompt.available)) return 'image'
  return 'prompt'
}

/** Parts are forced off on a legacy backend (UI11); otherwise the stored switch decides. */
export function effectivePartsEnabled(p: PartOptionsForm, f: FeaturesSnapshot): boolean {
  return f.status === 'legacy' ? false : p.enabled
}

/**
 * Gate of the lazy part options section (UI_DESIGN §4.2): always when features are ready; while they load or failed,
 * only when a stored `enabled = true` needs its off switch (rule 7a); never on a legacy backend.
 */
export function shouldRenderPartOptions(f: FeaturesSnapshot, p: PartOptionsForm): boolean {
  return f.status === 'ready' || (p.enabled && f.status !== 'legacy')
}

/** Number of review candidates actually requested (the stored value clamped to the backend's `max_images`). */
export function candidateCount(p: PromptForm, f: FeaturesSnapshot): number {
  return Math.max(1, Math.min(p.numCandidates, f.prompt.maxImages))
}

const blocked = (code: PlanBlockCode, reason: string): GenerationPlan => ({ kind: 'blocked', code, reason })

/** The call a Generate press makes now, or why it cannot; the first matching block wins (UI_DESIGN §2.9). */
export function planGeneration(i: PlanInputs): GenerationPlan {
  const f = i.features
  const source = effectiveSource(i.source, f)
  const partsOn = effectivePartsEnabled(i.parts, f)

  const references = i.references
  if (source === 'prompt') {
    if (f.status === 'loading') return blocked('features_loading', PLAN_TEXT.featuresLoading)
    if (f.status === 'error') return blocked('features_error', PLAN_TEXT.featuresError)
    const length = i.prompt.prompt.trim().length
    if (length === 0) return blocked('empty_prompt', PLAN_TEXT.emptyPrompt)
    if (length > f.prompt.maxChars) return blocked('prompt_too_long', PLAN_TEXT.promptTooLong(f.prompt.maxChars))
    // DESIGN §4.4.8: one-shot needs workflows; the review flow does not.
    if (i.prompt.flow === 'one_shot' && !f.workflow.available) {
      return blocked('workflows_unavailable', PLAN_TEXT.oneShotNeedsWorkflows(f.workflow))
    }
  }
  if (source === 'image' && (!references || references.imageCount === 0)) {
    return blocked('no_references', PLAN_TEXT.noReferences)
  }
  if (partsOn && !f.parts.available) return blocked('parts_unavailable', PLAN_TEXT.partsUnavailable(f.parts))
  if (partsOn && !f.workflow.available) return blocked('workflows_unavailable', PLAN_TEXT.partsNeedWorkflows(f.workflow))

  const ctx = { features: f }
  if (source === 'prompt') {
    // Review first: the 3D step is the candidate action "Use & generate 3D", which plans again with the image source.
    if (i.prompt.flow === 'review') {
      return { kind: 'image_generation', request: buildGeneration(i.prompt, candidateCount(i.prompt, f), ctx), needsEngine: false }
    }
    return { kind: 'workflow', request: workflowRequest(i, buildPromptSource(i.prompt, ctx), partsOn), needsEngine: true }
  }

  // source === 'image': `references` is non-empty here (no_references above).
  const set = references as PlanReferences
  if (!partsOn) {
    const request = buildImageJobRequest({
      referenceSetId: set.id,
      primaryImageId: set.primaryImageId,
      form: i.form,
      referenceMode: i.referenceMode,
    })
    return { kind: 'job', request, needsEngine: true }
  }
  return { kind: 'workflow', request: workflowRequest(i, buildImageSource(set.id, set.primaryImageId), partsOn), needsEngine: true }
}

function workflowRequest(i: PlanInputs, source: WorkflowSource, partsOn: boolean): WorkflowRequest {
  return buildWorkflowRequest(
    { source, partOptions: { ...i.parts, enabled: partsOn }, settings: toRequestSettings(i.form, i.referenceMode) },
    { features: i.features },
  )
}
