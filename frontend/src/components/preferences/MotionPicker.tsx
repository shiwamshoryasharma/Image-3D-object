import { usePreferencesStore } from '../../store/preferencesStore'
import { SegmentedControl } from '../common/SegmentedControl'
import { MOTION_OPTIONS } from './preferencesText'
import styles from './preferences.module.css'

/** Motion: follow the system setting, or reduce motion in this app regardless of it. */
export function MotionPicker() {
  const motion = usePreferencesStore((s) => s.motion)
  const setMotion = usePreferencesStore((s) => s.setMotion)
  return (
    <div className={styles.field}>
      <h4 className={styles.fieldLabel}>Motion</h4>
      <SegmentedControl label="Motion" value={motion} options={MOTION_OPTIONS} onChange={setMotion} />
      <p className={styles.hint}>Reduce motion turns off animations and smooth scrolling.</p>
    </div>
  )
}
