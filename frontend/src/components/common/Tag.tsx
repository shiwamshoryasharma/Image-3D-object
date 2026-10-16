import type { ReactNode } from 'react'

import styles from './Tag.module.css'

export type TagTone = 'neutral' | 'accent' | 'success' | 'warning' | 'danger' | 'info'

export interface TagProps {
  tone?: TagTone
  icon?: ReactNode
  children: ReactNode
  title?: string
  className?: string
}

/** Small inline label (LTS, System/Embedded, compatibility, check status). */
export function Tag({ tone = 'neutral', icon, children, title, className }: TagProps) {
  return (
    <span className={`${styles.tag} ${styles[tone]} ${className ?? ''}`} title={title}>
      {icon}
      {children}
    </span>
  )
}
