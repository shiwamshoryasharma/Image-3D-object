import { useRef, type KeyboardEvent, type ReactNode } from 'react'

import styles from './SegmentedControl.module.css'

export interface SegmentOption<T extends string | number> {
  value: T
  label: ReactNode
  /** Accessible/tooltip description. */
  title?: string
  disabled?: boolean
}

export interface SegmentedControlProps<T extends string | number> {
  value: T
  options: readonly SegmentOption<T>[]
  onChange: (value: T) => void
  /** Accessible name of the group. */
  label: string
  size?: 'sm' | 'md'
  disabled?: boolean
}

/** Radio-group style segmented control with roving focus (arrow keys). */
export function SegmentedControl<T extends string | number>({
  value,
  options,
  onChange,
  label,
  size = 'md',
  disabled = false,
}: SegmentedControlProps<T>) {
  const refs = useRef<Array<HTMLButtonElement | null>>([])
  const enabled = options.filter((option) => !option.disabled)

  const handleKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const keys = ['ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown', 'Home', 'End']
    if (!keys.includes(event.key) || enabled.length === 0) return
    event.preventDefault()
    const currentIndex = Math.max(0, enabled.findIndex((option) => option.value === value))
    let nextIndex = currentIndex
    if (event.key === 'Home') nextIndex = 0
    else if (event.key === 'End') nextIndex = enabled.length - 1
    else if (event.key === 'ArrowLeft' || event.key === 'ArrowUp')
      nextIndex = (currentIndex - 1 + enabled.length) % enabled.length
    else nextIndex = (currentIndex + 1) % enabled.length
    const next = enabled[nextIndex]
    onChange(next.value)
    refs.current[options.indexOf(next)]?.focus()
  }

  return (
    <div
      role="radiogroup"
      aria-label={label}
      aria-disabled={disabled || undefined}
      className={`${styles.group} ${styles[size]}`}
      onKeyDown={disabled ? undefined : handleKeyDown}
    >
      {options.map((option, index) => {
        const selected = option.value === value
        return (
          <button
            key={String(option.value)}
            ref={(node) => {
              refs.current[index] = node
            }}
            type="button"
            role="radio"
            aria-checked={selected}
            tabIndex={selected ? 0 : -1}
            title={option.title}
            disabled={disabled || option.disabled}
            className={`${styles.segment} ${selected ? styles.selected : ''}`}
            onClick={() => onChange(option.value)}
          >
            {option.label}
          </button>
        )
      })}
    </div>
  )
}
