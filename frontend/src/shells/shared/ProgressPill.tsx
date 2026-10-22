import { CircleCheck, CircleSlash, CircleX } from 'lucide-react'
import { useEffect, useState } from 'react'

import { ProgressRing } from '../../components/common/ProgressRing'
import { useActiveRun, type ActiveRunView } from '../../hooks/useActiveRun'
import { RUN_KIND_LABELS } from '../../lib/featureMeta.ts'
import { useJobStore } from '../../store/jobStore'
import { useRunStore } from '../../store/runStore'
import { openShellSheet, sheetElementId } from './sheets'
import { ENDED_PILL_MS, endedText } from './shellText'
import styles from './Shell.module.css'

type RunRef = Pick<ActiveRunView, 'kind' | 'id'>

const refKey = (run: RunRef | null) => (run ? `${run.kind}:${run.id}` : null)

/** Final status of a run that just left `useActiveRun()` (primitive selectors over the stored records). */
function useEndedStatus(ended: RunRef | null) {
  const jobStatus = useJobStore((s) => (ended?.kind === 'job' ? (s.jobs[ended.id]?.status ?? null) : null))
  const runStatus = useRunStore((s) => {
    if (ended?.kind === 'workflow') return s.workflows[ended.id]?.status ?? null
    if (ended?.kind === 'image_generation') return s.imageGenerations[ended.id]?.status ?? null
    return null
  })
  return jobStatus ?? runStatus
}

/**
 * Floating top-centre pill over the viewer (UI_DESIGN §4.1): shown while a run is queued/running and for 8 s after
 * it ended; a tap opens the `progress` sheet. It positions itself (absolute, top centre, `--progress-pill-top`) in
 * the nearest positioned ancestor, so render it inside the viewer's container. The last seen run is kept in state and
 * compared during render (no `setState` in an effect); the 8 s linger is a timer whose callback clears it.
 */
export function ProgressPill() {
  const run = useActiveRun()
  const [seen, setSeen] = useState<RunRef | null>(run ? { kind: run.kind, id: run.id } : null)
  const [ended, setEnded] = useState<RunRef | null>(null)

  if (refKey(run) !== refKey(seen)) {
    setSeen(run ? { kind: run.kind, id: run.id } : null)
    setEnded(run === null ? seen : null)
  }

  useEffect(() => {
    if (ended === null) return
    const timer = window.setTimeout(() => setEnded(null), ENDED_PILL_MS)
    return () => window.clearTimeout(timer)
  }, [ended])

  const endedStatus = useEndedStatus(run ? null : ended)
  const open = () => openShellSheet('progress')

  if (run) {
    const percent = run.status === 'queued' ? null : run.percent
    const step = run.status === 'queued' ? 'Queued' : (run.stepLabel ?? 'Running')
    const text = percent === null ? step : `${step} · ${Math.round(percent)} %`
    return (
      <div className={styles.pillSlot}>
        <button
          type="button"
          className={styles.progressPill}
          data-state={run.status}
          aria-label={`${RUN_KIND_LABELS[run.kind]}: ${text}. Show progress`}
          aria-haspopup="dialog"
          aria-controls={sheetElementId('progress')}
          onClick={open}
        >
          <span aria-hidden className={styles.pillRing}>
            <ProgressRing value={percent} size={20} thickness={3} />
          </span>
          <span className={styles.pillText}>{text}</span>
        </button>
      </div>
    )
  }

  if (!ended) return null
  const text = endedText(RUN_KIND_LABELS[ended.kind], endedStatus)
  return (
    <div className={styles.pillSlot}>
      <button
        type="button"
        className={styles.progressPill}
        data-state={endedStatus ?? 'completed'}
        aria-label={`${text}. Show progress`}
        aria-haspopup="dialog"
        aria-controls={sheetElementId('progress')}
        onClick={open}
      >
        <span aria-hidden className={styles.pillIcon}>
          {endedStatus === 'failed' ? (
            <CircleX size={18} />
          ) : endedStatus === 'cancelled' ? (
            <CircleSlash size={18} />
          ) : (
            <CircleCheck size={18} />
          )}
      </span>
      <span className={styles.pillText}>{text}</span>
    </button>
    </div>
  )
}
