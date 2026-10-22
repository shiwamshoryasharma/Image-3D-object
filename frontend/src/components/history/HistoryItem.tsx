import { Box, Ellipsis, Eye, ImageOff, PencilLine, Sparkles, Square, Trash, WandSparkles } from 'lucide-react'
import { useEffect, useState } from 'react'

import { regenerateFromPrompt, reusePrompt } from '../../actions/generation'
import { jobPreviewUrl } from '../../api/jobs'
import type { Job } from '../../api/types'
import { useTunerVisible } from '../../hooks/useTunerVisible'
import { formatCompact, formatDuration, formatRelativeTime } from '../../lib/format'
import { isTerminal, useJobStore } from '../../store/jobStore'
import { useMeshTunerStore } from '../../store/meshTunerStore'
import { useRunStore } from '../../store/runStore'
import { Button, IconButton } from '../common/Button'
import { Menu } from '../common/Menu'
import { ProgressBar } from '../common/ProgressBar'
import { StatusBadge } from '../common/StatusBadge'
import { Tag } from '../common/Tag'
import styles from './HistoryItem.module.css'

export interface HistoryItemProps {
  job: Job
  now: number
  onOpened?: () => void
}

function JobThumbnail({ job }: { job: Job }) {
  const url = jobPreviewUrl(job)
  const [failedUrl, setFailedUrl] = useState<string | null>(null)
  // A running job's preview appears after preprocessing; retry when its stage advances.
  const retryToken = job.status === 'running' ? job.progress.stage : job.status
  const src = `${url}${url.includes('?') ? '&' : '?'}v=${encodeURIComponent(retryToken)}`
  if (failedUrl === src) {
    return (
      <div className={styles.thumbPlaceholder}>
        {job.status === 'completed' ? <Box size={18} aria-hidden /> : <ImageOff size={16} aria-hidden />}
      </div>
    )
  }
  return (
    <img
      className={styles.thumb}
      src={src}
      alt=""
      loading="lazy"
      decoding="async"
      onError={() => setFailedUrl(src)}
    />
  )
}

/** One history row: preview, status, metadata and actions (open / cancel / delete with confirmation). */
export function HistoryItem({ job, now, onOpened }: HistoryItemProps) {
  const viewedJobId = useJobStore((s) => s.viewedJobId)
  const view = useJobStore((s) => s.view)
  const cancel = useJobStore((s) => s.cancel)
  const remove = useJobStore((s) => s.remove)
  const cancelling = useJobStore((s) => s.cancellingIds.includes(job.id))
  const deleting = useJobStore((s) => s.deletingIds.includes(job.id))
  const [confirmDelete, setConfirmDelete] = useState(false)
  // Tuner parts only where the shell has tuner mount points (Default, UI_DESIGN §3.9).
  const tunerVisible = useTunerVisible()
  const tunedResult = useMeshTunerStore((s) => job.id in s.latestTunedByJob)
  const tuned = tunerVisible && tunedResult
  // Badges from the session's workflow index (GET /workflows + events): a prompt source, part-aware output.
  const fromPrompt = useRunStore((s) => {
    const id = s.workflowByJobId[job.id]
    return id ? s.workflows[id]?.request.source?.kind === 'prompt' : false
  })
  const withParts = useRunStore((s) => {
    const id = s.workflowByJobId[job.id]
    const workflow = id ? s.workflows[id] : undefined
    return !!workflow && (workflow.request.parts != null || workflow.outputs.part_analysis_id != null)
  })
  const completed = job.status === 'completed'

  // Tuning runs are fetched lazily for listed completed jobs (deduplicated by the store) to show the marker.
  useEffect(() => {
    if (completed && tunerVisible) void useMeshTunerStore.getState().loadRunsForJob(job.id)
  }, [completed, job.id, tunerVisible])

  useEffect(() => {
    if (!confirmDelete) return
    const timer = window.setTimeout(() => setConfirmDelete(false), 4000)
    return () => window.clearTimeout(timer)
  }, [confirmDelete])

  const viewed = viewedJobId === job.id
  const terminal = isTerminal(job)
  const duration = job.result?.timings.total ?? null
  const open = () => {
    view(job.id)
    onOpened?.()
  }

  return (
    <li className={`${styles.item} ${viewed ? styles.viewed : ''}`}>
      <div className={styles.thumbWrap}>
        <JobThumbnail job={job} />
      </div>
      <div className={styles.info}>
        <div className={styles.titleRow}>
          <StatusBadge status={job.status} />
          <time className={styles.time} dateTime={job.created_at} title={new Date(job.created_at).toLocaleString()}>
            {formatRelativeTime(job.created_at, now)}
          </time>
        </div>
        <p className={styles.meta}>
          <span>{job.settings.resolution}</span>
          <span>seed {job.seed}</span>
          <span>{formatCompact(job.result?.stats.faces ?? job.settings.polygon_target)} tris</span>
          {duration !== null && <span>{formatDuration(duration)}</span>}
          {tuned && (
            <span className={styles.tuned} title="An Auto Tune result exists for this model">
              <WandSparkles size={11} aria-hidden />
              tuned
            </span>
          )}
          {fromPrompt && (
            <Tag tone="accent" title="Generated from a text prompt">
              Prompt
            </Tag>
          )}
          {withParts && (
            <Tag tone="info" title="Generated with part-aware output">
              Parts
            </Tag>
          )}
        </p>
        {!terminal && (
          <div className={styles.progress}>
            <ProgressBar value={job.status === 'queued' ? null : job.progress.percent} size="xs" label="Progress" />
            <span className={styles.progressText}>
              {job.status === 'queued'
                ? job.queue_position
                  ? `Queued #${job.queue_position}`
                  : 'Queued'
                : `${job.progress.stage_label} · ${Math.round(job.progress.percent)}%`}
            </span>
          </div>
        )}
        {job.status === 'failed' && job.error && (
          <p className={styles.error} title={job.error.message}>
            {job.error.message}
          </p>
        )}
        <div className={styles.actions}>
          {job.status === 'completed' && (
            <Button size="sm" variant={viewed ? 'subtle' : 'secondary'} icon={<Eye size={14} />} onClick={open} disabled={viewed}>
              {viewed ? 'In viewer' : 'Open'}
            </Button>
          )}
          {!terminal && (
            <Button
              size="sm"
              variant="danger"
              icon={<Square size={12} />}
              loading={cancelling}
              onClick={() => void cancel(job.id)}
            >
              Cancel
            </Button>
          )}
          {terminal &&
            (confirmDelete ? (
              <Button size="sm" variant="danger" icon={<Trash size={14} />} loading={deleting} onClick={() => void remove(job.id)}>
                Confirm delete
              </Button>
            ) : (
              <IconButton
                label={`Delete job ${job.id}`}
                icon={<Trash size={14} />}
                size="sm"
                variant="danger"
                onClick={() => setConfirmDelete(true)}
              />
            ))}
          {fromPrompt && terminal && (
            <Menu
              label="Prompt actions"
              icon={<Ellipsis size={15} />}
              triggerVariant="ghost"
              sections={[
                {
                  items: [
                    {
                      id: 'regenerate',
                      label: 'Regenerate from prompt',
                      icon: <Sparkles size={14} />,
                      onSelect: () => void regenerateFromPrompt(job.id),
                    },
                    {
                      id: 'reuse',
                      label: 'Reuse prompt',
                      icon: <PencilLine size={14} />,
                      onSelect: () => void reusePrompt(job.id),
                    },
                  ],
                },
              ]}
            />
          )}
        </div>
      </div>
    </li>
  )
}
