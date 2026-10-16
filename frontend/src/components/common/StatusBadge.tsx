import { Ban, CircleCheck, CircleX, Clock, LoaderCircle } from 'lucide-react'

import type { JobStatus } from '../../api/types'
import { STATUS_LABEL } from '../../lib/stages'
import styles from './StatusBadge.module.css'

const ICONS = {
  queued: Clock,
  running: LoaderCircle,
  completed: CircleCheck,
  failed: CircleX,
  cancelled: Ban,
} as const

export function StatusBadge({ status, label }: { status: JobStatus; label?: string }) {
  const Icon = ICONS[status]
  return (
    <span className={`${styles.badge} ${styles[status]}`}>
      <Icon size={12} className={status === 'running' ? styles.spin : undefined} aria-hidden />
      {label ?? STATUS_LABEL[status]}
    </span>
  )
}
