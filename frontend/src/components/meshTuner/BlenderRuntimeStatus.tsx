import { ChevronDown, Cuboid, Download, RefreshCw, Settings2, TriangleAlert } from 'lucide-react'
import { useEffect, useId, useRef, useState } from 'react'

import type { BlenderRuntimeStatus as RuntimeStatus } from '../../api/types'
import { formatBytes, formatDateTime } from '../../lib/format'
import {
  DOWNLOAD_STATE_LABEL,
  INSTALL_KIND_LABEL,
  RUNTIME_MODE_LABEL,
  RUNTIME_STATE_LABEL,
  downloadPercent,
  installVersion,
  isDownloadActive,
} from '../../lib/meshTuner'
import { useMeshTunerStore } from '../../store/meshTunerStore'
import { Button } from '../common/Button'
import { ProgressBar } from '../common/ProgressBar'
import { Spinner } from '../common/Spinner'
import styles from './BlenderRuntimeStatus.module.css'

function pillText(runtime: RuntimeStatus): string {
  const download = runtime.download
  if (runtime.state === 'downloading' || isDownloadActive(download)) {
    const percent = download ? downloadPercent(download) : null
    if (download && percent !== null) return `Blender ${Math.round(percent)}%`
    return download ? `Blender: ${DOWNLOAD_STATE_LABEL[download.state].toLowerCase()}` : 'Installing Blender'
  }
  switch (runtime.state) {
    case 'ready':
      return runtime.selected ? `Blender ${installVersion(runtime.selected)}` : 'Blender: none selected'
    case 'needs_download':
      return 'Set up Blender'
    case 'detecting':
      return 'Detecting Blender…'
    default:
      return 'Blender error'
  }
}

/** Header pill for the Blender runtime (Auto Mesh Tuner) with a details popover. */
export function BlenderRuntimeStatus({ compact = false }: { compact?: boolean }) {
  const runtime = useMeshTunerStore((s) => s.runtime)
  const runtimeLoad = useMeshTunerStore((s) => s.runtimeStatus)
  const detecting = useMeshTunerStore((s) => s.detecting)
  const detect = useMeshTunerStore((s) => s.detect)
  const setSetupOpen = useMeshTunerStore((s) => s.setSetupOpen)
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

  if (!runtime) {
    if (runtimeLoad === 'idle') return null
    if (runtimeLoad === 'loading') {
      return (
        <div className={`${styles.pill} ${styles.unavailable}`} role="status" aria-label="Checking the Blender runtime">
          <Spinner size={12} />
          <Cuboid size={14} className={styles.mark} aria-hidden />
          {!compact && <span className={styles.text}>Blender…</span>}
        </div>
      )
    }
    // Older backends without the Blender endpoints: show a muted, non-interactive pill.
    return (
      <div className={`${styles.pill} ${styles.unavailable}`} title="The Blender runtime is not available on this backend">
        <Cuboid size={14} className={styles.mark} aria-hidden />
        {!compact && <span className={styles.text}>Blender unavailable</span>}
      </div>
    )
  }

  const download = runtime.download
  const downloading = runtime.state === 'downloading' || isDownloadActive(download)
  const percent = download ? downloadPercent(download) : null
  const text = pillText(runtime)
  const tone = downloading ? 'downloading' : runtime.state
  const selected = runtime.selected

  const openSetup = () => {
    setOpen(false)
    setSetupOpen(true)
  }

  return (
    <div className={styles.root} ref={rootRef}>
      <button
        type="button"
        className={`${styles.pill} ${styles[tone]}`}
        aria-expanded={open}
        aria-controls={popoverId}
        aria-label={`Blender runtime: ${text}. Show details`}
        title={runtime.message ?? runtime.error ?? text}
        onClick={() => setOpen((value) => !value)}
      >
        {downloading || runtime.state === 'detecting' ? <Spinner size={12} /> : <span className={styles.dot} />}
        <Cuboid size={14} className={styles.mark} aria-hidden />
        {!compact && <span className={styles.text}>{text}</span>}
        {downloading && percent !== null && !compact && (
          <span className={styles.pillBar}>
            <ProgressBar value={percent} size="xs" label="Blender download progress" />
          </span>
        )}
        <ChevronDown size={14} className={styles.chevron} aria-hidden />
      </button>

      {open && (
        <div id={popoverId} className={styles.popover} role="dialog" aria-label="Blender runtime status">
          <section className={styles.section}>
            <div className={styles.sectionHeader}>
              <Cuboid size={14} className={styles.mark} aria-hidden />
              <span>Blender runtime</span>
              <span className={`${styles.stateTag} ${styles[tone]}`}>
                {downloading ? RUNTIME_STATE_LABEL.downloading : RUNTIME_STATE_LABEL[runtime.state]}
              </span>
            </div>

            {runtime.message && <p className={styles.message}>{runtime.message}</p>}
            {runtime.error && <p className={styles.errorText}>{runtime.error}</p>}

            {downloading && download && (
              <div className={styles.loadBlock}>
                <ProgressBar value={percent} size="sm" label="Blender download progress" />
                <span className={styles.meta}>
                  {DOWNLOAD_STATE_LABEL[download.state]}
                  {download.version ? ` Blender ${download.version}` : ''} · {formatBytes(download.bytes_done)}
                  {download.bytes_total ? ` / ${formatBytes(download.bytes_total)}` : ''}
                </span>
              </div>
            )}

            {selected ? (
              <dl className={styles.grid}>
                <dt>Version</dt>
                <dd>{selected.version_label ?? installVersion(selected)}</dd>
                <dt>Type</dt>
                <dd>{INSTALL_KIND_LABEL[selected.kind]}</dd>
                <dt>Mode</dt>
                <dd>{RUNTIME_MODE_LABEL[runtime.mode]}</dd>
                <dt>Path</dt>
                <dd className={styles.path}>{selected.executable}</dd>
                {runtime.selection_reason && (
                  <>
                    <dt>Reason</dt>
                    <dd>{runtime.selection_reason}</dd>
                  </>
                )}
              </dl>
            ) : (
              !runtime.message && <p className={styles.meta}>No compatible Blender is selected.</p>
            )}

            {runtime.warnings.length > 0 && (
              <ul className={styles.warnings}>
                {runtime.warnings.map((warning) => (
                  <li key={warning}>
                    <TriangleAlert size={13} aria-hidden />
                    <span>{warning}</span>
                  </li>
                ))}
              </ul>
            )}
          </section>

          <section className={styles.section}>
            <div className={styles.actions}>
              {runtime.state === 'needs_download' && (
                <Button size="sm" variant="primary" icon={<Download size={14} />} onClick={openSetup}>
                  Set up Blender…
                </Button>
              )}
              <Button size="sm" variant="subtle" icon={<Settings2 size={14} />} onClick={openSetup}>
                {runtime.state === 'needs_download' ? 'Details' : 'Change runtime…'}
              </Button>
              <Button
                size="sm"
                variant="ghost"
                icon={<RefreshCw size={14} />}
                loading={detecting}
                disabled={downloading}
                onClick={() => void detect()}
              >
                Re-detect
              </Button>
            </div>
            {runtime.last_scan_at && (
              <span className={styles.meta}>
                Last scan {formatDateTime(runtime.last_scan_at)} · requires Blender {runtime.min_version}+
              </span>
            )}
          </section>
        </div>
      )}
    </div>
  )
}
