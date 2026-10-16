import { useSyncExternalStore } from 'react'

/**
 * On-screen keyboard detection for the touch shells (UI_DESIGN §4.4): the keyboard counts as open while an editable
 * field has focus and the visual viewport is shorter than 75 % of the layout viewport. While at least one component
 * uses the hook, `--keyboard-inset` on <html> holds how much of the layout viewport the keyboard covers, so a sheet
 * footer can sit above it.
 */
const OPEN_RATIO = 0.75

/** `<input>` types that never raise a soft keyboard. */
const NON_TEXT_INPUTS = new Set(['button', 'checkbox', 'color', 'file', 'hidden', 'image', 'radio', 'range', 'reset', 'submit'])

export interface SoftKeyboardMetrics {
  /** An editable field has focus. */
  readonly typing: boolean
  /** `innerHeight` measured the last time nothing was being edited (the height without a keyboard). */
  readonly baselineHeight: number
  /** Current `innerHeight` (the layout viewport; shrinks with the keyboard only where the page is resized). */
  readonly layoutHeight: number
  readonly viewportHeight: number
  readonly viewportOffsetTop: number
  readonly scale: number
}

export interface SoftKeyboardState {
  readonly open: boolean
  /** CSS px of the layout viewport hidden below the visual viewport (0 while closed). */
  readonly inset: number
}

/** The detection rule as a pure function (unit-tested). */
export function softKeyboardState(metrics: SoftKeyboardMetrics): SoftKeyboardState {
  const reference = Math.max(metrics.baselineHeight, metrics.layoutHeight)
  // A pinch zoom shrinks the visual viewport by the scale factor; the keyboard shrinks it in screen pixels.
  const visible = metrics.viewportHeight * metrics.scale
  const open = metrics.typing && reference > 0 && visible < OPEN_RATIO * reference
  const inset = open ? Math.max(0, Math.round(metrics.layoutHeight - metrics.viewportOffsetTop - metrics.viewportHeight)) : 0
  return { open, inset }
}

function isEditable(target: EventTarget | null): boolean {
  if (target instanceof HTMLTextAreaElement) return !target.readOnly && !target.disabled
  if (target instanceof HTMLInputElement) return !NON_TEXT_INPUTS.has(target.type) && !target.readOnly && !target.disabled
  return target instanceof HTMLElement && target.isContentEditable
}

const listeners = new Set<() => void>()
let open = false
let inset = -1
let typing = false
let baselineHeight = 0

function measure(): void {
  if (!typing) baselineHeight = window.innerHeight
  const viewport = window.visualViewport
  const state = viewport
    ? softKeyboardState({
        typing,
        baselineHeight,
        layoutHeight: window.innerHeight,
        viewportHeight: viewport.height,
        viewportOffsetTop: viewport.offsetTop,
        scale: viewport.scale,
      })
    : { open: false, inset: 0 }
  if (state.inset !== inset) {
    inset = state.inset
    document.documentElement.style.setProperty('--keyboard-inset', `${inset}px`)
  }
  if (state.open === open) return
  open = state.open
  for (const listener of [...listeners]) listener()
}

function onFocusIn(event: FocusEvent): void {
  typing = isEditable(event.target)
  measure()
}

function onFocusOut(event: FocusEvent): void {
  typing = isEditable(event.relatedTarget)
  measure()
}

function attach(): void {
  typing = isEditable(document.activeElement)
  document.addEventListener('focusin', onFocusIn)
  document.addEventListener('focusout', onFocusOut)
  window.visualViewport?.addEventListener('resize', measure)
  window.visualViewport?.addEventListener('scroll', measure)
  window.addEventListener('resize', measure)
  measure()
}

function detach(): void {
  document.removeEventListener('focusin', onFocusIn)
  document.removeEventListener('focusout', onFocusOut)
  window.visualViewport?.removeEventListener('resize', measure)
  window.visualViewport?.removeEventListener('scroll', measure)
  window.removeEventListener('resize', measure)
  document.documentElement.style.removeProperty('--keyboard-inset')
  open = false
  inset = -1
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener)
  if (listeners.size === 1) attach()
  return () => {
    listeners.delete(listener)
    if (listeners.size === 0) detach()
  }
}

function getSnapshot(): boolean {
  return open
}

function getServerSnapshot(): boolean {
  return false
}

/** True while the soft keyboard is up (Mobile/Tablet hide the floating nav and the FAB). */
export function useSoftKeyboard(): boolean {
  return useSyncExternalStore(subscribe, getSnapshot, getServerSnapshot)
}
