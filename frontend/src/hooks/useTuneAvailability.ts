import { isRunTerminal, useMeshTunerStore } from '../store/meshTunerStore'
import { useSystemStore } from '../store/systemStore'
import { useViewedTuning } from './useViewedTuning'

export interface TuneAvailability {
  /** Whether "Auto Tune" is enabled. */
  canTune: boolean
  /** Why it is disabled (shown next to the button). */
  reason: string | null
  /** A run of the viewed model is queued/running (show Cancel). */
  busy: boolean
  /** The Blender runtime needs the user's attention (missing / downloading / error) — offer "Set up Blender". */
  needsSetup: boolean
  /** The viewed completed job id, when there is one. */
  jobId: string | null
}

/** Single source of truth for whether Auto Tune can start for the model in the viewer, and why not. */
export function useTuneAvailability(): TuneAvailability {
  const reachable = useSystemStore((s) => s.reachable)
  const { job, latestRun } = useViewedTuning()
  const runtimeState = useMeshTunerStore((s) => s.runtime?.state ?? null)
  const runtimeError = useMeshTunerStore((s) => s.runtime?.error ?? null)
  const runtimeLoad = useMeshTunerStore((s) => s.runtimeStatus)
  const minVersion = useMeshTunerStore((s) => s.runtime?.min_version ?? '5.1')
  const hasSelection = useMeshTunerStore((s) => !!s.runtime?.selected)
  const starting = useMeshTunerStore((s) => (job ? s.startingJobIds.includes(job.id) : false))

  const busy = !!latestRun && !isRunTerminal(latestRun)
  const needsSetup =
    runtimeState === 'needs_download' || runtimeState === 'downloading' || runtimeState === 'error' || (runtimeState === 'ready' && !hasSelection)

  let reason: string | null = null
  if (reachable === false) reason = 'The backend is unreachable.'
  else if (!job) reason = 'Open a completed model to tune it.'
  else if (runtimeState === null)
    reason = runtimeLoad === 'error' ? 'The Blender runtime is not available on this backend.' : 'Checking the Blender runtime…'
  else if (runtimeState === 'detecting') reason = 'Detecting Blender installations…'
  else if (runtimeState === 'needs_download') reason = `Blender ${minVersion} or newer is required — set up Blender first.`
  else if (runtimeState === 'downloading') reason = 'Blender is being installed…'
  else if (runtimeState === 'error') reason = runtimeError ?? 'The Blender runtime reported an error.'
  else if (!hasSelection) reason = 'No compatible Blender is selected.'
  else if (busy) reason = 'Auto Tune is already running for this model.'
  else if (starting) reason = 'Starting…'

  return { canTune: reason === null, reason, busy, needsSetup, jobId: job?.id ?? null }
}
