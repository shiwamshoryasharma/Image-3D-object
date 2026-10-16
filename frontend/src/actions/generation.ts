/**
 * Generation commands shared by every shell (UI_DESIGN §2.9, §5.3). Every Generate trigger (Default card/bar,
 * Mobile FAB, Tablet rail, Node toolbar with a `job` graph, Ctrl/Cmd+Enter) calls `startGeneration()`.
 * Layer (§1.4): stores, api, lib; no React.
 */
import { getImageGeneration } from '../api/imageGenerations'
import type { ImageGenerationRequest } from '../api/partAwareTypes.ts'
import { getReferenceSet } from '../api/references'
import { announce } from '../store/announcerStore'
import { useInputStore } from '../store/inputStore'
import { isTerminal, selectActiveJob, useJobStore } from '../store/jobStore'
import { useNavigationStore } from '../store/navigationStore'
import { availabilityFromStores, planFromStores } from '../store/planFromStores'
import { useReferenceStore } from '../store/referenceStore'
import { selectActiveWorkflow, selectReviewGeneration, useRunStore } from '../store/runStore'
import { useSystemStore } from '../store/systemStore'
import { toast } from '../store/toastStore'

let shortcutInFlight = false

/** The one Generate entry point. While the Node shell is mounted it runs the Node graph instead (`runOverride`). */
export async function startGeneration(): Promise<boolean> {
  const override = useNavigationStore.getState().runOverride
  if (override) return override()
  return startDefaultGeneration()
}

/** Steps 1–5 of §2.9 without the Node override (also used by Node `job` graphs and "Use & generate 3D"). */
export async function startDefaultGeneration(): Promise<boolean> {
  const availability = availabilityFromStores()
  if (!availability.canGenerate) {
    announce(availability.reason ?? 'Generation is not possible right now.', 'assertive')
    return false
  }
  const plan = planFromStores()
  if (plan.kind === 'blocked') {
    toast.warning('Cannot generate yet', plan.reason)
    announce(plan.reason, 'assertive')
    return false
  }
  // The worker waits for the model; make sure a load is actually in progress (today's GenerateCard rule).
  const { system, loadEngine } = useSystemStore.getState()
  if (plan.needsEngine && system?.engine.state === 'not_loaded') void loadEngine()

  let ok: boolean
  if (plan.kind === 'job') ok = await useJobStore.getState().submit()
  else if (plan.kind === 'workflow') ok = (await useRunStore.getState().startWorkflow(plan.request)) !== null
  else ok = (await useRunStore.getState().startImageGeneration(plan.request)) !== null

  if (ok) {
    useNavigationStore.getState().requestReveal(plan.kind === 'image_generation' ? 'review' : 'viewer')
    announce('Generation queued')
  }
  return ok
}

/** "Use & generate 3D": the candidate becomes the front reference of a new set, then the image path runs. */
export async function adoptCandidateAndGenerate(generationId: string, index: number): Promise<boolean> {
  const used = await useRunStore.getState().adoptCandidate(generationId, index)
  if (!used) return false
  useReferenceStore.getState().adoptSet(used.set)
  useInputStore.getState().setSource('image')
  toast.info('Image added', 'The generated image is now the front reference.')
  return startDefaultGeneration()
}

/** "Use as reference": the same adoption without generating. */
export async function adoptCandidateAsReference(generationId: string, index: number): Promise<boolean> {
  const used = await useRunStore.getState().adoptCandidate(generationId, index)
  if (!used) return false
  useReferenceStore.getState().adoptSet(used.set)
  useInputStore.getState().setSource('image')
  toast.info('Image added as the front reference')
  announce('Image added as the front reference')
  return true
}

/** Cancel what Generate started: the active workflow (cascades to its job), else the job, else the review images. */
export async function cancelActiveRun(): Promise<void> {
  const run = useRunStore.getState()
  const workflow = selectActiveWorkflow(run)
  if (workflow && !isTerminal(workflow)) {
    await run.cancel('workflow', workflow.id)
    return
  }
  const job = selectActiveJob(useJobStore.getState())
  if (job && !isTerminal(job)) {
    await useJobStore.getState().cancel(job.id)
    return
  }
  const review = selectReviewGeneration(run)
  if (review && !isTerminal(review)) await run.cancel('image_generation', review.id)
}

/** Ctrl/Cmd+Enter (registered once by `app/GlobalShortcuts.tsx`). */
export function runGenerateShortcut(): void {
  if (shortcutInFlight || availabilityFromStores().busy) return
  // Numeric fields commit their draft on blur; blur synchronously so the shortcut submits what was typed.
  const focused = typeof document !== 'undefined' ? document.activeElement : null
  if (focused instanceof HTMLInputElement || focused instanceof HTMLTextAreaElement) focused.blur()
  shortcutInFlight = true
  void startGeneration().finally(() => {
    shortcutInFlight = false
  })
}

async function promptOfJob(jobId: string): Promise<ImageGenerationRequest | null> {
  const run = useRunStore.getState()
  const workflowId = run.workflowByJobId[jobId]
  const source = workflowId ? run.workflows[workflowId]?.request.source : undefined
  if (source?.kind === 'prompt') return source.generation
  // Review-first jobs: the job's front image came from an image generation.
  const job = useJobStore.getState().jobs[jobId]
  if (!job) return null
  try {
    const set = await getReferenceSet(job.reference_set_id)
    const imageId = job.primary_image_id ?? set.primary_image_id
    const generationId = set.images.find((image) => image.id === imageId)?.source?.image_generation_id
    if (!generationId) return null
    return (await getImageGeneration(generationId)).options
  } catch {
    return null
  }
}

/** History "Regenerate from prompt": load the job's prompt into the form, switch to the prompt source, generate. */
export async function regenerateFromPrompt(jobId: string): Promise<boolean> {
  const request = await promptOfJob(jobId)
  if (!request) {
    toast.warning('No prompt found', 'This model was not generated from a prompt.')
    return false
  }
  useInputStore.getState().loadPrompt(request)
  useInputStore.getState().setSource('prompt')
  return startGeneration()
}

/** History "Reuse prompt": load the job's prompt into the form without generating. */
export async function reusePrompt(jobId: string): Promise<boolean> {
  const request = await promptOfJob(jobId)
  if (!request) {
    toast.warning('No prompt found', 'This model was not generated from a prompt.')
    return false
  }
  useInputStore.getState().loadPrompt(request)
  useInputStore.getState().setSource('prompt')
  useNavigationStore.getState().requestReveal('prompt')
  return true
}
