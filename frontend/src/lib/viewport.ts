/**
 * Viewport classification and shell postures (UI_DESIGN.md §3.5, §4.4, §4.5.1).
 *
 * The posture functions take the previous posture and apply hysteresis, so a resize across a threshold does not
 * flicker. They are called OUTSIDE render, inside the `hooks/useViewport.ts` store listeners (which also debounce
 * width changes by 120 ms); nothing here reads or writes module state.
 *
 * PURE (UI_DESIGN §1.5): relative `.ts` value imports only; no React, zustand, axios or browser globals.
 */

export interface ViewportMetrics {
  readonly width: number
  readonly height: number
  /** `(pointer: coarse)` */
  readonly coarse: boolean
  /** `(hover: hover)` */
  readonly hover: boolean
  /** `navigator.deviceMemory` in GB, `null` when the browser does not report it. */
  readonly deviceMemory: number | null
  /** An input, textarea or contenteditable has focus (the soft keyboard may be up). */
  readonly typing: boolean
}

export type TabletPosture = 'rail' | 'bottom' | 'compact'
export type MobilePosture = 'portrait' | 'landscape'

export interface ShellPostures {
  readonly tablet: TabletPosture
  readonly mobile: MobilePosture
}

export interface ViewportSnapshot extends ViewportMetrics {
  /** Computed outside render by the viewport store listeners. */
  readonly posture: Readonly<ShellPostures>
}

/** `useSyncExternalStore` server snapshot (UI_DESIGN §3.5). */
export const SERVER_VIEWPORT_SNAPSHOT: ViewportSnapshot = Object.freeze({
  width: 1280,
  height: 800,
  coarse: false,
  hover: true,
  deviceMemory: null,
  typing: false,
  posture: Object.freeze({ tablet: 'rail', mobile: 'portrait' } as const),
})

/** < 600 · 600–839 · 840–1199 · ≥ 1200 */
export type WidthClass = 'compact' | 'medium' | 'expanded' | 'large'

export function widthClass(w: number): WidthClass {
  if (w < 600) return 'compact'
  if (w < 840) return 'medium'
  if (w < 1200) return 'expanded'
  return 'large'
}

/**
 * Tablet posture with hysteresis (UI_DESIGN §4.5.1), evaluated in this order:
 * 1. typing guard: keep `prev` (the soft keyboard must not flip the layout);
 * 2. compact: enter below 600 px wide, stay below 632 px;
 * 3. rail: enter in landscape (w ≥ 1.05·h) when w ≥ 840, or h < 520 and w ≥ 640 (phone landscape);
 *    stay while w ≥ 0.95·h and (w ≥ 800, or h < 540 and w ≥ 620);
 * 4. otherwise bottom.
 */
export function nextTabletPosture(prev: TabletPosture | null, v: ViewportMetrics): TabletPosture {
  const { width: w, height: h } = v
  if (v.typing && prev !== null) return prev
  if (w < 600 || (prev === 'compact' && w < 632)) return 'compact'
  const enterRail = w >= 1.05 * h && (w >= 840 || (h < 520 && w >= 640))
  const stayRail = prev === 'rail' && w >= 0.95 * h && (w >= 800 || (h < 540 && w >= 620))
  return enterRail || stayRail ? 'rail' : 'bottom'
}

/**
 * Mobile posture with hysteresis (UI_DESIGN §3.5): enter landscape when w ≥ 1.05·h and h < 520; stay while
 * w ≥ 0.95·h and h < 540; else portrait. The typing guard of the tablet rule applies too (a soft keyboard shrinks the
 * height of a portrait phone and must not switch it to the landscape rail).
 */
export function nextMobilePosture(prev: MobilePosture | null, v: ViewportMetrics): MobilePosture {
  const { width: w, height: h } = v
  if (v.typing && prev !== null) return prev
  const enter = w >= 1.05 * h && h < 520
  const stay = prev === 'landscape' && w >= 0.95 * h && h < 540
  return enter || stay ? 'landscape' : 'portrait'
}

/** Both postures for new metrics (`prev = null` at boot). */
export function nextPostures(prev: ShellPostures | null, v: ViewportMetrics): ShellPostures {
  return {
    tablet: nextTabletPosture(prev?.tablet ?? null, v),
    mobile: nextMobilePosture(prev?.mobile ?? null, v),
  }
}

/** Low-memory devices keep only one large model mounted in the viewer (UI_DESIGN §3.5, §6.6). */
export function isLowMemory(v: ViewportMetrics): boolean {
  return (v.deviceMemory !== null && v.deviceMemory <= 4) || (v.coarse && v.width < 1024)
}
