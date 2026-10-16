import { TriangleAlert } from 'lucide-react'

import styles from './InlineAlert.module.css'

/** Error shown right next to the control that failed (announced via `role="alert"`). */
export function InlineAlert({ message }: { message: string | null }) {
  if (!message) return null
  return (
    <p className={styles.alert} role="alert">
      <TriangleAlert size={14} aria-hidden />
      <span>{message}</span>
    </p>
  )
}
