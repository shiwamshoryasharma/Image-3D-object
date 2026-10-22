import { MemoryStick } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'

import type { DeviceInfo } from '../../api/types'
import { formatMegabytes } from '../../lib/format'
import { useSystemStore } from '../../store/systemStore'
import styles from './GpuMeter.module.css'

interface Usage {
  total: number
  used: number
  percent: number
  text: string
  /** Fill class of the bar (empty below 75 %). */
  tone: string
  /** Text class of the compact percent (empty below 75 %). */
  percentTone: string
}

function usageOf(device: DeviceInfo): Usage {
  const total = device.vram_total_mb ?? 0
  const used = device.vram_used_mb ?? 0
  const percent = total > 0 ? (used / total) * 100 : 0
  const tone = percent > 90 ? styles.danger : percent > 75 ? styles.warning : ''
  const percentTone = percent > 90 ? styles.percentDanger : percent > 75 ? styles.percentWarning : ''
  const text = `${formatMegabytes(used)} / ${formatMegabytes(total)}`
  return { total, used, percent, text, tone, percentTone }
}

/** Name, usage and the VRAM bar (today's meter body; also the popover body below 1440px). */
function MeterBody({ device, usage }: { device: DeviceInfo; usage: Usage }) {
  return (
    <div className={styles.body}>
      <div className={styles.row}>
        <span className={styles.name}>{device.name ?? 'GPU'}</span>
        <span className={styles.usage}>{usage.text}</span>
      </div>
      <div
        className={styles.track}
        role="meter"
        aria-label="VRAM usage"
        aria-valuemin={0}
        aria-valuemax={usage.total}
        aria-valuenow={usage.used}
        aria-valuetext={usage.text}
      >
        <div className={`${styles.fill} ${usage.tone}`} style={{ width: `${Math.min(100, usage.percent)}%` }} />
      </div>
    </div>
  )
}

/**
 * GPU name + VRAM usage bar from `/system.device`. Below 1440px (P21: the header gets crowded at 1280–1439px) the
 * meter collapses to an icon + percent button ("GPU memory details") that opens the same meter in a popover; the
 * switch is CSS only, so there is no layout flash and nothing renders twice for assistive technology.
 */
export function GpuMeter() {
  const device = useSystemStore((s) => s.system?.device ?? null)
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const buttonRef = useRef<HTMLButtonElement>(null)
  const popoverId = useId()

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Escape') return
      setOpen(false)
      buttonRef.current?.focus()
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (!device) return null
  const cuda = device.cuda_available
  const usage = cuda ? usageOf(device) : null
  const title = usage ? `${device.name ?? 'GPU'} — VRAM ${usage.text}` : 'CUDA is not available on the backend'

  return (
    <div className={styles.root} ref={rootRef}>
      {usage ? (
        <div className={`${styles.meter} ${styles.full}`} title={title}>
          <MemoryStick size={14} aria-hidden className={styles.icon} />
          <MeterBody device={device} usage={usage} />
        </div>
      ) : (
        <div className={`${styles.meter} ${styles.full}`} title={title}>
          <MemoryStick size={14} aria-hidden className={styles.icon} />
          <span className={styles.name}>No CUDA device</span>
        </div>
      )}

      <div className={styles.compact}>
        <button
          ref={buttonRef}
          type="button"
          className={styles.compactButton}
          aria-label="GPU memory details"
          aria-expanded={open}
          aria-controls={popoverId}
          title={title}
          onClick={() => setOpen((value) => !value)}
        >
          <MemoryStick size={14} aria-hidden className={styles.icon} />
          <span className={`${styles.percent} ${usage?.percentTone ?? ''}`}>
            {usage ? `${Math.round(usage.percent)}%` : 'N/A'}
          </span>
        </button>
        {open && (
          <div id={popoverId} className={styles.popover} role="dialog" aria-label="GPU memory">
            {usage ? <MeterBody device={device} usage={usage} /> : <span className={styles.name}>No CUDA device</span>}
          </div>
        )}
      </div>
    </div>
  )
}
