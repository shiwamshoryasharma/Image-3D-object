import { useState, type KeyboardEvent } from 'react'

import { clamp } from '../../lib/format'
import styles from './NumberField.module.css'

export interface NumberFieldProps {
  value: number | null
  onChange: (value: number | null) => void
  min: number
  max: number
  step?: number
  /** Allow clearing the field (→ `null`). */
  nullable?: boolean
  placeholder?: string
  disabled?: boolean
  id?: string
  ariaLabel?: string
  className?: string
  /** Integer-only values. */
  integer?: boolean
  suffix?: string
}

function format(value: number | null): string {
  return value === null ? '' : String(value)
}

/** Parses "1500", "1.5k", "2m" (case-insensitive). */
function parseNumber(text: string): number | null {
  const match = /^(-?\d*\.?\d+)([km]?)$/i.exec(text)
  if (!match) return null
  const multiplier = match[2].toLowerCase() === 'k' ? 1_000 : match[2].toLowerCase() === 'm' ? 1_000_000 : 1
  const value = Number(match[1]) * multiplier
  return Number.isFinite(value) ? value : null
}

/**
 * Numeric input that keeps a free-form draft while typing and commits a clamped value on blur / Enter.
 */
export function NumberField({
  value,
  onChange,
  min,
  max,
  step = 1,
  nullable = false,
  placeholder,
  disabled,
  id,
  ariaLabel,
  className,
  integer = false,
  suffix,
}: NumberFieldProps) {
  const [draft, setDraft] = useState<string | null>(null)

  const commit = () => {
    if (draft === null) return
    const text = draft.trim().replace(/[,_\s]/g, '')
    setDraft(null)
    if (text === '') {
      if (nullable) onChange(null)
      return
    }
    const parsed = parseNumber(text)
    if (parsed === null) return
    const rounded = integer ? Math.round(parsed) : Math.round(parsed / step) * step
    onChange(Number(clamp(rounded, min, max).toFixed(6)))
  }

  const onKeyDown = (event: KeyboardEvent<HTMLInputElement>) => {
    if (event.key === 'Enter') {
      commit()
      ;(event.target as HTMLInputElement).blur()
    } else if (event.key === 'Escape') {
      setDraft(null)
      ;(event.target as HTMLInputElement).blur()
    } else if (event.key === 'ArrowUp' || event.key === 'ArrowDown') {
      event.preventDefault()
      const base = value ?? (Number(placeholder) || min)
      const delta = (event.key === 'ArrowUp' ? 1 : -1) * step * (event.shiftKey ? 10 : 1)
      const next = Number(clamp(base + delta, min, max).toFixed(6))
      setDraft(null)
      onChange(integer ? Math.round(next) : next)
    }
  }

  return (
    <div className={`${styles.field} ${disabled ? styles.disabled : ''} ${className ?? ''}`}>
      <input
        id={id}
        className={styles.input}
        type="text"
        inputMode={integer ? 'numeric' : 'decimal'}
        autoComplete="off"
        spellCheck={false}
        aria-label={ariaLabel}
        placeholder={placeholder}
        disabled={disabled}
        value={draft ?? format(value)}
        onChange={(event) => setDraft(event.target.value)}
        onBlur={commit}
        onKeyDown={onKeyDown}
      />
      {suffix && <span className={styles.suffix}>{suffix}</span>}
    </div>
  )
}
