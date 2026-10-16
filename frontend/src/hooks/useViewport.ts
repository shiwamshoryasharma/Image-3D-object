/**
 * Viewport metrics + shell postures as a module-level external store (UI_DESIGN §3.5), read with
 * `useSyncExternalStore`. The listeners (`resize` rAF-throttled, `orientationchange`, pointer/hover media queries,
 * `focusin`/`focusout` for `typing`) recompute the metrics and then the postures INSIDE the listener, so hysteresis
 * never reads or writes a variable during render (React Compiler rule). Width-only changes are applied after a 120 ms
 * trailing debounce; everything else applies at once. A new frozen snapshot is published only when a value changed,
 * so `getSnapshot` never allocates.
 */
import { useSyncExternalStore } from 'react'

import {
  SERVER_VIEWPORT_SNAPSHOT,
  nextPostures,
  type MobilePosture,
  type TabletPosture,
  type ViewportMetrics,
  type ViewportSnapshot,
} from '../lib/viewport.ts'

const WIDTH_DEBOUNCE_MS = 120
const NON_TEXT_INPUTS = new Set(['checkbox', 'radio', 'range', 'button', 'submit', 'reset', 'file', 'color', 'image', 'hidden'])

const hasWindow = typeof window !== 'undefined'

function isTextEntry(element: EventTarget | null): boolean {
  if (!hasWindow || !(element instanceof HTMLElement)) return false
  if (element instanceof HTMLTextAreaElement) return true
  if (element instanceof HTMLInputElement) return !NON_TEXT_INPUTS.has(element.type)
  return element.isContentEditable
}

function mediaMatches(query: string): boolean {
  return hasWindow && typeof window.matchMedia === 'function' && window.matchMedia(query).matches
}

function readMetrics(typing: boolean): ViewportMetrics {
  const memory = (navigator as Navigator & { deviceMemory?: unknown }).deviceMemory
  return {
    width: window.innerWidth,
    height: window.innerHeight,
    coarse: mediaMatches('(pointer: coarse)'),
    hover: mediaMatches('(hover: hover)'),
    deviceMemory: typeof memory === 'number' && Number.isFinite(memory) ? memory : null,
    typing,
  }
}

function sameMetrics(a: ViewportMetrics, b: ViewportMetrics): boolean {
  return (
    a.width === b.width &&
    a.height === b.height &&
    a.coarse === b.coarse &&
    a.hover === b.hover &&
    a.deviceMemory === b.deviceMemory &&
    a.typing === b.typing
  )
}

function buildSnapshot(metrics: ViewportMetrics, previous: ViewportSnapshot | null): ViewportSnapshot {
  const posture = nextPostures(previous?.posture ?? null, metrics)
  if (previous && previous.posture.tablet === posture.tablet && previous.posture.mobile === posture.mobile) {
    return Object.freeze({ ...metrics, posture: previous.posture })
  }
  return Object.freeze({ ...metrics, posture: Object.freeze(posture) })
}

let current: ViewportSnapshot = hasWindow ? buildSnapshot(readMetrics(isTextEntry(document.activeElement)), null) : SERVER_VIEWPORT_SNAPSHOT
const listeners = new Set<() => void>()
let detach: (() => void) | null = null
let widthTimer: number | undefined
let frame: number | undefined

function publish(metrics: ViewportMetrics): void {
  if (sameMetrics(metrics, current)) return
  current = buildSnapshot(metrics, current)
  listeners.forEach((listener) => listener())
}

function applyNow(typing: boolean = current.typing): void {
  window.clearTimeout(widthTimer)
  widthTimer = undefined
  publish(readMetrics(typing))
}

function onResize(): void {
  if (frame !== undefined) return
  frame = window.requestAnimationFrame(() => {
    frame = undefined
    const metrics = readMetrics(current.typing)
    if (metrics.height === current.height && metrics.width !== current.width) {
      // Width-only change: trailing debounce (window drags, devtools resizes).
      window.clearTimeout(widthTimer)
      widthTimer = window.setTimeout(() => applyNow(), WIDTH_DEBOUNCE_MS)
      return
    }
    applyNow()
  })
}

function attach(): () => void {
  const onOrientation = () => applyNow()
  const onMedia = () => applyNow()
  const onFocusIn = (event: FocusEvent) => applyNow(isTextEntry(event.target))
  const onFocusOut = (event: FocusEvent) => applyNow(isTextEntry(event.relatedTarget))
  const queries = ['(pointer: coarse)', '(hover: hover)']
    .filter(() => typeof window.matchMedia === 'function')
    .map((query) => window.matchMedia(query))
  window.addEventListener('resize', onResize)
  window.addEventListener('orientationchange', onOrientation)
  queries.forEach((query) => query.addEventListener('change', onMedia))
  document.addEventListener('focusin', onFocusIn)
  document.addEventListener('focusout', onFocusOut)
  // Values may have changed while nobody listened.
  applyNow(isTextEntry(document.activeElement))
  return () => {
    window.removeEventListener('resize', onResize)
    window.removeEventListener('orientationchange', onOrientation)
    queries.forEach((query) => query.removeEventListener('change', onMedia))
    document.removeEventListener('focusin', onFocusIn)
    document.removeEventListener('focusout', onFocusOut)
    window.clearTimeout(widthTimer)
    if (frame !== undefined) window.cancelAnimationFrame(frame)
    widthTimer = undefined
    frame = undefined
  }
}

/** The external store (also read outside React, e.g. by `bootDocument()`). */
export const viewportStore = {
  subscribe(listener: () => void): () => void {
    listeners.add(listener)
    if (hasWindow && !detach) detach = attach()
    return () => {
      listeners.delete(listener)
      if (listeners.size === 0 && detach) {
        detach()
        detach = null
      }
    }
  },
  getSnapshot(): ViewportSnapshot {
    return current
  },
  getServerSnapshot(): ViewportSnapshot {
    return SERVER_VIEWPORT_SNAPSHOT
  },
}

export function useViewport(): ViewportSnapshot {
  return useSyncExternalStore(viewportStore.subscribe, viewportStore.getSnapshot, viewportStore.getServerSnapshot)
}

const tabletPosture = (): TabletPosture => viewportStore.getSnapshot().posture.tablet
const mobilePosture = (): MobilePosture => viewportStore.getSnapshot().posture.mobile
const serverTabletPosture = (): TabletPosture => SERVER_VIEWPORT_SNAPSHOT.posture.tablet
const serverMobilePosture = (): MobilePosture => SERVER_VIEWPORT_SNAPSHOT.posture.mobile

/** Tablet posture only (a primitive: re-renders only when the posture changes). */
export function useTabletPosture(): TabletPosture {
  return useSyncExternalStore(viewportStore.subscribe, tabletPosture, serverTabletPosture)
}

/** Mobile posture only (a primitive). */
export function useMobilePosture(): MobilePosture {
  return useSyncExternalStore(viewportStore.subscribe, mobilePosture, serverMobilePosture)
}
