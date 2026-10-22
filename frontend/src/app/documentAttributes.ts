/**
 * No-flash mode application (UI_DESIGN §3.6): `<html data-ui-mode data-density data-motion [data-posture]>` is set
 * before the first render by `bootDocument()` and re-applied by `ModeSwitch` in a layout effect. `styles/modes.css`
 * keys every per-mode override on these attributes only.
 */
import { preloadTuner } from '../components/tunerLock/lazyTuner.ts'
import { viewportStore } from '../hooks/useViewport'
import {
  parseUiOverride,
  resolveDensity,
  resolveMotion,
  resolveUiMode,
  type Density,
  type MotionAttribute,
  type UiMode,
} from '../lib/uiModes.ts'
import type { MobilePosture, TabletPosture, ViewportSnapshot } from '../lib/viewport.ts'
import { useNavigationStore } from '../store/navigationStore'
import { usePreferencesStore } from '../store/preferencesStore'
import { READY_MODES, SHELL_LOADERS } from './shellRegistry'

export interface DocumentAttributes {
  mode: UiMode
  density: Density
  motion: MotionAttribute
  posture: TabletPosture | MobilePosture | null
}

/** The posture attribute of a mode (Mobile and Tablet only). */
export function postureOf(
  mode: UiMode,
  postures: ViewportSnapshot['posture'],
): TabletPosture | MobilePosture | null {
  if (mode === 'mobile') return postures.mobile
  if (mode === 'tablet') return postures.tablet
  return null
}

export function applyDocumentAttributes(a: DocumentAttributes): void {
  const data = document.documentElement.dataset
  if (data.uiMode !== a.mode) data.uiMode = a.mode
  if (data.density !== a.density) data.density = a.density
  if (data.motion !== a.motion) data.motion = a.motion
  if (a.posture) {
    if (data.posture !== a.posture) data.posture = a.posture
  } else delete data.posture
  // The Default phone layout's toast offset flag (F4/F12) must not leak into another shell.
  if (a.mode !== 'default') delete data.defaultBar
}

/**
 * Before `createRoot`: reads the (synchronously hydrated) preferences and `?ui=`, stores the session override,
 * applies the attributes and starts the chunk the first render needs (the lazy shell, or the tuner chunk for
 * Default), so the download overlaps React's first render and the session handshake.
 */
export function bootDocument(): void {
  const preferences = usePreferencesStore.getState()
  const override = parseUiOverride(window.location.search)
  useNavigationStore.getState().setSessionUiOverride(override)
  const mode = resolveUiMode(preferences.uiMode, override, READY_MODES)
  const viewport = viewportStore.getSnapshot()
  applyDocumentAttributes({
    mode,
    density: resolveDensity(preferences.density, mode, viewport.coarse),
    motion: resolveMotion(preferences.motion),
    posture: postureOf(mode, viewport.posture),
  })
  if (mode === 'default') void preloadTuner()
  else SHELL_LOADERS[mode]().catch(() => undefined)
}
