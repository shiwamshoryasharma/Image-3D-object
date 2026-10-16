/**
 * The UI mode actually rendered (UI_DESIGN §3.5): `resolveUiMode(preference, ?ui= / session override, ready modes)`.
 * The ready set lives in `navigationStore.readyModes` (written once at boot from `app/shellRegistry.READY_MODES`),
 * because hooks cannot import `app/` (§1.4). Until then only `default` is ready, which matches the placeholders.
 */
import type { UiMode } from '../lib/uiModes.ts'
import { resolveUiMode } from '../lib/uiModes.ts'
import { useNavigationStore } from '../store/navigationStore'
import { usePreferencesStore } from '../store/preferencesStore'

export function useResolvedUiMode(): UiMode {
  const preference = usePreferencesStore((s) => s.uiMode)
  const override = useNavigationStore((s) => s.sessionUiOverride)
  const ready = useNavigationStore((s) => s.readyModes)
  return resolveUiMode(preference, override, ready)
}
