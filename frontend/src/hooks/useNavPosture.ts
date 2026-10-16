/**
 * The current posture of one shell (UI_DESIGN §3.5, §4.4, §4.5.1): a primitive from the viewport store, whose
 * listeners apply the hysteresis outside render.
 */
import { useSyncExternalStore } from 'react'

import { SERVER_VIEWPORT_SNAPSHOT, type MobilePosture, type ShellPostures, type TabletPosture } from '../lib/viewport.ts'
import { viewportStore } from './useViewport'

export function useNavPosture(shell: 'tablet'): TabletPosture
export function useNavPosture(shell: 'mobile'): MobilePosture
export function useNavPosture(shell: keyof ShellPostures): TabletPosture | MobilePosture {
  return useSyncExternalStore(
    viewportStore.subscribe,
    () => viewportStore.getSnapshot().posture[shell],
    () => SERVER_VIEWPORT_SNAPSHOT.posture[shell],
  )
}
