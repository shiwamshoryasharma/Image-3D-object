/**
 * Whether tuner parts may render at all (UI_DESIGN §3.9): only the Default shell has tuner mount points. Shared
 * components with tuner parts (`ViewerPanel`, `ModelStatsContent`, `HistoryItem`) render them only when this is
 * true, so Mobile, Tablet and Node never add tuner UI. A primitive.
 */
import { useResolvedUiMode } from './useResolvedUiMode'

export function useTunerVisible(): boolean {
  return useResolvedUiMode() === 'default'
}
