import { Ban, Check, CircleDashed, LoaderCircle, TriangleAlert, X } from 'lucide-react'

import { fileUrl } from '../../api/files'
import type { StepView, StepViewStatus } from '../../lib/runSteps.ts'
import { ProgressBar } from '../common/ProgressBar'
import styles from './PipelineSteps.module.css'

function StepIcon({ status }: { status: StepViewStatus }) {
  switch (status) {
    case 'done':
      return <Check size={12} aria-hidden />
    case 'running':
      return <LoaderCircle size={12} className={styles.spin} aria-hidden />
    case 'failed':
      return <X size={12} aria-hidden />
    case 'warning':
      return <TriangleAlert size={12} aria-hidden />
    case 'skipped':
    case 'cancelled':
      return <Ban size={11} aria-hidden />
    default:
      return <CircleDashed size={12} aria-hidden />
  }
}

const STATUS_TEXT: Readonly<Record<StepViewStatus, string>> = Object.freeze({
  pending: 'pending',
  running: 'in progress',
  done: 'done',
  warning: 'done with warnings',
  skipped: 'not run',
  failed: 'failed',
  cancelled: 'cancelled',
})

/** Step artifacts drawn as a thumbnail (image roles only; other artifacts are files). */
const IMAGE_ROLE = /thumb|image|preview|candidate|overlay/i

export interface PipelineStepsProps {
  /** Step views from `lib/runSteps` (`workflowStepViews` / `runStepViews`). */
  steps: readonly StepView[]
  /** Accessible name of the list and the visible header. */
  title: string
  /** Overall percent (0..100); `undefined` hides the bar, `null` = indeterminate. */
  percent?: number | null
  /** The run is still queued or running (shows the bar). */
  live?: boolean
}

/**
 * Live step list (UI_DESIGN §5.3, §5.4, §6.3): one row per step with an icon + text state (never colour alone), the
 * server's step message while it runs (e.g. "Waiting for the GPU (generation queued)"), warning counts and the step
 * artifact thumbnail (e.g. the text-to-image candidate) once done. Status changes are announced by the stores, not
 * here (no per-frame live region).
 */
export function PipelineSteps({ steps, title, percent, live = false }: PipelineStepsProps) {
  return (
    <div className={styles.root} data-ui-addition="pipeline-steps">
      <div className={styles.header}>
        <span>{title}</span>
        {percent !== undefined && percent !== null && <span className={styles.percent}>{Math.round(percent)}%</span>}
      </div>
      {live && percent !== undefined && <ProgressBar value={percent} size="sm" label={`${title} progress`} />}
      <ol className={styles.steps} aria-label={title}>
        {steps.map((step) => {
          const thumb =
            step.status === 'done' || step.status === 'warning'
              ? step.artifacts.find((artifact) => IMAGE_ROLE.test(artifact.role))
              : undefined
          return (
            <li key={step.id} className={`${styles.step} ${styles[step.status] ?? ''}`}>
              <span className={styles.icon}>
                <StepIcon status={step.status} />
              </span>
              <span className={styles.name}>
                {step.label}
                <span className="visually-hidden"> — {STATUS_TEXT[step.status]}</span>
              </span>
              <span className={styles.aside}>
                {step.warningCount > 0 && (
                  <span>
                    {step.warningCount} warning{step.warningCount === 1 ? '' : 's'}
                  </span>
                )}
                {step.status === 'running' && step.percent !== null && <span>{Math.round(step.percent)}%</span>}
              </span>
              {step.message && (step.status === 'running' || step.status === 'failed') && (
                <span className={styles.message}>{step.message}</span>
              )}
              {thumb && <img className={styles.thumb} src={fileUrl(thumb.url)} alt={`${step.label} result`} loading="lazy" />}
            </li>
          )
        })}
      </ol>
    </div>
  )
}
