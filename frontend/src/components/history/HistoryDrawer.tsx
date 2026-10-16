import { GalleryVerticalEnd, RefreshCw, X } from 'lucide-react'
import { useEffect, useRef } from 'react'

import { useJobStore } from '../../store/jobStore'
import { useUiStore } from '../../store/uiStore'
import { IconButton } from '../common/Button'
import { HistoryList } from './HistoryList'
import styles from './HistoryDrawer.module.css'

/** Slide-in history drawer (tablet/desktop). Escape or the backdrop closes it; focus moves into it on open. */
export function HistoryDrawer() {
  const open = useUiStore((s) => s.historyOpen)
  const setOpen = useUiStore((s) => s.setHistoryOpen)
  const loadHistory = useJobStore((s) => s.loadHistory)
  const loading = useJobStore((s) => s.historyStatus === 'loading')
  const panelRef = useRef<HTMLElement>(null)

  useEffect(() => {
    if (!open) return
    void useJobStore.getState().loadHistory()
    const previous = document.activeElement as HTMLElement | null
    panelRef.current?.focus()
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') setOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => {
      document.removeEventListener('keydown', onKeyDown)
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
