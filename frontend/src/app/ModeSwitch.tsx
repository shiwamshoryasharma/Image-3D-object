import { lazy, Suspense, useLayoutEffect, useRef, useSyncExternalStore, type ComponentType, type RefObject } from 'react'

import { useResolvedUiMode } from '../hooks/useResolvedUiMode'
import { useMobilePosture, useTabletPosture, viewportStore } from '../hooks/useViewport'
import { resolveDensity, resolveMotion, UI_MODE_META, type UiMode } from '../lib/uiModes.ts'
import { announce } from '../store/announcerStore'
import { usePreferencesStore } from '../store/preferencesStore'
import { applyDocumentAttributes } from './documentAttributes'
import { ShellErrorBoundary } from './ShellErrorBoundary'
import { ShellFallback } from './ShellFallback'
import { DefaultShell, SHELL_LOADERS, type ShellProps } from './shellRegistry'

const SHELLS: Readonly<Record<UiMode, ComponentType<ShellProps>>> = Object.freeze({
  default: DefaultShell,
  node: lazy(SHELL_LOADERS.node),
  mobile: lazy(SHELL_LOADERS.mobile),
  tablet: lazy(SHELL_LOADERS.tablet),
})

const readCoarse = () => viewportStore.getSnapshot().coarse
const serverCoarse = () => false

/**
 * Runs when the shell has actually committed (inside the Suspense boundary, after a lazy chunk resolved): on a mode
 * change (not the first mount) focus moves to the new shell's `#main-content` and the mode name is announced.
 */
function ShellArrival({ mode, arrivedRef }: { mode: UiMode; arrivedRef: RefObject<UiMode | null> }) {
  useLayoutEffect(() => {
    const previous = arrivedRef.current
    arrivedRef.current = mode
    if (previous === null || previous === mode) return
    document.getElementById('main-content')?.focus({ preventScroll: true })
    announce(UI_MODE_META[mode].name)
  }, [mode, arrivedRef])
  return null
}

/**
 * Renders the resolved UI mode's shell (UI_DESIGN §3.7) and keeps `<html data-ui-mode data-density data-motion
 * data-posture>` in sync in a layout effect (no frame with stale attributes). Every switch is preceded by
 * `prepareMode(mode)`, so a lazy shell is already downloaded when it mounts.
 */
export function ModeSwitch({ nextRetryAt }: ShellProps) {
  const mode = useResolvedUiMode()
  const densityPreference = usePreferencesStore((s) => s.density)
  const motionPreference = usePreferencesStore((s) => s.motion)
  const coarse = useSyncExternalStore(viewportStore.subscribe, readCoarse, serverCoarse)
  const tabletPosture = useTabletPosture()
  const mobilePosture = useMobilePosture()
  const arrivedRef = useRef<UiMode | null>(null)

  const density = resolveDensity(densityPreference, mode, coarse)
  const motion = resolveMotion(motionPreference)
  const posture = mode === 'mobile' ? mobilePosture : mode === 'tablet' ? tabletPosture : null

  useLayoutEffect(() => {
    applyDocumentAttributes({ mode, density, motion, posture })
  }, [mode, density, motion, posture])

  const Shell = SHELLS[mode]
  return (
    <ShellErrorBoundary key={mode} mode={mode}>
      <Suspense fallback={<ShellFallback mode={mode} />}>
        <Shell nextRetryAt={nextRetryAt} />
        <ShellArrival mode={mode} arrivedRef={arrivedRef} />
      </Suspense>
    </ShellErrorBoundary>
  )
}
