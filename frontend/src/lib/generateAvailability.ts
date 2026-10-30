/**
 * Whether a new generation can start and why not (UI_DESIGN.md §2.10). `hooks/useGenerateAvailability.ts` wraps this
 * pure function; `store/planFromStores.ts → availabilityFromStores()` gathers the inputs.
 *
 * Rule order (first match wins). Rules marked "same" return the exact strings of today's hook
 * (`useGenerateAvailability.ts:L55-73` before this milestone), pinned by `generate_availability.test.mjs`:
 *   1 unreachable (same) · 2 waiting for the backend (same) · 3 engine problem (same) · 4 Node pipeline block ·
 *   5a–5e prompt source · 6a no images (same) · 6b uploading (same) · 7a/7b part-aware output ·
 *   8 busy (same) · 9a job queue full (same) · 9b run queue full · 10 submitting (same).
 * While the Node shell is mounted (`nodeShellActive`), rules 5–7 are skipped: Node validation covers them and rule 4
 * carries its first error.
 *
 * PURE (UI_DESIGN §1.5): relative `.ts` value imports only; no React, zustand, axios or browser globals.
 */
import type { RunStatus } from '../api/partAwareTypes.ts'
import type { JobStatus, SystemStatus } from '../api/types.ts'
import {
  PLAN_TEXT,
  effectivePartsEnabled,
  effectiveSource,
  planGeneration,
  reasonClause,
  type GenerationPlan,
  type PlanInputs,
} from './generatePlan.ts'

export interface EngineProblem {
  /** One-line summary suitable for the card (the backend's status message). */
  summary: string
  /** Full multi-line diagnostic (how to fix), shown collapsed. */
  details: string | null
}

export type AvailabilityCode =
  | 'unreachable'
  | 'waiting_backend'
  | 'engine_problem'
  | 'pipeline_invalid'
  | 'features_loading'
  | 'features_error'
  | 'empty_prompt'
  | 'prompt_too_long'
  | 'workflows_unavailable'
  | 'no_references'
  | 'uploading'
  | 'parts_unavailable'
  | 'busy'
  | 'queue_full'
  | 'run_queue_full'
  | 'submitting'

/** Today's `GenerateAvailability` shape plus `code` and `plan`. */
export interface GenerateAvailability {
  /** Whether the Generate button is enabled. */
  canGenerate: boolean
  /** Why it is disabled (shown under the button). */
  reason: string | null
  /** Informational note when generation is allowed but will wait (engine loading, queue, cold start). */
  note: string | null
  /** A job, workflow or review image generation from this session is queued/running (show Cancel). */
  busy: boolean
  /** Engine-level blocker (unavailable / failed to load): short summary + full diagnostic text, retryable. */
  engineProblem: EngineProblem | null
  /** Machine-readable `reason` (null when generation is possible). */
  code: AvailabilityCode | null
  /** Kind of call a Generate press makes; `null` while the Node shell decides (`nodeShellActive`). */
  plan: GenerationPlan['kind'] | null
}

export interface AvailabilityInputs {
  /** `systemStore.reachable` (`null` = not known yet). */
  reachable: boolean | null
  system: Pick<SystemStatus, 'engine' | 'queue'> | null
  /** Everything `planGeneration` needs (features, source, prompt, part options, references, settings). */
  plan: PlanInputs
  /** Reference uploads in flight. */
  uploading: boolean
  activeJobStatus: JobStatus | null
  activeWorkflowStatus: RunStatus | null
  reviewGenerationStatus: RunStatus | null
  /** Job store or run store is submitting. */
  submitting: boolean
  /** `navigationStore.pipelineBlock` (set only while the Node shell is mounted, UI10). */
  pipelineBlock: string | null
  /** `navigationStore.runOverride !== null`: the Node shell is mounted and runs its own graph. */
  nodeShellActive: boolean
}

const TERMINAL: ReadonlySet<string> = new Set(['completed', 'failed', 'cancelled'])
const isActive = (status: string | null): boolean => status !== null && !TERMINAL.has(status)

/** The backend's status message ends with a pointer to the error text; the UI shows that text as "Details". */
function summarize(message: string | null | undefined, fallback: string): string {
  const text = (message ?? '').replace(/\s*\(see error for how to fix\)\s*$/i, '').trim()
  return text || fallback
}

function engineProblemOf(system: AvailabilityInputs['system']): EngineProblem | null {
  const engine = system?.engine
  // Engine blockers are reported regardless of the reference state so the user sees them before uploading.
  if (engine?.state === 'unavailable') {
    return { summary: summarize(engine.message, 'The 3D engine is unavailable.'), details: engine.error }
  }
  if (engine?.state === 'error') {
    return { summary: summarize(engine.message, 'The model failed to load.'), details: engine.error }
  }
  return null
}

interface Blocker {
  reason: string
  code: AvailabilityCode
}

/** Rules 5–7: inputs and capabilities (skipped while the Node shell validates its own graph). */
function inputBlocker(i: AvailabilityInputs): Blocker | null {
  const { features: f, prompt } = i.plan
  if (effectiveSource(i.plan.source, f) === 'prompt') {
    if (f.status === 'loading') return { reason: PLAN_TEXT.featuresLoading, code: 'features_loading' }
    if (f.status === 'error') return { reason: PLAN_TEXT.featuresError, code: 'features_error' }
    const length = prompt.prompt.trim().length
    if (length === 0) return { reason: PLAN_TEXT.emptyPrompt, code: 'empty_prompt' }
    if (length > f.prompt.maxChars) return { reason: PLAN_TEXT.promptTooLong(f.prompt.maxChars), code: 'prompt_too_long' }
    if (prompt.flow === 'one_shot' && !f.workflow.available) {
      return { reason: PLAN_TEXT.oneShotNeedsWorkflows(f.workflow), code: 'workflows_unavailable' }
    }
  } else {
    const references = i.plan.references
    if (!references || references.imageCount === 0) return { reason: PLAN_TEXT.noReferences, code: 'no_references' }
    if (i.uploading) return { reason: 'Wait for uploads to finish.', code: 'uploading' }
  }
  if (effectivePartsEnabled(i.plan.parts, f)) {
    if (!f.parts.available) {
      return { reason: `Part-aware output is unavailable: ${reasonClause(f.parts)}. Turn it off to generate.`, code: 'parts_unavailable' }
    }
    if (!f.workflow.available) {
      return { reason: `Part-aware output needs workflows: ${reasonClause(f.workflow)}. Turn it off to generate.`, code: 'workflows_unavailable' }
    }
    const strategy = f.parts.strategies.find((item) => item.id === (i.plan.parts.strategy ?? 'semantic_parts'))
    if (!strategy?.available) {
      const reason = strategy ? reasonClause(strategy) : 'semantic segmentation models are unavailable'
      return { reason: `Part-aware output is unavailable: ${reason}. Turn it off to generate.`, code: 'parts_unavailable' }
    }
  }
  return null
}

interface Derived {
  plan: GenerationPlan['kind'] | null
  needsReconstruction: boolean
  busy: boolean
  engineProblem: EngineProblem | null
}

function firstBlocker(i: AvailabilityInputs, d: Derived): Blocker | null {
  const { system } = i
  if (i.reachable === false) return { reason: 'The backend is unreachable.', code: 'unreachable' }
  if (!system) return { reason: 'Waiting for the backend…', code: 'waiting_backend' }
  if (d.engineProblem) return { reason: d.engineProblem.summary, code: 'engine_problem' }
  if (i.pipelineBlock !== null) return { reason: `Fix the workflow: ${i.pipelineBlock}`, code: 'pipeline_invalid' }
  if (!i.nodeShellActive) {
    const input = inputBlocker(i)
    if (input) return input
  }
  if (d.busy) return { reason: 'A generation is already in progress.', code: 'busy' }
  const queue = system.queue
  if (d.needsReconstruction && queue && queue.pending >= queue.max_size) {
    return { reason: 'The generation queue is full.', code: 'queue_full' }
  }
  const runQueue = i.plan.features.runQueue
  if ((d.plan === 'workflow' || d.plan === 'image_generation') && runQueue && runQueue.pending >= runQueue.maxSize) {
    return { reason: 'The run queue is full.', code: 'run_queue_full' }
  }
  if (i.submitting) return { reason: 'Submitting…', code: 'submitting' }
  return null
}

/** Single source of truth for whether a new generation can be queued and why not. */
export function generateAvailability(i: AvailabilityInputs): GenerateAvailability {
  const engine = i.system?.engine
  const queue = i.system?.queue
  const features = i.plan.features
  const plan = i.nodeShellActive ? null : planGeneration(i.plan).kind
  const derived: Derived = {
    plan,
    // A blocked plan (only reachable with the Node shell) counts as a 3D plan: today's queue rules stay.
    needsReconstruction: plan !== 'image_generation',
    busy: isActive(i.activeJobStatus) || isActive(i.activeWorkflowStatus) || isActive(i.reviewGenerationStatus),
    engineProblem: engineProblemOf(i.system),
  }
  const { needsReconstruction, busy, engineProblem } = derived
  const blocker = firstBlocker(i, derived)

  let note: string | null = null
  if (!blocker) {
    const promptPlan = !i.nodeShellActive && effectiveSource(i.plan.source, features) === 'prompt'
    if (promptPlan && features.prompt.defaultModelState === 'installed') {
      const coldStartS = features.prompt.coldStartS
      note = coldStartS !== null && coldStartS > 0
        ? `The text-to-image model loads first (about ${Math.ceil(coldStartS / 60)} min).`
        : 'The text-to-image model loads first.'
    } else if (needsReconstruction && engine && engine.state !== 'ready') {
      const progress = engine.load_progress
      note =
        engine.state === 'loading'
          ? `Model is loading${progress ? ` (${progress.loaded}/${progress.total})` : ''} — the job will start when it is ready.`
          : 'The model is not loaded yet — it will load and the job will start automatically.'
    } else if (needsReconstruction && queue && (queue.pending > 0 || queue.running_job_id)) {
      note = `${queue.pending + (queue.running_job_id ? 1 : 0)} job(s) ahead in the queue.`
    }
  }

  return {
    canGenerate: blocker === null,
    reason: blocker?.reason ?? null,
    note,
    busy,
    engineProblem: i.reachable === false ? null : engineProblem,
    code: blocker?.code ?? null,
    plan,
  }
}
