import { useId } from 'react'

import type { PolygonTargetRange } from '../../api/types'
import { clamp, formatCompact, formatInteger } from '../../lib/format'
import { NumberField } from '../common/NumberField'
import { RangeSlider } from '../common/RangeSlider'
import { Field } from './Field'
import styles from './PolygonTargetControl.module.css'

const SLIDER_STEPS = 1000
const PRESETS = [50_000, 200_000, 500_000, 1_000_000]

/** Rounds to a "nice" value for the current magnitude so the log slider produces readable numbers. */
function roundNice(value: number): number {
  const step = value < 10_000 ? 100 : value < 100_000 ? 1_000 : value < 1_000_000 ? 5_000 : 10_000
  return Math.round(value / step) * step
}

function toSlider(value: number, range: PolygonTargetRange): number {
  const min = Math.log(range.min)
  const max = Math.log(range.max)
  return Math.round(((Math.log(clamp(value, range.min, range.max)) - min) / (max - min)) * SLIDER_STEPS)
}

function fromSlider(position: number, range: PolygonTargetRange): number {
  const min = Math.log(range.min)
  const max = Math.log(range.max)
  const value = Math.exp(min + (position / SLIDER_STEPS) * (max - min))
  return clamp(roundNice(value), range.min, range.max)
}

export interface PolygonTargetControlProps {
  value: number
  range: PolygonTargetRange
  onChange: (value: number) => void
  disabled?: boolean
}

/** Target face count: log-scaled slider, numeric input and presets, bounded by engine capabilities. */
export function PolygonTargetControl({ value, range, onChange, disabled }: PolygonTargetControlProps) {
  const inputId = useId()
  const presets = PRESETS.filter((preset) => preset >= range.min && preset <= range.max)
  return (
    <Field
      label="Polygon target"
      htmlFor={inputId}
      aside={<span className="mono">{formatCompact(value)} faces</span>}
      hint={`Target triangle count after decimation (${formatCompact(range.min)}–${formatCompact(range.max)}). Higher keeps more detail but produces larger files.`}
    >
      <div className={styles.row}>
        <RangeSlider
          value={toSlider(value, range)}
          min={0}
          max={SLIDER_STEPS}
          onChange={(position) => onChange(fromSlider(position, range))}
          ariaLabel="Polygon target (logarithmic)"
          ariaValueText={`${formatInteger(value)} faces`}
          disabled={disabled}
        />
        <NumberField
          id={inputId}
          className={styles.input}
          value={value}
          min={range.min}
          max={range.max}
          integer
          onChange={(next) => next !== null && onChange(next)}
          disabled={disabled}
        />
      </div>
      <div className={styles.presets} role="group" aria-label="Polygon target presets">
        {presets.map((preset) => (
          <button
            key={preset}
            type="button"
            className={`${styles.preset} ${preset === value ? styles.presetActive : ''}`}
            aria-pressed={preset === value}
            disabled={disabled}
            onClick={() => onChange(preset)}
          >
            {formatCompact(preset)}
          </button>
        ))}
        {range.default && !presets.includes(range.default) && (
          <button
            type="button"
            className={`${styles.preset} ${range.default === value ? styles.presetActive : ''}`}
            aria-pressed={range.default === value}
            disabled={disabled}
            onClick={() => onChange(range.default)}
          >
            Default
          </button>
        )}
      </div>
    </Field>
  )
}
