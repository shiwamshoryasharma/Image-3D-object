/**
 * Backend capabilities for components (UI_DESIGN §2.5). Both hooks return stored references or primitives: the
 * snapshot object is replaced only by `featuresStore.refresh()` / `apply()`.
 */
import type { FeaturesSnapshot, FeaturesStatus } from '../lib/featureFlags.ts'
import { useFeaturesStore } from '../store/featuresStore'

export function useFeatures(): FeaturesSnapshot {
  return useFeaturesStore((s) => s.snapshot)
}

export function useFeaturesStatus(): FeaturesStatus {
  return useFeaturesStore((s) => s.status)
}
