import styles from './ProgressBar.module.css'

export interface ProgressBarProps {
  /** 0..100; `null` renders an indeterminate bar. */
  value: number | null
  tone?: 'accent' | 'success' | 'warning' | 'danger' | 'muted'
  size?: 'xs' | 'sm' | 'md'
  label?: string
  className?: string
}

export function ProgressBar({ value, tone = 'accent', size = 'sm', label, className }: ProgressBarProps) {
  const clamped = value === null ? null : Math.max(0, Math.min(100, value))
  return (
    <div
      className={`${styles.track} ${styles[size]} ${className ?? ''}`}
      role="progressbar"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={clamped === null ? undefined : Math.round(clamped)}
    >
      {clamped === null ? (
        <div className={`${styles.indeterminate} ${styles[tone]}`} />
      ) : (
        <div className={`${styles.fill} ${styles[tone]}`} style={{ width: `${clamped}%` }} />
      )}
    </div>
  )
}
