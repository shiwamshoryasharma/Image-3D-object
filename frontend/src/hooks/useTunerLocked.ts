/**
 * Tuner lock (UI_DESIGN §3.9, §2.5): the Default mode keeps today's Auto Tune card and Blender pill in place, locked
 * while the backend reports `mesh_tuning.enabled === false`. Both hooks return primitives.
 *   legacy backend (`/features` 404)       → unlocked (today's UI exactly, UI1)
 *   ready + mesh_tuning.enabled === true   → unlocked (approval: no code change, no rebuild)
 *   ready + disabled, loading, error       → locked (fail-closed: the Blender download routes are not gated)
 * There is no frontend build flag for the tuner; the backend gate S9 holds independently.
 */
import type { FeaturesSnapshot } from '../lib/featureFlags.ts'
import { useFeaturesStore } from '../store/featuresStore'

export type TunerLock = 'unlocked' | 'locked'

export const TUNER_LOCK_NOTICE = 'Paused until you approve the part-aware milestone'

export function tunerLockOf(f: FeaturesSnapshot): TunerLock {
  if (f.status === 'legacy') return 'unlocked'
  if (f.status === 'ready' && f.meshTuning.enabled === true) return 'unlocked'
  return 'locked'
}

export function tunerLockNoticeOf(f: FeaturesSnapshot): string {
  if (f.status === 'loading') return 'Checking whether Auto Tune is available…'
  if (f.status === 'error') return 'Locked: backend features could not be read.'
  return TUNER_LOCK_NOTICE
}

export function useTunerLocked(): boolean {
  return useFeaturesStore((s) => tunerLockOf(s.snapshot) === 'locked')
}

/** Text of the lock notice strip (`TunerLockNotice`, F6); exactly `TUNER_LOCK_NOTICE` once features are ready. */
export function useTunerLockNotice(): string {
  return useFeaturesStore((s) => tunerLockNoticeOf(s.snapshot))
}
