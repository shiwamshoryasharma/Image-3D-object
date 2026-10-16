import type { ReactNode } from 'react'

import styles from './ProgressRing.module.css'

export interface ProgressRingProps {
  /** 0..100; `null` renders an indeterminate ring (a spinning arc, static under reduced motion). */
  value: number | null
  /** Outer diameter in px. */
  size?: number
  /** Stroke width in px. */
  thickness?: number
  label?: string
  className?: string
  /** Centred content (e.g. the FAB's stop icon). */
  children?: ReactNode
}

/** Circular progress indicator (`role="progressbar"`). */
export function ProgressRing({ value, size = 40, thickness = 3, label, className, children }: ProgressRingProps) {
  const clamped = value === null ? null : Math.max(0, Math.min(100, value))
  const center = size / 2
  const radius = Math.max(0, (size - thickness) / 2)
  const circumference = 2 * Math.PI * radius
  const arc = clamped === null ? circumference * 0.25 : (clamped / 100) * circumference
  return (
    <span
      className={`${styles.ring} ${clamped === null ? styles.indeterminate : ''} ${className ?? ''}`}
      style={{ width: size, height: size }}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped === null ? undefined : Math.round(clamped)}
    >
      <svg className={styles.svg} width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
        <circle className={styles.track} cx={center} cy={center} r={radius} strokeWidth={thickness} />
        <circle
          className={styles.indicator}
          cx={center}
          cy={center}
          r={radius}
          strokeWidth={thickness}
          strokeDasharray={`${arc} ${circumference}`}
          transform={`rotate(-90 ${center} ${center})`}
        />
      </svg>
      {children !== undefined && children !== null && <span className={styles.content}>{children}</span>}
    </span>
  )
}
