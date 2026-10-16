import { GalleryVerticalEnd, RefreshCw } from 'lucide-react'

import { useNow } from '../../hooks/useNow'
import { useJobStore } from '../../store/jobStore'
import { useSystemStore } from '../../store/systemStore'
import { Button } from '../common/Button'
import { EmptyState } from '../common/EmptyState'
import { Spinner } from '../common/Spinner'
import { HistoryItem } from './HistoryItem'
import styles from './HistoryList.module.css'

/** Recent jobs (GET /jobs) with loading / error / empty states. */
export function HistoryList({ onOpened }: { onOpened?: () => void }) {
  const order = useJobStore((s) => s.order)
  const jobs = useJobStore((s) => s.jobs)
  const status = useJobStore((s) => s.historyStatus)
  const error = useJobStore((s) => s.historyError)
  const loadHistory = useJobStore((s) => s.loadHistory)
  const reachable = useSystemStore((s) => s.reachable)
  const now = useNow(30_000)

  if ((status === 'idle' || status === 'loading') && order.length === 0) {
    return (
      <div className={styles.center}>
        <Spinner size={18} />
        <span>Loading history…</span>
      </div>
    )
  }

  if (status === 'error' && order.length === 0) {
    return (
      <EmptyState
        compact
        tone="danger"
        icon={<GalleryVerticalEnd size={18} />}
        title="Could not load history"
        description={reachable === false ? 'The backend is offline.' : error?.message}
        action={
          <Button size="sm" icon={<RefreshCw size={14} />} onClick={() => void loadHistory()}>
            Try again
          </Button>
        }
      />
    )
  }

  if (order.length === 0) {
    return (
      <EmptyState
        compact
        icon={<GalleryVerticalEnd size={18} />}
        title="No jobs yet"
        description="Generated models are listed here so you can reopen, download or delete them."
      />
    )
  }

  return (
    <ul className={styles.list}>
      {order.map((id) => (jobs[id] ? <HistoryItem key={id} job={jobs[id]} now={now} onOpened={onOpened} /> : null))}
    </ul>
  )
}
