import type { ReactNode } from 'react'

import styles from './Field.module.css'

export interface FieldProps {
  label: ReactNode
  htmlFor?: string
  /** Element id for `aria-labelledby` when the control is not a native input. */
  labelId?: string
  hint?: ReactNode
  aside?: ReactNode
  children: ReactNode
}

/** Labelled settings row: label (+ aside value) on top, control, optional hint. */
export function Field({ label, htmlFor, labelId, hint, aside, children }: FieldProps) {
  return (
    <div className={styles.field}>
      <div className={styles.labelRow}>
        {htmlFor ? (
          <label className={styles.label} htmlFor={htmlFor} id={labelId}>
            {label}
          </label>
        ) : (
          <span className={styles.label} id={labelId}>
            {label}
          </span>
        )}
        {aside && <span className={styles.aside}>{aside}</span>}
      </div>
      {children}
      {hint && <p className={styles.hint}>{hint}</p>}
    </div>
  )
}
