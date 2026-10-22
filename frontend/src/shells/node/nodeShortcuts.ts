/**
 * Canvas/list-scoped shortcuts (UI_DESIGN §4.3.13), ignored inside text inputs: `/` or Ctrl+K palette; Delete /
 * Backspace remove the selected node or disconnect the selected edge (React Flow's own `deleteKeyCode` is null);
 * Ctrl+B bypass; Alt+C collapse; Ctrl+Z / Ctrl+Y (Ctrl+Shift+Z) undo/redo; `.` fit view; Enter opens the Inspector.
 * Run stays the global Ctrl/Cmd+Enter (one listener, UI6).
 */
import type { KeyboardEvent as ReactKeyboardEvent } from 'react'

import { registryNow, useWorkflowStore } from '../../store/workflowStore'

export interface NodeShortcutActions {
  openPalette: () => void
  fitView: () => void
  openInspector: () => void
}

function isTextTarget(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  if (target.isContentEditable) return true
  const tag = target.tagName
  if (tag === 'TEXTAREA' || tag === 'SELECT') return true
  if (tag !== 'INPUT') return false
  const type = (target as HTMLInputElement).type
  return !['checkbox', 'radio', 'button', 'range', 'submit'].includes(type)
}

/** Handles one keydown; returns true (and prevents the default) when it consumed the key. */
export function handleNodeShortcut(event: ReactKeyboardEvent, actions: NodeShortcutActions): boolean {
  if (event.defaultPrevented || isTextTarget(event.target)) return false
  const store = useWorkflowStore.getState()
  const { selection } = store
  const mod = event.ctrlKey || event.metaKey
  const key = event.key.toLowerCase()
  let handled = true

  if (mod && key === 'enter') return false // the global Generate shortcut
  if ((key === '/' && !mod) || (mod && key === 'k')) actions.openPalette()
  else if ((key === 'delete' || key === 'backspace') && !mod && selection) {
    if (selection.kind === 'node') store.removeNodes([selection.id])
    else store.disconnect(selection.id)
  } else if (mod && key === 'b' && selection?.kind === 'node') {
    const node = store.doc.graph.nodes.find((n) => n.id === selection.id)
    const def = node ? registryNow().get(node.type) : undefined
    if (node && def && def.bypass !== null && !def.locked) store.setMode(node.id, node.mode === 'active' ? 'bypass' : 'active')
  } else if (event.altKey && key === 'c' && selection?.kind === 'node') {
    store.setCollapsed(selection.id, !store.doc.layout.collapsed.includes(selection.id))
  } else if (mod && key === 'z' && !event.shiftKey) store.undo()
  else if (mod && (key === 'y' || (key === 'z' && event.shiftKey))) store.redo()
  else if (key === '.' && !mod) actions.fitView()
  else if (key === 'enter' && !mod && selection) {
    // Enter on a focused button keeps its own meaning.
    if (event.target instanceof HTMLButtonElement) return false
    actions.openInspector()
  } else handled = false

  if (handled) event.preventDefault()
  return handled
}
