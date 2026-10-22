/**
 * `lazy()` wrappers of the paused tuner components (UI_DESIGN §1.3, §3.9). Each is rendered inside its own
 * `<Suspense fallback={null}>`, in today's place, wrapped by `TunerLockFrame`. `preloadTuner()` starts the chunk
 * download early (`bootDocument()` / `prepareMode('default')`), so the slots normally resolve before the session is
 * ready.
 */
import { lazy } from 'react'

const loadTuner = () => import('./tunerBundle.ts')

/** Starts (or reuses) the tuner chunk download; never rejects (a failed load surfaces in the lazy slot instead). */
export function preloadTuner(): Promise<void> {
  return loadTuner().then(
    () => undefined,
    () => undefined,
  )
}

export const LazyTuningCard = lazy(() => loadTuner().then((m) => ({ default: m.TuningCard })))
export const LazyBlenderRuntimeStatus = lazy(() => loadTuner().then((m) => ({ default: m.BlenderRuntimeStatus })))
export const LazyBlenderSetupDialog = lazy(() => loadTuner().then((m) => ({ default: m.BlenderSetupDialog })))
export const LazyTunedDownloadButton = lazy(() => loadTuner().then((m) => ({ default: m.TunedDownloadButton })))
