/**
 * Safe `localStorage` wrapper. Storage may be unavailable (private mode, blocked site data), so every access
 * is guarded and failures degrade to "nothing stored".
 */

export const STORAGE_KEYS = {
  authToken: 'img3d.auth.token',
  referenceSetId: 'img3d.referenceSetId',
  viewedJobId: 'img3d.viewedJobId',
  settings: 'img3d.settings',
  ui: 'img3d.ui',
  /** Persisted Auto Tune preset/options form. */
  meshTuner: 'img3d.meshTuner',
  /** Blender setup prompt (`needs_download` message) the user dismissed; the dialog auto-opens once per message. */
  blenderSetupDismissed: 'img3d.blenderSetup.dismissedMessage',
} as const

function getStorage(): Storage | null {
  try {
    return typeof window !== 'undefined' ? window.localStorage : null
  } catch {
    return null
  }
}

export function readString(key: string): string | null {
  try {
    return getStorage()?.getItem(key) ?? null
  } catch {
    return null
  }
}

export function writeString(key: string, value: string | null): void {
  try {
    const storage = getStorage()
    if (!storage) return
    if (value === null) storage.removeItem(key)
    else storage.setItem(key, value)
  } catch {
    // Storage full or blocked: persistence is best-effort.
  }
}
