import type { CSSProperties } from 'react'

import styles from './RangeSlider.module.css'

export interface RangeSliderProps {
  value: number
  min: number
  max: number
  step?: number
  onChange: (value: number) => void
  ariaLabel: string
  ariaValueText?: string
  disabled?: boolean
  id?: string
}

/** CSS custom property consumed by RangeSlider.module.css to paint the filled part of the track. */
type FillStyle = CSSProperties & { '--fill': string }

/** Styled native range input (keyboard accessible by default). */
export function RangeSlider({ value, min, max, step = 1, onChange, ariaLabel, ariaValueText, disabled, id }: RangeSliderProps) {
  const percent = max > min ? ((value - min) / (max - min)) * 100 : 0
  const style: FillStyle = { '--fill': `${Math.max(0, Math.min(100, percent))}%` }
  return (
    <input
      id={id}
      className={styles.range}
      type="range"
      min={min}
      max={max}
      step={step}
      value={value}
      disabled={disabled}
      aria-label={ariaLabel}
      aria-valuetext={ariaValueText}
      style={style}
      onChange={(event) => onChange(Number(event.target.value))}
    />
  )
}
