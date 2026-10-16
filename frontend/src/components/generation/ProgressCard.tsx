import { Activity, Ban, Box, Check, CircleDashed, Clock, LoaderCircle, RotateCcw, X } from 'lucide-react'

import type { Job } from '../../api/types'
import { useNow } from '../../hooks/useNow'
import { formatDuration, secondsSince } from '../../lib/format'
import { PIPELINE_STAGES, STATUS_LABEL, stageState, type StageState } from '../../lib/stages'
import { isTerminal, selectActiveJob, useJobStore } from '../../store/jobStore'
import { useSystemStore } from '../../store/systemStore'
import { Button } from '../common/Button'
import { EmptyState } from '../common/EmptyState'
import { Panel } from '../common/Panel'
import { ProgressBar } from '../common/ProgressBar'
import { StatusBadge } from '../common/StatusBadge'
import styles from './ProgressCard.module.css'

function StageIcon({ state }: { state: StageState }) {
  switch (state) {
    case 'done':
      return <Check size={13} aria-hidden />
    case 'active':
      return <LoaderCircle size={13} className={styles.spin} aria-hidden />
    case 'failed':
      return <X size={13} aria-hidden />
    case 'skipped':
      return <Ban size={12} aria-hidden />
    default:
      return <CircleDashed size={13} aria-hidden />
  }
}

const STATE_TEXT: Record<StageState, string> = {
  done: 'done',
  active: 'in progress',
  pending: 'pending',
  failed: 'failed',
  skipped: 'not run',
}

function elapsedSeconds(job: Job, now: number): number | null {
  if (job.status === 'running') return secondsSince(job.started_at, now) ?? job.progress.elapsed_seconds
  if (job.status === 'queued') return secondsSince(job.created_at, now)
  if (job.started_at && job.finished_at) return (Date.parse(job.finished_at) - Date.parse(job.started_at)) / 1000
  return job.result?.timings.total ?? job.progress.elapsed_seconds
}

function etaSeconds(job: Job, now: number): number | null {
  if (job.status !== 'running' || job.progress.eta_seconds === null) return null
  const sinceUpdate = secondsSince(job.progress.updated_at, now) ?? 0
  return Math.max(0, job.progress.eta_seconds - sinceUpdate)
}

/** Live progress of the active job: overall bar, stage list, step, elapsed/ETA, terminal states. */
export function ProgressCard() {
  const job = useJobStore(selectActiveJob)
  const viewedJobId = useJobStore((s) => s.viewedJobId)
  const view = useJobStore((s) => s.view)
  const retry = useJobStore((s) => s.retry)
  const submitting = useJobStore((s) => s.submitting)
  const engine = useSystemStore((s) => s.system?.engine ?? null)
  const live = !!job && !isTerminal(job)
  const now = useNow(1000, live)

  if (!job) {
    return (
      <Panel id="progress" title="Progress" icon={<Activity size={16} />}>
        <EmptyState
          compact
          icon={<Clock size={18} />}
          title="No active generation"
          description="Progress, stages and ETA appear here once you start a generation."
        />
      </Panel>
    )
  }

  const progress = job.progress
  const percent = job.status === 'completed' ? 100 : progress.percent
  const tone =
    job.status === 'failed' ? 'danger' : job.status === 'cancelled' ? 'warning' : job.status === 'completed' ? 'success' : 'accent'
  const elapsed = elapsedSeconds(job, now)
  const eta = etaSeconds(job, now)
  const shortId = job.id.replace(/^job_/, '').slice(0, 8)
  const waitingForModel = progress.stage === 'waiting_for_model'
  const loadProgress = engine?.load_progress

  return (
    <Panel
      id="progress"
      title="Progress"
      icon={<Activity size={16} />}
      subtitle={`Job ${shortId} · ${job.settings.resolution} · seed ${job.seed}`}
      actions={<StatusBadge status={job.status} />}
    >
      <div className={styles.body}>
        {/* Announce stage/status changes only (the body also holds per-second timers that must stay silent). */}
        <span className="visually-hidden" role="status">
          {`Generation ${STATUS_LABEL[job.status].toLowerCase()}${job.status === 'running' ? `: ${progress.stage_label}` : ''}`}
        </span>
        <div className={styles.overall}>
          <div className={styles.overallRow}>
            <span className={styles.stageLabel}>
              {job.status === 'queued'
                ? job.queue_position
                  ? `Queued — position ${job.queue_position}`
                  : 'Queued'
                : job.status === 'running'
                  ? progress.stage_label
                  : job.status === 'completed'
                    ? 'Completed'
                    : job.status === 'failed'
                      ? 'Failed'
                      : 'Cancelled'}
            </span>
            <span className={styles.percent}>{Math.round(percent)}%</span>
          </div>
          <ProgressBar
            value={job.status === 'queued' && percent === 0 ? null : percent}
            tone={tone}
            size="md"
            label="Overall progress"
          />
          {progress.message && live && <p className={styles.message}>{progress.message}</p>}
        </div>

        {waitingForModel && live && (
          <div className={styles.waiting}>
            <LoaderCircle size={14} className={styles.spin} aria-hidden />
            <span>
              Waiting for the model to load
              {loadProgress ? ` (${loadProgress.loaded}/${loadProgress.total}${loadProgress.current ? ` · ${loadProgress.current}` : ''})` : ''}
              …
            </span>
          </div>
        )}

        <dl className={styles.metrics}>
          <div>
            <dt>Elapsed</dt>
            <dd>{formatDuration(elapsed)}</dd>
          </div>
          <div>
            <dt>ETA</dt>
            <dd>{live ? (eta !== null ? formatDuration(eta) : '—') : '—'}</dd>
          </div>
          <div>
            <dt>Step</dt>
            <dd>
              {live && progress.step !== null && progress.total_steps ? `${progress.step}/${progress.total_steps}` : '—'}
            </dd>
          </div>
        </dl>

        <ol className={styles.stages} aria-label="Pipeline stages">
          {PIPELINE_STAGES.map((info) => {
            const state = stageState(job, info)
            const showSteps = state === 'active' && progress.step !== null && progress.total_steps
            return (
              <li key={info.stage} className={`${styles.stage} ${styles[state] ?? ''}`}>
                <span className={styles.stageIcon}>
                  <StageIcon state={state} />
                </span>
                <span className={styles.stageName}>
                  {info.label}
                  <span className="visually-hidden"> — {STATE_TEXT[state]}</span>
                </span>
                {showSteps && (
                  <span className={styles.stageSteps}>
                    {progress.step}/{progress.total_steps}
                  </span>
                )}
                {state === 'done' && job.result?.timings[info.stage] !== undefined && (
                  <span className={styles.stageTime}>{formatDuration(job.result.timings[info.stage])}</span>
                )}
              </li>
            )
          })}
        </ol>

        {job.status === 'failed' && (
          <div className={styles.errorBox} role="alert">
            <p className={styles.errorTitle}>{job.error?.code ?? 'error'}</p>
            <p className={styles.errorMessage}>{job.error?.message ?? 'The generation failed.'}</p>
          </div>
        )}

        {isTerminal(job) && (
          <div className={styles.actions}>
            {job.status === 'completed' && viewedJobId !== job.id && (
              <Button size="sm" variant="subtle" icon={<Box size={14} />} onClick={() => view(job.id)}>
                Show in viewer
              </Button>
            )}
            {(job.status === 'failed' || job.status === 'cancelled') && (
              <Button
                size="sm"
                variant="secondary"
                icon={<RotateCcw size={14} />}
                loading={submitting}
                onClick={() => void retry(job)}
              >
                Retry
              </Button>
            )}
          </div>
        )}
      </div>
    </Panel>
  )
}
