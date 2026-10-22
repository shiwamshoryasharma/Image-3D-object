import { ConnectionIndicator } from '../../components/layout/ConnectionIndicator'
import { EngineStatus } from '../../components/layout/EngineStatus'
import { GpuMeter } from '../../components/layout/GpuMeter'
import styles from './Shell.module.css'

/** Body of the `status` sheet (UI_DESIGN §4.1): the full engine status (with its details), realtime state and GPU. */
export function StatusSheetContent() {
  return (
    <div className={styles.statusSheet}>
      <div className={styles.statusRow}>
        <span className={styles.statusLabel}>Engine</span>
        <EngineStatus />
      </div>
      <div className={styles.statusRow}>
        <span className={styles.statusLabel}>Live updates</span>
        <ConnectionIndicator showLabel />
      </div>
      <GpuMeter />
    </div>
  )
}
