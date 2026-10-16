/**
 * Whether a new generation can be queued and why not (UI_DESIGN §2.10): a thin wrapper over the pure
 * `lib/generateAvailability.ts`. Image-source reasons and notes are byte-identical to the pre-milestone hook (pinned
 * by `generate_availability.test.mjs`); the shape adds `code` and `plan`.
 */
import { generateAvailability, type GenerateAvailability } from '../lib/generateAvailability.ts'
import { useFeaturesStore } from '../store/featuresStore'
import { useInputStore } from '../store/inputStore'
import { selectActiveJob, useJobStore } from '../store/jobStore'
import { useNavigationStore } from '../store/navigationStore'
import { usePartOptionsStore } from '../store/partOptionsStore'
import { availabilityInputsOf } from '../store/planFromStores'
import { useReferenceStore } from '../store/referenceStore'
import { selectActiveWorkflow, selectReviewGeneration, useRunStore } from '../store/runStore'
import { useSettingsStore } from '../store/settingsStore'
import { useSystemStore } from '../store/systemStore'

export type { AvailabilityCode, EngineProblem, GenerateAvailability } from '../lib/generateAvailability.ts'

/** Single source of truth for whether a new generation can be queued and why not. */
export function useGenerateAvailability(): GenerateAvailability {
  const reachable = useSystemStore((s) => s.reachable)
  const system = useSystemStore((s) => s.system)
  const features = useFeaturesStore((s) => s.snapshot)
  const source = useInputStore((s) => s.source)
  const prompt = useInputStore((s) => s.prompt)
  const parts = usePartOptionsStore((s) => s.form)
  const referenceSet = useReferenceStore((s) => s.set)
  const uploading = useReferenceStore((s) => s.uploads.length > 0)
  const form = useSettingsStore((s) => s.form)
  const activeJobStatus = useJobStore((s) => selectActiveJob(s)?.status ?? null)
  const activeWorkflowStatus = useRunStore((s) => selectActiveWorkflow(s)?.status ?? null)
  const reviewGenerationStatus = useRunStore((s) => selectReviewGeneration(s)?.status ?? null)
  const jobSubmitting = useJobStore((s) => s.submitting)
  const runSubmitting = useRunStore((s) => s.submitting)
  const pipelineBlock = useNavigationStore((s) => s.pipelineBlock)
  const nodeShellActive = useNavigationStore((s) => s.runOverride !== null)

  return generateAvailability(
    availabilityInputsOf({
      features,
      source,
      prompt,
      parts,
      referenceSet,
      form,
      system,
      reachable,
      uploading,
      activeJobStatus,
      activeWorkflowStatus,
      reviewGenerationStatus,
      submitting: jobSubmitting || runSubmitting,
      pipelineBlock,
      nodeShellActive,
    }),
  )
}
