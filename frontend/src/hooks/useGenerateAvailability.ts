import { isTerminal, selectActiveJob, useJobStore } from '../store/jobStore'
import { useReferenceStore } from '../store/referenceStore'
import { useSystemStore } from '../store/systemStore'

export interface GenerateAvailability {
  /** Whether the Generate button is enabled. */
  canGenerate: boolean
  /** Why it is disabled (shown under the button). */
  reason: string | null
  /** Informational note when generation is allowed but will wait (engine loading). */
  note: string | null
  /** A job from this session is queued/running (show Cancel). */
  busy: boolean
  /** Engine-level blocker (unavailable / failed to load): short summary + full diagnostic text, retryable. */
  engineProblem: EngineProblem | null
}

export interface EngineProblem {
  /** One-line summary suitable for the card (the backend's status message). */
  summary: string
  /** Full multi-line diagnostic (how to fix), shown collapsed. */
  details: string | null
}

/** The backend's status message ends with a pointer to the error text; the UI shows that text as "Details". */
function summarize(message: string | null | undefined, fallback: string): string {
  const text = (message ?? '').replace(/\s*\(see error for how to fix\)\s*$/i, '').trim()
  return text || fallback
}

/** Single source of truth for whether a new generation can be queued and why not. */
export function useGenerateAvailability(): GenerateAvailability {
  const reachable = useSystemStore((s) => s.reachable)
  const system = useSystemStore((s) => s.system)
  const referenceSet = useReferenceStore((s) => s.set)
  const uploading = useReferenceStore((s) => s.uploads.length > 0)
  const activeJob = useJobStore(selectActiveJob)
  const submitting = useJobStore((s) => s.submitting)

  const busy = !!activeJob && !isTerminal(activeJob)
  const engine = system?.engine
  const queue = system?.queue

  let reason: string | null = null
  let note: string | null = null
  let engineProblem: EngineProblem | null = null

  // Engine blockers are reported regardless of the reference state so the user sees them before uploading.
  if (engine?.state === 'unavailable') {
    engineProblem = { summary: summarize(engine.message, 'The 3D engine is unavailable.'), details: engine.error }
  } else if (engine?.state === 'error') {
    engineProblem = { summary: summarize(engine.message, 'The model failed to load.'), details: engine.error }
  }

  if (reachable === false) reason = 'The backend is unreachable.'
  else if (!system) reason = 'Waiting for the backend…'
  else if (engineProblem) reason = engineProblem.summary
  else if (!referenceSet || referenceSet.images.length === 0) reason = 'Add at least one reference image.'
  else if (uploading) reason = 'Wait for uploads to finish.'
  else if (busy) reason = 'A generation is already in progress.'
  else if (queue && queue.pending >= queue.max_size) reason = 'The generation queue is full.'
  else if (submitting) reason = 'Submitting…'

  if (!reason && engine && engine.state !== 'ready') {
    const progress = engine.load_progress
    note =
      engine.state === 'loading'
        ? `Model is loading${progress ? ` (${progress.loaded}/${progress.total})` : ''} — the job will start when it is ready.`
        : 'The model is not loaded yet — it will load and the job will start automatically.'
  } else if (!reason && queue && (queue.pending > 0 || queue.running_job_id)) {
    note = `${queue.pending + (queue.running_job_id ? 1 : 0)} job(s) ahead in the queue.`
  }

  return { canGenerate: reason === null, reason, note, busy, engineProblem: reachable === false ? null : engineProblem }
}
