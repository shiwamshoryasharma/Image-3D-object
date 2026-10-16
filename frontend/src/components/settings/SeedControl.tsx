import { Dice5, Undo2 } from 'lucide-react'
import { useId } from 'react'

import { MAX_SEED, randomSeed } from '../../store/settingsStore'
import { IconButton } from '../common/Button'
import { NumberField } from '../common/NumberField'
import { Switch } from '../common/Switch'
import { Field } from './Field'
import styles from './SeedControl.module.css'

export interface SeedControlProps {
  seed: number
  randomize: boolean
  onSeedChange: (seed: number) => void
  onRandomizeChange: (randomize: boolean) => void
  /** Seed used by the most recent job (for "reuse"). */
  lastSeed: number | null
  disabled?: boolean
}

export function SeedControl({ seed, randomize, onSeedChange, onRandomizeChange, lastSeed, disabled }: SeedControlProps) {
  const inputId = useId()
  return (
    <Field
      label="Seed"
      htmlFor={inputId}
      hint={randomize ? 'A new random seed is drawn for every job (shown in the result).' : 'Same seed + settings reproduce the same model.'}
    >
      <div className={styles.row}>
        <NumberField
          id={inputId}
          value={seed}
          min={0}
          max={MAX_SEED}
          integer
          disabled={disabled || randomize}
          onChange={(next) => next !== null && onSeedChange(next)}
          className={styles.input}
        />
        <IconButton
          label="Pick a random seed"
          icon={<Dice5 size={16} />}
          variant="solid"
          disabled={disabled}
          onClick={() => {
            onSeedChange(randomSeed())
            onRandomizeChange(false)
          }}
        />
        {lastSeed !== null && (
          <IconButton
            label={`Reuse last seed (${lastSeed})`}
            icon={<Undo2 size={16} />}
            variant="solid"
            disabled={disabled || (!randomize && seed === lastSeed)}
            onClick={() => {
              onSeedChange(lastSeed)
              onRandomizeChange(false)
            }}
          />
        )}
      </div>
      <Switch size="sm" label="Randomize seed" checked={randomize} onChange={onRandomizeChange} disabled={disabled} />
    </Field>
  )
}
