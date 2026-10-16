import type { CSSProperties } from 'react'

import styles from './Skeleton.module.css'

export interface SkeletonProps {
  shape: 'line' | 'block' | 'circle'
  /** CSS length (number = px). Lines default to the full width, circles to 32px. */
  width?: number | string
  height?: number | string
  /** Number of text lines for `shape="line"`; the last of several is shorter. */
  lines?: number
  className?: string
}

/**
 * Loading placeholder with a shimmer (static under reduced motion). Hidden from assistive technology: the
 * container that is loading carries `aria-busy`.
 */
export function Skeleton({ shape, width, height, lines = 1, className }: SkeletonProps) {
  const size: CSSProperties = { width, height }
  if (shape === 'line' && lines > 1) {
    return (
      <div className={`${styles.lines} ${className ?? ''}`} style={{ width }} aria-hidden>
        {Array.from({ length: lines }, (_, index) => (
          <div
            key={index}
            className={`${styles.skeleton} ${styles.line} ${index === lines - 1 ? styles.lastLine : ''}`}
            style={{ height }}
          />
        ))}
      </div>
    )
  }
  return <div className={`${styles.skeleton} ${styles[shape]} ${className ?? ''}`} style={size} aria-hidden />
}
