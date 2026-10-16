import { useId, useRef, type KeyboardEvent, type ReactNode } from 'react'

import { Spinner } from './Spinner'
import styles from './RadioCardGroup.module.css'

export interface RadioCardOption<T extends string> {
  value: T
  label: ReactNode
  description?: ReactNode
  /** Decorative preview above the label (e.g. a CSS schematic of a layout). */
  media?: ReactNode
  /** Chips or status text after the description ("Current", "Suggested for this screen"). */
  meta?: ReactNode
  disabled?: boolean
  /** Shows a spinner and sets `aria-busy` (e.g. while a lazy chunk loads). */
  busy?: boolean
}

export interface RadioCardGroupProps<T extends string> {
  value: T | null
  options: readonly RadioCardOption<T>[]
  onChange: (value: T) => void
  /** Accessible name of the group (or `labelledBy`). */
  label?: string
  labelledBy?: string
  /** Called when a card is focused or hovered, e.g. to start loading what selecting it needs. */
  onPrefetch?: (value: T) => void
  className?: string
}

const KEY_STEPS: Record<string, (index: number, count: number) => number> = {
  ArrowRight: (index, count) => (index + 1) % count,
  ArrowDown: (index, count) => (index + 1) % count,
  ArrowLeft: (index, count) => (index - 1 + count) % count,
  ArrowUp: (index, count) => (index - 1 + count) % count,
  Home: () => 0,
  End: (_index, count) => count - 1,
}

/** Radio group of cards (`role="radiogroup"`): roving tabIndex, arrow keys move and select, Space/Enter select. */
export function RadioCardGroup<T extends string>({
  value,
  options,
  onChange,
  label,
  labelledBy,
  onPrefetch,
  className,
}: RadioCardGroupProps<T>) {
  const groupId = useId()
  const refs = useRef<Array<HTMLButtonElement | null>>([])
  const enabled = options.filter((option) => !option.disabled)
  const checkedIndex = options.findIndex((option) => option.value === value && !option.disabled)
  // The checked card is the tab stop; without one, the first enabled card is.
  const tabStop = checkedIndex >= 0 ? checkedIndex : options.findIndex((option) => !option.disabled)

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const step = KEY_STEPS[event.key]
    if (!step || enabled.length === 0) return
    event.preventDefault()
    const current = Math.max(0, enabled.findIndex((option) => option.value === value))
    const next = enabled[step(current, enabled.length)]
    onChange(next.value)
    refs.current[options.indexOf(next)]?.focus()
  }

  return (
    <div
      role="radiogroup"
      aria-label={labelledBy ? undefined : label}
      aria-labelledby={labelledBy}
      className={`${styles.group} ${className ?? ''}`}
      onKeyDown={handleKeyDown}
    >
      {options.map((option, index) => {
        const checked = index === checkedIndex
        const labelId = `${groupId}-${index}-label`
        const describedBy = [
          option.description ? `${groupId}-${index}-description` : '',
          option.meta ? `${groupId}-${index}-meta` : '',
        ]
          .filter(Boolean)
          .join(' ')
        return (
          <button
            key={option.value}
            ref={(node) => {
              refs.current[index] = node
            }}
            type="button"
            role="radio"
            aria-checked={checked}
            aria-labelledby={labelId}
            aria-describedby={describedBy || undefined}
            aria-busy={option.busy || undefined}
            tabIndex={index === tabStop ? 0 : -1}
            disabled={option.disabled}
            className={`${styles.card} ${checked ? styles.checked : ''}`}
            onClick={() => onChange(option.value)}
            onFocus={onPrefetch ? () => onPrefetch(option.value) : undefined}
            onPointerEnter={onPrefetch ? () => onPrefetch(option.value) : undefined}
          >
            {option.media && (
              <span className={styles.media} aria-hidden>
                {option.media}
              </span>
            )}
            <span className={styles.heading}>
              <span id={labelId} className={styles.label}>
                {option.label}
              </span>
              {option.busy && <Spinner size={14} />}
            </span>
            {option.description && (
              <span id={`${groupId}-${index}-description`} className={styles.description}>
                {option.description}
              </span>
            )}
            {option.meta && (
              <span id={`${groupId}-${index}-meta`} className={styles.meta}>
                {option.meta}
              </span>
            )}
          </button>
        )
      })}
    </div>
  )
}
