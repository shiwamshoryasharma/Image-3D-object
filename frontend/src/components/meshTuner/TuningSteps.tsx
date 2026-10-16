import { Ban, Check, CircleDashed, LoaderCircle, TriangleAlert, X } from 'lucide-react'

import type { TuningRun, TuningStepStatus } from '../../api/types'
import { useNow } from '../../hooks/useNow'
import { formatDuration, secondsSince } from '../../lib/format'
import { STEP_STATUS_TEXT, TUNING_STEP_PLAN } from '../../lib/meshTuner'
import { STATUS_LABEL } from '../../lib/stages'
import { isRunTerminal } from '../../store/meshTunerStore'
import { ProgressBar } from '../common/ProgressBar'
import styles from './TuningSteps.module.css'

function StepIcon({ status }: { status: TuningStepStatus }) {
  switch (status) {
    case 'done':
      return <Check size={13} aria-hidden />
    case 'running':
      return <LoaderCircle size={13} className={styles.spin} aria-hidden />
    case 'warning':
      return <TriangleAlert size={12} aria-hidden />
    case 'failed':
      return <X size={13} aria-hidden />
    case 'skipped':
      return <Ban size={12} aria-hidden />
    default:
      return <CircleDashed size={13} aria-hidden />
  }
}

function elapsedSeconds(run: TuningRun, now: number): number | null {
  if (run.status === 'running') return secondsSince(run.started_at, now)
  if (run.status === 'queued') return secondsSince(run.created_at, now)
  if (run.started_at && run.finished_at) return (Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000
  return null
}

/** Overall progress + the live 8-step checklist (load → analyze → repair → face → topology → rig → validate → save). */
export function TuningSteps({ run }: { run: TuningRun }) {
  const live = !isRunTerminal(run)
  const now = useNow(1000, live)
  const steps = run.steps.length > 0 ? run.steps : TUNING_STEP_PLAN
  const running = steps.find((step) => step.status === 'running') ?? null
  const percent = run.status === 'completed' ? 100 : run.progress.percent
  const tone =
    run.status === 'failed' ? 'danger' : run.status === 'cancelled' ? 'warning' : run.status === 'completed' ? 'success' : 'accent'
  const headline =
    run.status === 'queued'
      ? 'Waiting for the Blender worker…'
      : run.status === 'running'
        ? (running?.label ?? 'Starting Blender…')
        : STATUS_LABEL[run.status]
  const elapsed = elapsedSeconds(run, now)

  return (
    <div className={styles.root} data-reveal="tuning-steps">
      {/* Announce status/step changes only; timers and percentages stay silent. */}
      <span className="visually-hidden" role="status">
        {`Auto Tune ${STATUS_LABEL[run.status].toLowerCase()}${running ? `: ${running.label}` : ''}`}
      </span>
      <div className={styles.overall}>
        <div className={styles.overallRow}>
          <span className={styles.headline}>{headline}</span>
          <span className={styles.percent}>{Math.round(percent)}%</span>
        </div>
        <ProgressBar value={run.status === 'queued' ? null : percent} tone={tone} size="sm" label="Auto Tune progress" />
        <div className={styles.metaRow}>
          <span className={styles.message}>{live ? (run.progress.message ?? '') : ''}</span>
          <span className={styles.elapsed}>{formatDuration(elapsed)}</span>
        </div>
      </div>

      <ol className={styles.steps} aria-label="Auto Tune steps">
        {steps.map((step) => (
          <li key={step.id} className={`${styles.step} ${styles[step.status] ?? ''}`}>
            <span className={styles.icon}>
              <StepIcon status={step.status} />
            </span>
            <span className={styles.text}>
              <span className={styles.label}>
                {step.label}
                <span className="visually-hidden"> — {STEP_STATUS_TEXT[step.status]}</span>
              </span>
              {step.message && step.status !== 'pending' && <span className={styles.stepMessage}>{step.message}</span>}
            </span>
            {step.duration_s !== null && step.status !== 'running' && step.status !== 'pending' && (
              <span className={styles.time}>{formatDuration(step.duration_s)}</span>
            )}
          </li>
        ))}
      </ol>
    </div>
  )
}
