import { Suspense, useEffect } from 'react'

import { Toaster } from '../components/common/Toaster'
import { AppSettingsDialog } from '../components/preferences/AppSettingsDialog'
import { LazyBlenderSetupDialog } from '../components/tunerLock/lazyTuner.ts'
import { useTunerLocked } from '../hooks/useTunerLocked'
import { useTunerVisible } from '../hooks/useTunerVisible'
import { STORAGE_KEYS } from '../lib/storage'
import { readPersistedState, sanitizePreferences } from '../lib/uiModes.ts'
import { useAuthStore } from '../store/authStore'
import { usePreferencesStore } from '../store/preferencesStore'
import { Announcer } from './Announcer'
import { GlobalShortcuts } from './GlobalShortcuts'
import { prefetchMode, prepareMode } from './shellRegistry'

/** Preferences changed in another tab: load the new shell first (if the mode changed), then adopt the values. */
function onPreferencesStorage(event: StorageEvent): void {
  if (event.key !== STORAGE_KEYS.preferences || event.storageArea !== window.localStorage) return
  const next = sanitizePreferences(readPersistedState(event.newValue))
  if (Object.keys(next).length === 0) return
  const adopt = () => usePreferencesStore.setState(next)
  if (next.uiMode && next.uiMode !== usePreferencesStore.getState().uiMode) void prepareMode(next.uiMode).then(adopt, adopt)
  else adopt()
}

/**
 * App-wide overlays and singletons (UI_DESIGN §1.6): Toaster, App settings, Announcer, the global shortcuts, the
 * cross-tab preferences listener, and `BlenderSetupDialog`, which mounts only in the Default mode while the tuner is
 * unlocked (legacy backend or approved, §3.9). While locked it is never mounted, so the store's auto-open has no
 * renderer and nothing writes `blenderSetupDismissed`.
 */
export function GlobalOverlays() {
  const phase = useAuthStore((s) => s.phase)
  const tunerVisible = useTunerVisible()
  const tunerLocked = useTunerLocked()

  useEffect(() => {
    window.addEventListener('storage', onPreferencesStorage)
    return () => window.removeEventListener('storage', onPreferencesStorage)
  }, [])

  return (
    <>
      {phase === 'ready' && tunerVisible && !tunerLocked && (
        <Suspense fallback={null}>
          <LazyBlenderSetupDialog />
        </Suspense>
      )}
      <Toaster />
      <AppSettingsDialog prepareMode={prepareMode} prefetchMode={prefetchMode} />
      <Announcer />
      <GlobalShortcuts />
    </>
  )
}
