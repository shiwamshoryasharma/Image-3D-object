import { ChevronDown, RotateCcw } from 'lucide-react'
import { useId } from 'react'

import { SAMPLER_KEYS, type SamplerKey } from '../../api/types'
import {
  SAMPLER_BOUNDS,
  SAMPLER_DEFAULTS,
  SAMPLER_FIELDS,
  useSettingsStore,
  type SamplerField,
} from '../../store/settingsStore'
import { Button, IconButton } from '../common/Button'
import { NumberField } from '../common/NumberField'
import styles from './AdvancedSettings.module.css'

const SAMPLER_LABEL: Record<SamplerKey, { title: string; description: string }> = {
  sparse_structure: { title: 'Sparse structure', description: 'Coarse occupancy (overall shape layout)' },
  shape: { title: 'Shape', description: 'Geometry detail (SLat flow)' },
  texture: { title: 'Texture', description: 'PBR material generation' },
}

const FIELD_LABEL: Record<SamplerField, string> = {
  steps: 'Steps',
  guidance_strength: 'Guidance',
  guidance_rescale: 'Rescale',
  rescale_t: 'Rescale t',
}

/** Collapsible per-sampler overrides. Empty fields use the engine defaults (shown as placeholders). */
export function AdvancedSettings({ disabled }: { disabled?: boolean }) {
  const open = useSettingsStore((s) => s.advancedOpen)
  const setOpen = useSettingsStore((s) => s.setAdvancedOpen)
  const advanced = useSettingsStore((s) => s.form.advanced)
  const updateSampler = useSettingsStore((s) => s.updateSampler)
  const resetSampler = useSettingsStore((s) => s.resetSampler)
  const resetAdvanced = useSettingsStore((s) => s.resetAdvanced)
  const contentId = useId()

  const overrideCount = SAMPLER_KEYS.reduce(
    (count, key) => count + SAMPLER_FIELDS.filter((field) => advanced[key][field] !== null).length,
    0,
  )

  return (
    <div className={styles.advanced}>
      <button
        type="button"
        className={styles.toggle}
        aria-expanded={open}
        aria-controls={contentId}
        onClick={() => setOpen(!open)}
      >
        <ChevronDown size={16} className={`${styles.chevron} ${open ? styles.chevronOpen : ''}`} aria-hidden />
        <span>Advanced sampling</span>
        {overrideCount > 0 && <span className={styles.count}>{overrideCount} override{overrideCount > 1 ? 's' : ''}</span>}
      </button>

      {open && (
        <div id={contentId} className={styles.content}>
          <p className={styles.intro}>
            Leave a field empty to use the model default (shown in grey). Bounds: steps 1–50, guidance 0–10, rescale
            0–1, rescale t 1–6.
          </p>
          {SAMPLER_KEYS.map((key) => {
            const values = advanced[key]
            const hasOverrides = SAMPLER_FIELDS.some((field) => values[field] !== null)
            return (
              <fieldset key={key} className={styles.sampler} disabled={disabled}>
                <legend className={styles.legend}>
                  <span className={styles.samplerTitle}>{SAMPLER_LABEL[key].title}</span>
                  <span className={styles.samplerDescription}>{SAMPLER_LABEL[key].description}</span>
                </legend>
                {hasOverrides && (
                  <IconButton
                    className={styles.samplerReset}
                    label={`Reset ${SAMPLER_LABEL[key].title} overrides`}
                    icon={<RotateCcw size={13} />}
                    size="sm"
                    onClick={() => resetSampler(key)}
                  />
                )}
                <div className={styles.grid}>
                  {SAMPLER_FIELDS.map((field) => {
                    const bounds = SAMPLER_BOUNDS[field]
                    const inputId = `${contentId}-${key}-${field}`
                    return (
                      <div key={field} className={styles.cell}>
                        <label className={styles.cellLabel} htmlFor={inputId}>
                          {FIELD_LABEL[field]}
                        </label>
                        <NumberField
                          id={inputId}
                          value={values[field]}
                          nullable
                          integer={field === 'steps'}
                          min={bounds.min}
                          max={bounds.max}
                          step={bounds.step}
                          placeholder={String(SAMPLER_DEFAULTS[key][field])}
                          onChange={(value) => updateSampler(key, field, value)}
                        />
                      </div>
                    )
                  })}
                </div>
              </fieldset>
            )
          })}
          <Button
            size="sm"
            variant="ghost"
            icon={<RotateCcw size={14} />}
            disabled={overrideCount === 0 || disabled}
            onClick={resetAdvanced}
          >
            Reset all advanced settings
          </Button>
        </div>
      )}
    </div>
  )
}
