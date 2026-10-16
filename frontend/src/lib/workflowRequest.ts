/**
 * Request builders for the part-aware calls (UI_DESIGN.md §2.8): image generations, segmentation options and the
 * segmentation preview request, part analysis options, the Node Export params and the simple-form `WorkflowRequest`.
 *
 * Emission rules (they are what keeps the golden bodies G2–G8a byte-stable):
 * - keys are emitted in DESIGN schema order, and only the fixed DESIGN v1 keys (`fixtures/request_keys.json`);
 * - nested objects accept partial objects (DESIGN §6.0), so optional keys are sent only when they differ from the
 *   backend default; required keys (`prompt`, `separate`, `source`, `settings`) are always sent;
 * - never a top-level `export`, a `parts.segmentation_id`, or a `segmentation` without `parts` (DESIGN §4.7.1).
 *
 * PURE (UI_DESIGN §1.5): relative `.ts` value imports only; no React, zustand, axios or browser globals.
 */
import type {
  ImageGenerationRequest,
  PartAnalysisOptions,
  SegmentationOptions,
  SegmentationRequest,
  SegmentationSource,
  UseCandidateRequest,
  WorkflowRequest,
  WorkflowSource,
} from '../api/partAwareTypes.ts'
import type { GenerationSettings } from '../api/types.ts'
import { defaultSeparate, type FeaturesSnapshot } from './featureFlags.ts'
import { normalizeExtraTerms, type PartOptionsForm, type PromptForm } from './forms.ts'

export interface BuildContext {
  features: FeaturesSnapshot
}

/** `ImageGenerationRequest` (G5; with `numImages = 1` the `generation` of a prompt workflow source, G3/G4). */
export function buildGeneration(p: PromptForm, numImages: number, c: BuildContext): ImageGenerationRequest {
  const request: ImageGenerationRequest = { prompt: p.prompt.trim() }
  const negative = p.negativePrompt.trim()
  if (c.features.prompt.supportsNegativePrompt && negative) request.negative_prompt = negative
  if (p.modelId !== null) request.model_id = p.modelId
  request.preset = p.preset
  if (p.style !== null) request.style = p.style
  request.part_hint = p.partHint
  request.num_images = numImages
  request.seed = p.seed
  request.randomize_seed = p.randomizeSeed
  if (p.size !== null) {
    request.width = p.size[0]
    request.height = p.size[1]
  }
  // `steps` / `guidance` are never sent by v1 (model defaults).
  return request
}

/** Segmentation preview source for a reference image (`POST /segmentations`, DESIGN §4.5.1). */
export function buildReferenceImageSegmentationSource(referenceSetId: string, imageId: string): SegmentationSource {
  return { kind: 'reference_image', reference_set_id: referenceSetId, image_id: imageId }
}

/**
 * `POST /segmentations` body for the segmentation preview (display only; its id is never sent in a workflow, DESIGN
 * §4.7.1). `options` is sent only when an option differs from its default.
 */
export function buildSegmentationRequest(source: SegmentationSource, f: PartOptionsForm, c: BuildContext): SegmentationRequest {
  const request: SegmentationRequest = { source }
  const options = buildSegmentationOptions(f, c)
  if (options !== null) request.options = options
  return request
}

/** Partial `SegmentationOptions`; `null` when every option is the default (`auto` / `auto` / no terms). */
export function buildSegmentationOptions(f: PartOptionsForm, c: BuildContext): SegmentationOptions | null {
  const options: SegmentationOptions = {}
  if (f.profile !== 'auto') options.profile = f.profile
  if (f.subject !== 'auto') options.subject = f.subject
  const terms = normalizeExtraTerms(f.extraTerms, c.features.segmentation.maxExtraTerms)
  if (terms.length > 0) options.extra_terms = terms
  return Object.keys(options).length > 0 ? options : null
}

/** `separate` resolved: the stored choice (or the backend defaults), filtered to known groups, in `/features` order. */
export function resolveSeparate(f: PartOptionsForm, features: FeaturesSnapshot): string[] {
  const chosen = new Set(f.separate ?? defaultSeparate(features))
  return features.parts.separableGroups.filter((g) => chosen.has(g.id)).map((g) => g.id)
}

/**
 * `PartAnalysisOptions`: the same object for a workflow's `parts` and for a direct `POST /jobs/{id}/part-analyses`
 * (G8 / G8a). `segmentation_id`, `min_part_faces`, `max_parts` and `split_shells` are never sent by v1.
 */
export function buildPartAnalysisOptions(f: PartOptionsForm, c: BuildContext): PartAnalysisOptions {
  const options: PartAnalysisOptions = {}
  if (f.strategy !== null) options.strategy = f.strategy
  const segmentation = buildSegmentationOptions(f, c)
  if (segmentation !== null) options.segmentation = segmentation
  options.separate = resolveSeparate(f, c.features)
  if (f.views !== null) options.views = Math.min(Math.max(0, Math.round(f.views)), c.features.parts.maxViews)
  if (f.perPartGlbs) options.export = { per_part_glbs: true } // `previews` stays at its default (true)
  return options
}

/** Node Export node params (G7); the server compiles them to `parts.export`. */
export function buildExportParams(f: PartOptionsForm): { per_part_glbs: boolean } {
  return { per_part_glbs: f.perPartGlbs }
}

/** `POST /image-generations/{id}/candidates/{n}/use` body (G6): front slot, primary, new "Prompt: …" set. */
export function buildUseCandidateRequest(): UseCandidateRequest {
  return { slot: 'front', make_primary: true }
}

/** Image workflow source (all three keys always; `primary_image_id` may be `null`, as in G1b). */
export function buildImageSource(referenceSetId: string, primaryImageId: string | null): WorkflowSource {
  return { kind: 'image', reference_set_id: referenceSetId, primary_image_id: primaryImageId }
}

/** Prompt workflow source: one image, imported automatically (`auto_first`, DESIGN §4.4.8). */
export function buildPromptSource(p: PromptForm, c: BuildContext): WorkflowSource {
  return { kind: 'prompt', generation: buildGeneration(p, 1, c) }
}

export interface WorkflowInputs {
  source: WorkflowSource
  partOptions: PartOptionsForm
  settings: GenerationSettings
}

/** Simple-form `WorkflowRequest` (G2, G2a, G3, G4); `name`, `graph` and `layout` are never part of it. */
export function buildWorkflowRequest(i: WorkflowInputs, c: BuildContext): WorkflowRequest {
  const parts = i.partOptions
  const request: WorkflowRequest = { source: i.source }
  // DESIGN §4.7.1: the pre-reconstruction segmentation exists only together with `parts`.
  if (parts.enabled && parts.previewSegmentation) request.segmentation = buildSegmentationOptions(parts, c) ?? {}
  if (i.source.kind !== 'job') request.reconstruction = { settings: i.settings }
  if (parts.enabled) request.parts = buildPartAnalysisOptions(parts, c)
  return request
}
