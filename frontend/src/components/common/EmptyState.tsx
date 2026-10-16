import type { ReactNode } from 'react'

import styles from './EmptyState.module.css'

export interface EmptyStateProps {
  icon?: ReactNode
  title: ReactNode
  description?: ReactNode
  action?: ReactNode
  compact?: boolean
  tone?: 'neutral' | 'danger'
}

export function EmptyState({ icon, title, description, action, compact = false, tone = 'neutral' }: EmptyStateProps) {
  return (
    <div className={`${styles.empty} ${compact ? styles.compact : ''} ${tone === 'danger' ? styles.danger : ''}`}>
      {icon && <div className={styles.icon}>{icon}</div>}
      <p className={styles.title}>{title}</p>
      {description && <div className={styles.description}>{description}</div>}
      {action && <div className={styles.action}>{action}</div>}
    </div>
  )
}
