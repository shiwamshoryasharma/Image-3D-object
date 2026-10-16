import { X } from 'lucide-react'
import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type KeyboardEvent,
  type MouseEvent,
  type PointerEvent,
  type ReactNode,
} from 'react'

import { prefersReducedMotion } from '../../lib/motion'
import { IconButton } from './Button'
import { registerModal } from './modalStack'
import styles from './Sheet.module.css'

/** Same union as `navigationStore.SheetSnap` (stores never import components, UI_DESIGN §1.4). */
export type SheetSnap = 'peek' | 'half' | 'full'

export interface SheetProps {
  /** Element id (target of the opener's `aria-controls`). */
  id: string
  title: ReactNode
  open: boolean
  onClose: () => void
  /**
   * Modal (default): native `<dialog>.showModal()` with an inert background, Escape, backdrop light-dismiss and
   * focus return. Standard: a `region` beside the page, without a focus trap.
   */
  modal?: boolean
  side?: 'bottom' | 'right'
  /** Snap heights a bottom sheet may take (any order). */
  snaps?: readonly SheetSnap[]
  /** Controlled snap; uncontrolled sheets start at `half` (or their largest snap). */
  snap?: SheetSnap
  onSnapChange?: (snap: SheetSnap) => void
  headerActions?: ReactNode
  /** Sticky footer; it stays above the soft keyboard (`--keyboard-inset`). */
  footer?: ReactNode
  children: ReactNode
}

interface DragState {
  pointerId: number
  startY: number
  startHeight: number
  maxGrow: number
  offset: number
  moved: boolean
  samples: ReadonlyArray<{ time: number; y: number }>
}

const SNAP_ORDER: readonly SheetSnap[] = ['peek', 'half', 'full']
const SNAP_TEXT: Record<SheetSnap, string> = { peek: 'peek height', half: 'half height', full: 'full height' }
const DEFAULT_SNAPS: readonly SheetSnap[] = ['half', 'full']
const FULL_ONLY: readonly SheetSnap[] = ['full']
/** Movement (px) before a press on the header becomes a drag (a shorter press on the handle is a tap). */
const DRAG_SLOP = 4
/** Release velocity is projected this far ahead to pick the snap (`y + v·200 ms`). */
const PROJECTION_MS = 200
/** A modal sheet whose projected height falls below this share of its height closes. */
const CLOSE_RATIO = 0.4
/** Keeps the content mounted while the exit transition (--duration-slow = 320 ms) plays. */
const EXIT_MS = 400
/** Gap the `full` snap leaves at the top (Sheet.module.css uses the same 24px). */
const FULL_TOP_GAP = 24

/** Upward drags follow the finger up to the tallest snap, then resist. */
function resist(delta: number, maxGrow: number): number {
  if (delta >= 0) return delta
  const grow = -delta
  return -(grow <= maxGrow ? grow : maxGrow + (grow - maxGrow) * 0.2)
}

/** px per ms, positive downwards, from the last pointer samples. */
function releaseVelocity(samples: DragState['samples']): number {
  if (samples.length < 2) return 0
  const first = samples[0]
  const last = samples[samples.length - 1]
  const elapsed = last.time - first.time
  return elapsed > 0 ? (last.y - first.y) / elapsed : 0
}

function snapHeights(panel: HTMLElement): Record<SheetSnap, number> {
  const container = panel.parentElement?.clientHeight ?? window.innerHeight
  const peek = Number.parseFloat(getComputedStyle(panel).getPropertyValue('--sheet-peek')) || 96
  return { peek, half: container * 0.5, full: container - FULL_TOP_GAP }
}

function nearestSnap(height: number, panel: HTMLElement, allowed: readonly SheetSnap[]): SheetSnap {
  const heights = snapHeights(panel)
  return allowed.reduce((best, snap) => (Math.abs(heights[snap] - height) < Math.abs(heights[best] - height) ? snap : best))
}

/** Returns the panel to the resting position of its snap (animated by the CSS transform transition). */
function settle(panel: HTMLElement): void {
  panel.style.transition = ''
  panel.style.transform = ''
}

/**
 * Bottom or side sheet for the touch shells (UI_DESIGN §4.1). Bottom sheets snap between peek / half / full: drag
 * the handle or header (Pointer Events; the body only scrolls), tap the handle or press Enter/Space on it to cycle,
 * ArrowUp/ArrowDown to step. During a drag the transform is written to the panel element directly (no React state
 * per frame); snap changes animate with a FLIP transform. The modal `<dialog>` itself is full-viewport and never
 * transformed, so toasts portaled into it (modalStack) still lay out against the viewport.
 */
export function Sheet({
  id,
  title,
  open,
  onClose,
  modal = true,
  side = 'bottom',
  snaps = DEFAULT_SNAPS,
  snap,
  onSnapChange,
  headerActions,
  footer,
  children,
}: SheetProps) {
  const titleId = useId()
  const dialogRef = useRef<HTMLDialogElement>(null)
  const panelRef = useRef<HTMLDivElement>(null)
  const handleRef = useRef<HTMLButtonElement>(null)
  const dragRef = useRef<DragState | null>(null)
  const flipRef = useRef<{ target: SheetSnap; fromHeight: number } | null>(null)
  const suppressClickRef = useRef(false)
  const backdropPressRef = useRef(false)

  const bottom = side === 'bottom'
  const listed = SNAP_ORDER.filter((value) => snaps.includes(value))
  const allowed = listed.length > 0 ? listed : FULL_ONLY
  const fallbackSnap = allowed.includes('half') ? 'half' : allowed[allowed.length - 1]
  const [ownSnap, setOwnSnap] = useState<SheetSnap>(fallbackSnap)
  const requestedSnap = snap ?? ownSnap
  const currentSnap = allowed.includes(requestedSnap) ? requestedSnap : fallbackSnap

  // Keep the content mounted while the exit transition plays (state derived from the previous `open`).
  const [shownOpen, setShownOpen] = useState(open)
  const [exiting, setExiting] = useState(false)
  if (open !== shownOpen) {
    setShownOpen(open)
    setExiting(!open)
  }
  const rendered = open || exiting

  useEffect(() => {
    if (!exiting) return
    const timer = window.setTimeout(() => setExiting(false), prefersReducedMotion() ? 0 : EXIT_MS)
    return () => window.clearTimeout(timer)
  }, [exiting])

  useEffect(() => {
    const dialog = dialogRef.current
    if (!dialog) return
    if (open && !dialog.open) dialog.showModal()
    else if (!open && dialog.open) dialog.close()
  }, [open, modal])

  // After the showModal() effect, so the dialog is open when the Toaster portals into it.
  useEffect(() => {
    const dialog = dialogRef.current
    if (!open || !dialog) return
    return registerModal(dialog)
  }, [open, modal])

  // A standard sheet has no focus trap; if it closes while holding focus, focus goes back to the opener.
  useEffect(() => {
    if (modal || !open) return
    const opener = document.activeElement
    const panel = panelRef.current
    return () => {
      if (panel?.contains(document.activeElement) && opener instanceof HTMLElement && opener.isConnected) opener.focus()
    }
  }, [open, modal])

  // FLIP: a snap change requested from the handle or a drag starts from where the panel was and animates to its
  // new height with a transform only.
  useLayoutEffect(() => {
    const flip = flipRef.current
    const panel = panelRef.current
    if (!flip || flip.target !== currentSnap || !panel) return
    flipRef.current = null
    const height = panel.getBoundingClientRect().height
    panel.style.transition = 'none'
    panel.style.transform = `translateY(${height - flip.fromHeight}px)`
    panel.getBoundingClientRect() // commit the start position before the transition is re-enabled
    settle(panel)
  }, [currentSnap])

  const changeSnap = (next: SheetSnap, fromHeight: number) => {
    if (next === currentSnap) return
    flipRef.current = { target: next, fromHeight }
    if (snap === undefined) setOwnSnap(next)
    onSnapChange?.(next)
  }

  const stepSnap = (next: SheetSnap) => {
    const panel = panelRef.current
    if (panel) changeSnap(next, panel.getBoundingClientRect().height)
  }

  const onHandleClick = () => {
    if (suppressClickRef.current) return
    stepSnap(allowed[(allowed.indexOf(currentSnap) + 1) % allowed.length])
  }

  const onHandleKeyDown = (event: KeyboardEvent<HTMLButtonElement>) => {
    const index = allowed.indexOf(currentSnap)
    let next: SheetSnap | null = null
    if (event.key === 'ArrowUp') next = allowed[Math.min(index + 1, allowed.length - 1)]
    else if (event.key === 'ArrowDown') next = allowed[Math.max(index - 1, 0)]
    if (next === null) return
    event.preventDefault()
    stepSnap(next)
  }

  const onDragStart = (event: PointerEvent<HTMLElement>) => {
    const panel = panelRef.current
    if (!bottom || !panel || !event.isPrimary || event.button !== 0) return
    // Buttons in the header keep their click; only the handle and the bare header start a drag.
    const control = (event.target as Element).closest('button, a, input, select, textarea, [role="button"]')
    if (control && control !== handleRef.current) return
    const startHeight = panel.getBoundingClientRect().height
    dragRef.current = {
      pointerId: event.pointerId,
      startY: event.clientY,
      startHeight,
      maxGrow: Math.max(0, snapHeights(panel)[allowed[allowed.length - 1]] - startHeight),
      offset: 0,
      moved: false,
      samples: [{ time: event.timeStamp, y: event.clientY }],
    }
  }

  const onDragMove = (event: PointerEvent<HTMLElement>) => {
    const drag = dragRef.current
    const panel = panelRef.current
    if (!drag || !panel || event.pointerId !== drag.pointerId) return
    const delta = event.clientY - drag.startY
    if (!drag.moved) {
      if (Math.abs(delta) < DRAG_SLOP) return
      // Captured only once it is a drag, so a plain tap still clicks the handle.
      event.currentTarget.setPointerCapture(event.pointerId)
      panel.style.transition = 'none'
    }
    const offset = resist(delta, drag.maxGrow)
    dragRef.current = { ...drag, moved: true, offset, samples: [...drag.samples.slice(-3), { time: event.timeStamp, y: event.clientY }] }
    panel.style.transform = `translateY(${offset}px)`
  }

  const onDragEnd = (event: PointerEvent<HTMLElement>) => {
    const drag = dragRef.current
    const panel = panelRef.current
    if (!drag || event.pointerId !== drag.pointerId) return
    dragRef.current = null
    if (!drag.moved || !panel) return
    // The click that may follow this pointerup must not cycle the snap.
    suppressClickRef.current = true
    window.setTimeout(() => {
      suppressClickRef.current = false
    }, 0)
    if (event.type === 'pointercancel') {
      settle(panel)
      return
    }
    const visible = drag.startHeight - drag.offset
    const projected = visible - releaseVelocity(drag.samples) * PROJECTION_MS
    if (modal && projected < CLOSE_RATIO * drag.startHeight) {
      // The exit transition starts from the dragged position; if the owner keeps the sheet open, it settles back.
      onClose()
      window.requestAnimationFrame(() => {
        if (dialogRef.current?.open) settle(panel)
      })
      return
    }
    const next = nearestSnap(projected, panel, allowed)
    settle(panel)
    changeSnap(next, visible)
  }

  const onBackdropPointerDown = (event: PointerEvent<HTMLDialogElement>) => {
    backdropPressRef.current = event.target === event.currentTarget
  }

  const onBackdropClick = (event: MouseEvent<HTMLDialogElement>) => {
    // The <dialog> fills the viewport and the panel is inside it: a press and release on the dialog itself
    // happened outside the panel (light dismiss).
    const pressed = backdropPressRef.current
    backdropPressRef.current = false
    if (pressed && event.target === event.currentTarget) onClose()
  }

  const sheetPanel = rendered && (
    <div ref={panelRef} className={styles.panel} data-side={side} data-snap={bottom ? currentSnap : undefined} inert={!open}>
      <header
        className={`${styles.header} ${bottom ? styles.draggable : ''}`}
        onPointerDown={onDragStart}
        onPointerMove={onDragMove}
        onPointerUp={onDragEnd}
        onPointerCancel={onDragEnd}
      >
        {bottom && allowed.length > 1 && (
          <button
            ref={handleRef}
            type="button"
            className={styles.handle}
            aria-label={`Resize panel, currently ${SNAP_TEXT[currentSnap]}`}
            aria-expanded={currentSnap === allowed[allowed.length - 1]}
            onClick={onHandleClick}
            onKeyDown={onHandleKeyDown}
          >
            <span className={styles.pill} />
          </button>
        )}
        <div className={styles.titleRow}>
          <h2 id={titleId} className={styles.title}>
            {title}
          </h2>
          {headerActions && <div className={styles.actions}>{headerActions}</div>}
          <IconButton label="Close" icon={<X size={16} />} size="sm" onClick={onClose} />
        </div>
      </header>
      <div className={`${styles.body} ${footer ? styles.bodyWithFooter : ''}`}>{children}</div>
      {footer && <footer className={styles.footer}>{footer}</footer>}
    </div>
  )

  if (modal) {
    return (
      <dialog
        ref={dialogRef}
        id={id}
        className={styles.dialog}
        aria-labelledby={titleId}
        onCancel={(event) => {
          event.preventDefault()
          onClose()
        }}
        onClose={(event) => {
          // A close the browser forced (repeated Escape without user activation) is reported like a cancel.
          if (open && !event.currentTarget.open) onClose()
        }}
        onPointerDown={onBackdropPointerDown}
        onClick={onBackdropClick}
      >
        {sheetPanel}
      </dialog>
    )
  }

  return rendered ? (
    <section id={id} role="region" aria-labelledby={titleId} className={`${styles.region} ${open ? '' : styles.closing}`}>
      {sheetPanel}
    </section>
  ) : null
}
