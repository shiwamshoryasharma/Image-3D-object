import { MemoryStick } from 'lucide-react'

import { formatMegabytes } from '../../lib/format'
import { useSystemStore } from '../../store/systemStore'
import styles from './GpuMeter.module.css'

/** GPU name + VRAM usage bar from `/system.device`. */
export function GpuMeter() {
  const device = useSystemStore((s) => s.system?.device ?? null)
  if (!device) return null
  if (!device.cuda_available) {
    return (
      <div className={styles.meter} title="CUDA is not available on the backend">
        <MemoryStick size={14} aria-hidden className={styles.icon} />
        <span className={styles.name}>No CUDA device</span>
      </div>
    )
  }
  const total = device.vram_total_mb ?? 0
  const used = device.vram_used_mb ?? 0
  const percent = total > 0 ? (used / total) * 100 : 0
  const tone = percent > 90 ? styles.danger : percent > 75 ? styles.warning : ''
  const usage = `${formatMegabytes(used)} / ${formatMegabytes(total)}`
  return (
    <div className={styles.meter} title={`${device.name ?? 'GPU'} — VRAM ${usage}`}>
      <MemoryStick size={14} aria-hidden className={styles.icon} />
      <div className={styles.body}>
        <div className={styles.row}>
          <span className={styles.name}>{device.name ?? 'GPU'}</span>
          <span className={styles.usage}>{usage}</span>
        </div>
        <div
          className={styles.track}
          role="meter"
          aria-label="VRAM usage"
          aria-valuemin={0}
          aria-valuemax={total}
          aria-valuenow={used}
          aria-valuetext={usage}
        >
          <div className={`${styles.fill} ${tone}`} style={{ width: `${Math.min(100, percent)}%` }} />
        </div>
      </div>
    </div>
  )
}
