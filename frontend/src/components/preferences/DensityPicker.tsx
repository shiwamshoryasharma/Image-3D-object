import { useResolvedUiMode } from '../../hooks/useResolvedUiMode'
import { UI_MODE_META } from '../../lib/uiModes.ts'
import { usePreferencesStore } from '../../store/preferencesStore'
import { SegmentedControl } from '../common/SegmentedControl'
import { DENSITY_OPTIONS } from './preferencesText'
import styles from './preferences.module.css'

/** Density: Auto (touch-sized on touch screens) / Comfortable / Touch; Mobile and Tablet always use Touch. */
export function DensityPicker() {
  const density = usePreferencesStore((s) => s.density)
  const setDensity = usePreferencesStore((s) => s.setDensity)
  const mode = useResolvedUiMode()
  const forced = mode === 'mobile' || mode === 'tablet'
  return (
    <div className={styles.field}>
      <h4 className={styles.fieldLabel}>Density</h4>
      <SegmentedControl label="Density" value={forced ? 'touch' : density} options={DENSITY_OPTIONS} onChange={setDensity} disabled={forced} />
      <p className={styles.hint}>
        {forced
          ? `The ${UI_MODE_META[mode].name} always uses touch-sized controls.`
          : 'Auto uses touch-sized controls on touch screens.'}
      </p>
    </div>
  )
}
