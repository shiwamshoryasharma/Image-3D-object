/**
 * Lazily loaded 3D viewer (three.js + R3F + drei live in this chunk only).
 */
import { useProgress } from '@react-three/drei'
import { Canvas, useLoader } from '@react-three/fiber'
import { RefreshCw, TriangleAlert } from 'lucide-react'
import { useEffect, useLayoutEffect, useState } from 'react'
import { NeutralToneMapping } from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

import { formatBytes } from '../../lib/format'
import { Button } from '../common/Button'
import { ProgressBar } from '../common/ProgressBar'
import { ModelScene } from './ModelScene'
import { useViewerRuntime } from './viewerRuntime'
import type { ModelViewerProps } from './viewerTypes'
import styles from './ModelViewer.module.css'

const GL_OPTIONS = { antialias: true, alpha: true, toneMapping: NeutralToneMapping, powerPreference: 'high-performance' } as const
const CAMERA = { fov: 40, near: 0.01, far: 200, position: [2, 1.4, 2.4] as [number, number, number] }

function LoadingOverlay() {
  const status = useViewerRuntime((s) => s.status)
  const loaded = useViewerRuntime((s) => s.loadedBytes)
  const total = useViewerRuntime((s) => s.totalBytes)
  // drei's loading-manager progress (item based) is the fallback when the server sends no Content-Length.
  const managerActive = useProgress((s) => s.active)
  const itemProgress = useProgress((s) => s.progress)
  if (status !== 'loading') return null

  const bytePercent = total > 0 ? (loaded / total) * 100 : null
  const downloading = bytePercent === null || bytePercent < 100
  const percent = bytePercent ?? (managerActive && itemProgress > 0 && itemProgress < 100 ? itemProgress : null)
  return (
    <div className={styles.overlay}>
      <div className={styles.loadingCard}>
        {/* Only the phase is announced; the byte counter below changes many times per second. */}
        <span className={styles.loadingTitle} role="status">
          {downloading ? 'Downloading model…' : 'Preparing model…'}
        </span>
        <ProgressBar value={downloading ? percent : null} size="sm" label="Model loading progress" />
        <span className={styles.loadingMeta}>
          {total > 0 ? `${formatBytes(loaded)} / ${formatBytes(total)}` : loaded > 0 ? formatBytes(loaded) : 'Connecting…'}
          {percent !== null && downloading ? ` · ${Math.round(percent)}%` : ''}
        </span>
      </div>
    </div>
  )
}

export default function ModelViewer({
  url,
  secondaryUrl = null,
  showSecondary = false,
  options,
  active,
  onApi,
  parts = null,
}: ModelViewerProps) {
  const [retryKey, setRetryKey] = useState(0)
  const [secondaryRetryKey, setSecondaryRetryKey] = useState(0)
  const [partsRetryKey, setPartsRetryKey] = useState(0)
  const partsUrl = parts?.url ?? null
  const status = useViewerRuntime((s) => s.status)
  const error = useViewerRuntime((s) => s.error)

  useLayoutEffect(() => {
    useViewerRuntime.getState().start()
  }, [url, retryKey])

  // The secondary (tuned) model has its own load status; reset it whenever it is (re)mounted or removed.
  useLayoutEffect(() => {
    if (secondaryUrl) useViewerRuntime.getState().startSecondary()
    else useViewerRuntime.getState().resetSecondary()
    return () => useViewerRuntime.getState().resetSecondary()
  }, [secondaryUrl, retryKey, secondaryRetryKey])

  // Let the DOM overlay retry a failed tuned model without reloading the original.
  useEffect(() => {
    if (!secondaryUrl) return
    useViewerRuntime.getState().setRetrySecondary(() => {
      useLoader.clear(GLTFLoader, secondaryUrl)
      setSecondaryRetryKey((key) => key + 1)
    })
    return () => useViewerRuntime.getState().setRetrySecondary(null)
  }, [secondaryUrl])

  // The parts model (UI_DESIGN §6.6) has its own load status and retry, like the secondary one. "loading" is set by
  // its Suspense fallback (only while it really suspends), "ready" by the model itself.
  useLayoutEffect(() => {
    if (!partsUrl) useViewerRuntime.getState().resetParts()
  }, [partsUrl])
  useLayoutEffect(() => () => useViewerRuntime.getState().resetParts(), [])

  useEffect(() => {
    if (!partsUrl) return
    useViewerRuntime.getState().setRetryParts(() => {
      useLoader.clear(GLTFLoader, partsUrl)
      setPartsRetryKey((key) => key + 1)
    })
    return () => useViewerRuntime.getState().setRetryParts(null)
  }, [partsUrl])

  const retry = () => {
    useLoader.clear(GLTFLoader, url)
    if (secondaryUrl) useLoader.clear(GLTFLoader, secondaryUrl)
    if (partsUrl) useLoader.clear(GLTFLoader, partsUrl)
    setRetryKey((key) => key + 1)
  }

  return (
    <div className={styles.root}>
      <Canvas
        className={styles.canvas}
        frameloop={active ? 'always' : 'never'}
        dpr={[1, 2]}
        camera={CAMERA}
        gl={GL_OPTIONS}
        aria-label="Interactive 3D model viewer. Drag to orbit, scroll to zoom, right-drag to pan."
      >
        <ModelScene
          url={url}
          secondaryUrl={secondaryUrl}
          showSecondary={showSecondary}
          options={options}
          retryKey={retryKey}
          secondaryRetryKey={secondaryRetryKey}
          onApi={onApi}
          parts={parts}
          partsRetryKey={partsRetryKey}
        />
      </Canvas>

      <LoadingOverlay />

      {status === 'error' && (
        <div className={styles.overlay} role="alert">
          <div className={styles.errorCard}>
            <TriangleAlert size={22} className={styles.errorIcon} aria-hidden />
            <p className={styles.errorTitle}>Could not load the model</p>
            <p className={styles.errorMessage}>{error}</p>
            <Button size="sm" icon={<RefreshCw size={14} />} onClick={retry}>
              Try again
            </Button>
          </div>
        </div>
      )}
    </div>
  )
}
