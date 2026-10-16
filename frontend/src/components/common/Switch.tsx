import { useId, type ReactNode } from 'react'

import styles from './Switch.module.css'

export interface SwitchProps {
  checked: boolean
  onChange: (checked: boolean) => void
  label: ReactNode
  description?: ReactNode
  disabled?: boolean
  size?: 'sm' | 'md'
}

/** Accessible toggle switch (`role="switch"`). */
export function Switch({ checked, onChange, label, description, disabled = false, size = 'md' }: SwitchProps) {
  const labelId = useId()
  const descriptionId = useId()
  return (
    <div className={`${styles.row} ${disabled ? styles.disabled : ''}`}>
      <div className={styles.text}>
        <span id={labelId} className={styles.label}>
          {label}
        </span>
        {description && (
          <span id={descriptionId} className={styles.description}>
            {description}
          </span>
        )}
      </div>
      <button
        type="button"
        role="switch"
        aria-checked={checked}
        aria-labelledby={labelId}
        aria-describedby={description ? descriptionId : undefined}
        disabled={disabled}
        className={`${styles.track} ${styles[size]} ${checked ? styles.on : ''}`}
        onClick={() => onChange(!checked)}
      >
        <span className={styles.thumb} />
      </button>
    </div>
  )
}
