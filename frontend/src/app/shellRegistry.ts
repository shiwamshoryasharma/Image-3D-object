/**
 * Shell registry (UI_DESIGN §3.7): the only place that reaches `shells/*`. Lazy shells load through
 * `SHELL_LOADERS` (one cached promise per mode, so `prepareMode`, prefetch and `React.lazy` share the download);
 * `READY_MODES` comes from the tiny static `ready.ts` flags, so it is known without loading any shell chunk.
 * Module scope publishes `READY_MODES` to `navigationStore.readyModes` before the first render (hooks cannot import
 * `app/`, §1.4).
 */
import type { ComponentType } from 'react'

import { preloadTuner } from '../components/tunerLock/lazyTuner.ts'
import type { UiMode } from '../lib/uiModes.ts'
import DefaultShell from '../shells/default/index.ts'
import { SHELL_READY as MOBILE_READY } from '../shells/mobile/ready.ts'
import { SHELL_READY as NODE_READY } from '../shells/node/ready.ts'
import { SHELL_READY as TABLET_READY } from '../shells/tablet/ready.ts'
import { useNavigationStore } from '../store/navigationStore'

/** The only prop any shell receives. */
export interface ShellProps {
  nextRetryAt: number | null
}

export type LazyUiMode = Exclude<UiMode, 'default'>

type ShellModule = { default: ComponentType<ShellProps> }

const cache: Partial<Record<LazyUiMode, Promise<ShellModule>>> = {}

function cached(mode: LazyUiMode, load: () => Promise<ShellModule>): () => Promise<ShellModule> {
  return () => {
    const pending = cache[mode] ?? load()
    cache[mode] = pending
    return pending
  }
}

export const SHELL_LOADERS: Readonly<Record<LazyUiMode, () => Promise<ShellModule>>> = Object.freeze({
  node: cached('node', () => import('../shells/node/index.ts')),
  mobile: cached('mobile', () => import('../shells/mobile/index.ts')),
  tablet: cached('tablet', () => import('../shells/tablet/index.ts')),
})

const READY_FLAGS: Readonly<Record<LazyUiMode, boolean>> = Object.freeze({
  node: NODE_READY,
  mobile: MOBILE_READY,
  tablet: TABLET_READY,
})

/** `default` plus every mode whose `ready.ts` exports `SHELL_READY = true`. */
export const READY_MODES: ReadonlySet<UiMode> = Object.freeze(
  new Set<UiMode>(['default', ...(Object.keys(READY_FLAGS) as LazyUiMode[]).filter((mode) => READY_FLAGS[mode])]),
)

useNavigationStore.getState().setReadyModes(READY_MODES)

export { DefaultShell }

/**
 * Every mode switch awaits this first (UiModePicker, "Use Default UI", the cross-tab listener), so the new shell
 * never suspends after the switch (§3.7). `default` only starts the tuner chunk (not awaited: the tuner slots have
 * their own Suspense).
 */
export async function prepareMode(mode: UiMode): Promise<void> {
  if (mode === 'default') {
    void preloadTuner()
    return
  }
  await SHELL_LOADERS[mode]()
}

/** Focus/hover prefetch of a mode card (App settings); failures surface when the mode is selected. */
export function prefetchMode(mode: UiMode): void {
  if (mode === 'default' || !READY_MODES.has(mode)) return
  SHELL_LOADERS[mode]().catch(() => undefined)
}
