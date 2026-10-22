import { useEffect } from 'react'

import { GenerateShortcut } from '../components/generation/GenerateAction'
import { useNavigationStore } from '../store/navigationStore'

const NON_TEXT_INPUTS = new Set(['checkbox', 'radio', 'range', 'button', 'submit', 'reset', 'file', 'color', 'image'])

function isTextEntry(target: EventTarget | null): boolean {
  if (target instanceof HTMLTextAreaElement || target instanceof HTMLSelectElement) return true
  if (target instanceof HTMLInputElement) return !NON_TEXT_INPUTS.has(target.type)
  return target instanceof HTMLElement && target.isContentEditable
}

/**
 * The single owner of the global shortcuts (UI_DESIGN §1.6, UI6), mounted once in `GlobalOverlays`:
 * Ctrl/Cmd+Enter generates in every mode (`GenerateShortcut`, the one listener for it), Ctrl/Cmd+`,` opens App
 * settings, `?` opens its keyboard-shortcut list.
 */
export function GlobalShortcuts() {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.repeat || event.altKey) return
      const mod = event.ctrlKey || event.metaKey
      if (event.key === ',' && mod && !event.shiftKey) {
        event.preventDefault()
        useNavigationStore.getState().setAppSettingsOpen(true, 'interface')
      } else if (event.key === '?' && !mod && !isTextEntry(event.target)) {
        event.preventDefault()
        useNavigationStore.getState().setAppSettingsOpen(true, 'about')
      }
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])
  return <GenerateShortcut />
}
