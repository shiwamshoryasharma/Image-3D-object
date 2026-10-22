import { ChevronDown, Cpu, Power } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'

import type { EngineState, SystemStatus } from '../../api/types'
import { formatDateTime, formatMegabytes } from '../../lib/format'
import { useSystemStore } from '../../store/systemStore'
import { Button } from '../common/Button'
import { ProgressBar } from '../common/ProgressBar'
import { Spinner } from '../common/Spinner'
import styles from './EngineStatus.module.css'

/** Engine-state words (also used by the shared shell chrome, so the wording stays in one place). */
// oxlint-disable-next-line react/only-export-components -- one shared table; costs only a dev-time full HMR reload
export const STATE_LABEL: Record<EngineState, string> = {
  not_loaded: 'Model not loaded',
  loading: 'Loading model',
  ready: 'Model ready',
  error: 'Model error',
  unavailable: 'Engine unavailable',
}

function loadPercent(system: SystemStatus): number | null {
  const progress = system.engine.load_progress
  if (!progress || progress.total <= 0) return null
  return (progress.loaded / progress.total) * 100
}

/** Engine state pill with a details popover (load progress, errors, device, queue). */
export function EngineStatus({ compact = false }: { compact?: boolean }) {
  const system = useSystemStore((s) => s.system)
  const reachable = useSystemStore((s) => s.reachable)
  const loadEngine = useSystemStore((s) => s.loadEngine)
  const loadPending = useSystemStore((s) => s.engineLoadPending)
  const [open, setOpen] = useState(false)
  const rootRef = useRef<HTMLDivElement>(null)
  const popoverId = useId()

  useEffect(() => {
    if (!open) return
    const onPointerDown = (event: PointerEvent) => {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false)
    }
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('pointerdown', onPointerDown)
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('pointerdown', onPointerDown)
      document.removeEventListener('keydown', onKeyDown)
    }
  }, [open])

  if (!system) {
    return (
      <div className={`${styles.pill} ${styles.unknown}`}>
        {reachable === false ? <span className={styles.dot} /> : <Spinner size={12} />}
        {!compact && <span className={styles.text}>{reachable === false ? 'Backend offline' : 'Connecting…'}</span>}
      </div>
    )
  }

  const { engine, device, queue, app } = system
  const percent = loadPercent(system)
  const progress = engine.load_progress
  // `unavailable` is retryable too: e.g. missing aux models can be downloaded while the server keeps running.
  const canLoad = engine.state === 'not_loaded' || engine.state === 'error' || engine.state === 'unavailable'
  const pillText =
    engine.state === 'loading' && progress
      ? `${STATE_LABEL.loading} ${progress.loaded}/${progress.total}`
      : STATE_LABEL[engine.state]

  return (
    <div className={styles.root} ref={rootRef}>
      <button
        type="button"
        className={`${styles.pill} ${styles[engine.state]}`}
        aria-expanded={open}
        aria-controls={popoverId}
        aria-label={`Engine status: ${pillText}. Show details`}
        title={engine.error ?? engine.message ?? pillText}
        onClick={() => setOpen((value) => !value)}
      >
        {engine.state === 'loading' ? <Spinner size={12} /> : <span className={styles.dot} />}
        {!compact && <span className={styles.text}>{pillText}</span>}
        {engine.state === 'loading' && percent !== null && !compact && (
          <span className={styles.pillBar}>
            <ProgressBar value={percent} size="xs" label="Model load progress" />
          </span>
        )}
        <ChevronDown size={14} className={styles.chevron} aria-hidden />
      </button>

      {open && (
        <div id={popoverId} className={styles.popover} role="dialog" aria-label="System status">
          <section className={styles.section}>
            <div className={styles.sectionHeader}>
              <Cpu size={14} aria-hidden />
              <span>Engine · {engine.name}</span>
              <span className={`${styles.stateTag} ${styles[engine.state]}`}>{STATE_LABEL[engine.state]}</span>
            </div>
            {engine.message && <p className={styles.message}>{engine.message}</p>}
            {engine.state === 'loading' && (
              <div className={styles.loadBlock}>
                <ProgressBar value={percent} size="sm" label="Model load progress" />
                {progress && (
                  <span className={styles.meta}>
                    {progress.loaded}/{progress.total} components{progress.current ? ` · ${progress.current}` : ''}
                  </span>
                )}
              </div>
            )}
            {engine.error && <p className={styles.errorText}>{engine.error}</p>}
            <dl className={styles.grid}>
              {engine.loaded_at && (
                <>
                  <dt>Loaded</dt>
                  <dd>{formatDateTime(engine.loaded_at)}</dd>
                </>
              )}
              {typeof engine.details.attention_backend === 'string' && (
                <>
                  <dt>Attention</dt>
                  <dd>{engine.details.attention_backend}</dd>
                </>
              )}
              {typeof engine.details.low_vram === 'boolean' && (
                <>
                  <dt>Low-VRAM mode</dt>
                  <dd>{engine.details.low_vram ? 'On' : 'Off'}</dd>
                </>
              )}
              <dt>Reference mode</dt>
              <dd>{engine.capabilities.reference_modes.join(', ')}</dd>
            </dl>
            {canLoad && (
              <Button
                size="sm"
                variant="subtle"
                icon={<Power size={14} />}
                loading={loadPending}
                onClick={() => void loadEngine()}
              >
                {engine.state === 'not_loaded' ? 'Load model' : 'Retry loading model'}
              </Button>
            )}
          </section>

          <section className={styles.section}>
            <div className={styles.sectionHeader}>
              <span>GPU</span>
            </div>
            {device ? (
              <dl className={styles.grid}>
                <dt>Device</dt>
                <dd>{device.cuda_available ? (device.name ?? 'CUDA device') : 'CUDA not available'}</dd>
                {device.compute_capability && (
                  <>
                    <dt>Compute</dt>
                    <dd>sm {device.compute_capability}</dd>
                  </>
                )}
                {device.vram_total_mb !== null && (
                  <>
                    <dt>VRAM</dt>
                    <dd>
                      {formatMegabytes(device.vram_used_mb)} / {formatMegabytes(device.vram_total_mb)}
                    </dd>
                  </>
                )}
                {device.torch_version && (
                  <>
                    <dt>Torch</dt>
                    <dd>
                      {device.torch_version}
                      {device.cuda_version ? ` · CUDA ${device.cuda_version}` : ''}
                    </dd>
                  </>
                )}
              </dl>
            ) : (
              <p className={styles.meta}>Device information is not available (torch not importable).</p>
            )}
          </section>

          <section className={styles.section}>
            <dl className={styles.grid}>
              <dt>Queue</dt>
              <dd>
                {queue.pending} pending / {queue.max_size} max{queue.running_job_id ? ' · 1 running' : ''}
              </dd>
              <dt>Backend</dt>
              <dd>
                {app.name} {app.version} · {app.environment}
              </dd>
            </dl>
          </section>
        </div>
      )}
    </div>
  )
}
