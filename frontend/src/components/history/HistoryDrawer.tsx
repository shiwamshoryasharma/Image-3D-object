import { GalleryVerticalEnd, RefreshCw, X } from 'lucide-react'
import { useEffect, useRef } from 'react'

import { useJobStore } from '../../store/jobStore'
import { useUiStore } from '../../store/uiStore'
import { IconButton } from '../common/Button'
import { getTopModal } from '../common/modalStack'
import { HistoryList } from './HistoryList'
import styles from './HistoryDrawer.module.css'

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])'

/** The page behind the drawer: the app header and `#main-content` (P16), unless something else made them inert. */
function pageBehind(): HTMLElement[] {
  const main = document.getElementById('main-content')
  const header = main?.parentElement?.querySelector<HTMLElement>(':scope > header') ?? null
  return [header, main].filter((el): el is HTMLElement => el !== null && !el.inert)
}

/** Keeps Tab / Shift+Tab inside the open drawer (P16). */
function trapTab(event: KeyboardEvent, panel: HTMLElement): void {
  const items = [...panel.querySelectorAll<HTMLElement>(FOCUSABLE)].filter((el) => el.getClientRects().length > 0)
  const active = document.activeElement
  if (items.length === 0) {
    event.preventDefault()
    panel.focus()
    return
  }
  const first = items[0]
  const last = items[items.length - 1]
  if (!(active instanceof Node) || !panel.contains(active)) {
    event.preventDefault()
    first.focus()
  } else if (event.shiftKey && (active === first || active === panel)) {
    event.preventDefault()
    last.focus()
  } else if (!event.shiftKey && active === last) {
    event.preventDefault()
    first.focus()
  }
}

/**
 * Slide-in history drawer (tablet/desktop), modal while open (P16): the header and `#main-content` are inert, Tab
 * stays inside, Escape or the backdrop closes it, and focus returns to the opener.
 */
export function HistoryDrawer() {
  const open = useUiStore((s) => s.historyOpen)
  const setOpen = useUiStore((s) => s.setHistoryOpen)
  const loadHistory = useJobStore((s) => s.loadHistory)
  const loading = useJobStore((s) => s.historyStatus === 'loading')
  const panelRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (!open) return
    void useJobStore.getState().loadHistory()
    const panel = panelRef.current
    const previous = document.activeElement as HTMLElement | null
    const behind = pageBehind()
    for (const el of behind) el.inert = true
    panel?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      // A modal dialog opened on top (App settings via Ctrl+,) handles its own keys.
      if (getTopModal()) return
      if (event.key === 'Escape') setOpen(false)
      else if (event.key === 'Tab' && panel) trapTab(event, panel)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
      for (const el of behind) el.inert = false
      previous?.focus?.()
    }
  }, [open, setOpen])

  return (
    <>
      <div className={`${styles.backdrop} ${open ? styles.backdropVisible : ''}`} onClick={() => setOpen(false)} aria-hidden />
      <aside
        id="history-drawer"
        ref={panelRef}
        className={`${styles.drawer} ${open ? styles.open : ''}`}
        aria-label="Job history"
        aria-hidden={!open}
        inert={!open}
        tabIndex={-1}
      >
        <header className={styles.header}>
          <GalleryVerticalEnd size={16} aria-hidden className={styles.icon} />
          <h2 className={styles.title}>History</h2>
          <IconButton
            label="Refresh history"
            icon={<RefreshCw size={15} className={loading ? styles.spin : undefined} />}
            size="sm"
            onClick={() => void loadHistory()}
          />
          <IconButton label="Close history" icon={<X size={16} />} size="sm" onClick={() => setOpen(false)} />
        </header>
        <div className={styles.body}>{open && <HistoryList />}</div>
      </aside>
    </>
  )
}
