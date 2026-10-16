/**
 * State and commands of the shared Generate control (UI_DESIGN §4.1; rendered by `components/generation/
 * GenerateAction.tsx` as card / bar / fab / rail / toolbar). Phases (first match): cancelling · submitting ·
 * running · queued · ready · blocked.
 */
import { cancelActiveRun, startGeneration } from '../actions/generation'
import type { GenerateAvailability } from '../lib/generateAvailability.ts'
import { useJobStore } from '../store/jobStore'
import { useRunStore } from '../store/runStore'
import { useSystemStore } from '../store/systemStore'
import { useActiveRun, type ActiveRunView } from './useActiveRun'
import { useGenerateAvailability } from './useGenerateAvailability'

export type GenerateUiPhase = 'blocked' | 'ready' | 'submitting' | 'queued' | 'running' | 'cancelling'

export interface GenerateActionState {
  phase: GenerateUiPhase
  availability: GenerateAvailability
  activeRun: ActiveRunView | null
  /** 0..100 of the active run, `null` while unknown or idle. */
  percent: number | null
  /** The engine supports cancelling a job (workflows and image generations are always cancellable). */
  supportsCancel: boolean
  /** 'Generate 3D model' | 'Generate images' | 'Submitting…' | 'Cancelling…' */
  label: string
  /** Accessible name for icon-only variants (FAB, rail, toolbar). */
  ariaLabel: string
  run: () => void
  cancel: () => void
}

function phaseOf(
  availability: GenerateAvailability,
  activeRun: ActiveRunView | null,
  submitting: boolean,
  cancelling: boolean,
): GenerateUiPhase {
  if (activeRun && cancelling) return 'cancelling'
  if (submitting) return 'submitting'
  if (activeRun?.status === 'running') return 'running'
  if (activeRun?.status === 'queued') return 'queued'
  return availability.canGenerate ? 'ready' : 'blocked'
}

export function useGenerateAction(): GenerateActionState {
  const availability = useGenerateAvailability()
  const activeRun = useActiveRun()
  const activeId = activeRun?.id ?? null
  const jobCancelling = useJobStore((s) => (activeId ? s.cancellingIds.includes(activeId) : false))
  const runCancelling = useRunStore((s) => (activeId ? s.cancellingIds.includes(activeId) : false))
  const jobSubmitting = useJobStore((s) => s.submitting)
  const runSubmitting = useRunStore((s) => s.submitting)
  const engineCancels = useSystemStore((s) => s.system?.engine.capabilities.supports_cancel ?? true)

  const phase = phaseOf(availability, activeRun, jobSubmitting || runSubmitting, jobCancelling || runCancelling)
  const images = availability.plan === 'image_generation'
  let label = images ? 'Generate images' : 'Generate 3D model'
  if (phase === 'submitting') label = 'Submitting…'
  else if (phase === 'cancelling') label = 'Cancelling…'

  let ariaLabel = images ? 'Generate images from the prompt' : 'Generate 3D model'
  if (phase === 'submitting' || phase === 'cancelling') ariaLabel = label
  else if (phase === 'queued') ariaLabel = 'Generation queued: show progress'
  else if (phase === 'running') {
    const percent = activeRun?.percent
    ariaLabel = `Generation running${percent == null ? '' : ` (${Math.round(percent)}%)`}: show progress`
  }

  return {
    phase,
    availability,
    activeRun,
    percent: activeRun?.percent ?? null,
    supportsCancel: activeRun?.kind === 'job' ? engineCancels : true,
    label,
    ariaLabel,
    run: () => {
      void startGeneration()
    },
    cancel: () => {
      void cancelActiveRun()
    },
  }
}
