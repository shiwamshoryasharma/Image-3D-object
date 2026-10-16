/**
 * The run that Generate started and that is still queued or running (UI_DESIGN §4.1): the active workflow first
 * (its job is adopted, so the workflow is the outer run), else the active job, else the review image generation.
 * `null` when nothing is in flight.
 */
import type { RunStatus } from '../api/partAwareTypes.ts'
import { currentStep, runPercent, runStepViews, workflowStepViews } from '../lib/runSteps.ts'
import { PIPELINE_STAGES } from '../lib/stages'
import { isTerminal, selectActiveJob, useJobStore } from '../store/jobStore'
import { selectActiveWorkflow, selectReviewGeneration, useRunStore } from '../store/runStore'

export interface ActiveRunView {
  kind: 'job' | 'workflow' | 'image_generation'
  id: string
  status: RunStatus
  /** 0..100, `null` while unknown. */
  percent: number | null
  stepLabel: string | null
}

export function useActiveRun(): ActiveRunView | null {
  const workflow = useRunStore(selectActiveWorkflow)
  const job = useJobStore(selectActiveJob)
  const review = useRunStore(selectReviewGeneration)

  if (workflow && !isTerminal(workflow)) {
    const step = currentStep(workflowStepViews(workflow.steps))
    return {
      kind: 'workflow',
      id: workflow.id,
      status: workflow.status,
      percent: runPercent(workflow.progress),
      stepLabel: step?.label ?? null,
    }
  }
  if (job && !isTerminal(job)) {
    const stage = PIPELINE_STAGES.find((info) => info.stage === job.progress.stage)
    return {
      kind: 'job',
      id: job.id,
      status: job.status,
      percent: runPercent(job.progress),
      stepLabel: stage?.label ?? (job.progress.stage_label || null),
    }
  }
  if (review && !isTerminal(review)) {
    const step = currentStep(runStepViews(review.steps, review.warnings))
    return {
      kind: 'image_generation',
      id: review.id,
      status: review.status,
      percent: runPercent(review.progress),
      stepLabel: step?.label ?? null,
    }
  }
  return null
}
