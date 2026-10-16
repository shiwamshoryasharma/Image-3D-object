import { ChevronDown, RotateCcw } from 'lucide-react'
import { useId } from 'react'

import type { TuningPreset, TuningSymmetry } from '../../api/types'
import {
  PRESET_HINT,
  PRESET_OPTIONS,
  SMOOTHING_BOUNDS,
  SYMMETRY_OPTIONS,
  effectiveOptions,
  overrideCount,
} from '../../lib/meshTuner'
import { useMeshTunerStore } from '../../store/meshTunerStore'
import { Button } from '../common/Button'
import { RangeSlider } from '../common/RangeSlider'
import { SegmentedControl } from '../common/SegmentedControl'
import { Switch } from '../common/Switch'
import { Field } from '../settings/Field'
import styles from './TuningOptionsForm.module.css'

/** Auto Tune preset + options (persisted). Advanced switches are collapsed; overrides win over the preset. */
export function TuningOptionsForm({ disabled = false }: { disabled?: boolean }) {
  const preset = useMeshTunerStore((s) => s.preset)
  const overrides = useMeshTunerStore((s) => s.overrides)
  const open = useMeshTunerStore((s) => s.advancedOpen)
  const setPreset = useMeshTunerStore((s) => s.setPreset)
  const setOption = useMeshTunerStore((s) => s.setOption)
  const resetOverrides = useMeshTunerStore((s) => s.resetOverrides)
  const setOpen = useMeshTunerStore((s) => s.setAdvancedOpen)
  const presetLabelId = useId()
  const symmetryLabelId = useId()
  const smoothingId = useId()
  const contentId = useId()

  const values = effectiveOptions(preset, overrides)
  const custom = overrideCount(preset, overrides)

  return (
    <div className={styles.form}>
      <Field label="Preset" labelId={presetLabelId} hint={PRESET_HINT[preset]}>
        <SegmentedControl<TuningPreset>
          label="Preset"
          size="sm"
          value={preset}
          options={PRESET_OPTIONS}
          onChange={setPreset}
          disabled={disabled}
        />
      </Field>

      <div className={styles.advanced}>
        <button
          type="button"
          className={styles.toggle}
          aria-expanded={open}
          aria-controls={contentId}
          onClick={() => setOpen(!open)}
        >
          <ChevronDown size={16} className={`${styles.chevron} ${open ? styles.chevronOpen : ''}`} aria-hidden />
          <span>Advanced</span>
          {custom > 0 && <span className={styles.count}>{custom} custom</span>}
        </button>

        {open && (
          <div id={contentId} className={styles.content}>
            <Switch
              size="sm"
              label="Remove inner shell"
              description="Deletes the hidden double wall behind the surface"
              checked={values.remove_inner_shell}
              disabled={disabled}
              onChange={(value) => setOption('remove_inner_shell', value)}
            />
            <Switch
              size="sm"
              label="Fill holes"
              description={`Closes holes up to ${values.max_hole_edges} edges`}
              checked={values.fill_holes}
              disabled={disabled}
              onChange={(value) => setOption('fill_holes', value)}
            />
            <Switch
              size="sm"
              label="Face & eye refinement"
              description="Cleans eye recesses and the facial midline crease"
              checked={values.refine_face}
              disabled={disabled}
              onChange={(value) => {
                setOption('refine_face', value)
                setOption('eye_correction', value)
              }}
            />
            <Field label="Symmetry" labelId={symmetryLabelId}>
              <SegmentedControl<TuningSymmetry>
                label="Symmetry"
                size="sm"
                value={values.symmetry === 'symmetrize' ? 'repair' : values.symmetry}
                options={SYMMETRY_OPTIONS}
                onChange={(value) => setOption('symmetry', value)}
                disabled={disabled}
              />
            </Field>
            <Switch
              size="sm"
              label="Refine density"
              description="Splits oversized, faceted triangles for a finer mesh"
              checked={values.refine_density}
              disabled={disabled}
              onChange={(value) => setOption('refine_density', value)}
            />
            <Field
              label="Smoothing"
              htmlFor={smoothingId}
              aside={<span className="mono">{values.smoothing === 0 ? 'Off' : `${Math.round(values.smoothing * 100)}%`}</span>}
            >
              <RangeSlider
                id={smoothingId}
                value={values.smoothing}
                min={SMOOTHING_BOUNDS.min}
                max={SMOOTHING_BOUNDS.max}
                step={SMOOTHING_BOUNDS.step}
                disabled={disabled}
                ariaLabel="Smoothing strength"
                ariaValueText={values.smoothing === 0 ? 'Off' : `${Math.round(values.smoothing * 100)} percent`}
                onChange={(value) => setOption('smoothing', value)}
              />
            </Field>
            <Switch
              size="sm"
              label="Quads"
              description="Convert triangles to quads (the GLB is re-triangulated on export)"
              checked={values.quads}
              disabled={disabled}
              onChange={(value) => setOption('quads', value)}
            />
            <Switch
              size="sm"
              label="Previews"
              description="Render before/after comparison images"
              checked={values.render_previews}
              disabled={disabled}
              onChange={(value) => setOption('render_previews', value)}
            />
            <Button
              size="sm"
              variant="ghost"
              icon={<RotateCcw size={14} />}
              disabled={custom === 0 || disabled}
              onClick={resetOverrides}
            >
              Reset to preset
            </Button>
          </div>
        )}
      </div>
    </div>
  )
}
