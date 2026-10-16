/**
 * Registry of the open modal `<dialog>`s, topmost last (UI_DESIGN §4.1).
 *
 * A modal dialog makes everything outside it inert and removes it from the accessibility tree, so feedback that is
 * raised while one is open (toasts, announcer messages) must render inside the topmost modal. `Dialog` and modal
 * `Sheet` register themselves while open; `Toaster` and `app/Announcer` read `getTopModal()` through
 * `useSyncExternalStore(subscribeModalStack, getTopModal)`.
 *
 * Import-free on purpose: stores never import it (§1.4) and the Node unit tests load it directly.
 */

type Listener = () => void

let stack: readonly HTMLDialogElement[] = []
const listeners = new Set<Listener>()

function emit(): void {
  for (const listener of [...listeners]) listener()
}

/**
 * Pushes `dialog` as the topmost modal. The returned function removes this registration (only once, wherever it
 * sits in the stack, so dialogs may close in any order).
 */
export function registerModal(dialog: HTMLDialogElement): () => void {
  stack = [...stack, dialog]
  emit()
  let registered = true
  return () => {
    if (!registered) return
    registered = false
    const index = stack.lastIndexOf(dialog)
    if (index < 0) return
    stack = [...stack.slice(0, index), ...stack.slice(index + 1)]
    emit()
  }
}

/** The topmost open modal, or `null` (the snapshot of the external store; stable between changes). */
export function getTopModal(): HTMLDialogElement | null {
  return stack.length > 0 ? stack[stack.length - 1] : null
}

/** Subscribes to stack changes (`useSyncExternalStore` contract); returns the unsubscribe function. */
export function subscribeModalStack(listener: Listener): () => void {
  listeners.add(listener)
  return () => {
    listeners.delete(listener)
  }
}
