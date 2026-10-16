import { Box, GalleryVerticalEnd, Images, SlidersHorizontal } from 'lucide-react'
import type { KeyboardEvent, ReactNode } from 'react'

import { isTerminal, selectActiveJob, useJobStore } from '../../store/jobStore'
import { isRunTerminal, useMeshTunerStore } from '../../store/meshTunerStore'
import { useReferenceStore } from '../../store/referenceStore'
import { useUiStore, type MobileTab } from '../../store/uiStore'
import { mobileTabId, mobileTabPanelId } from './mobileTabs'
import styles from './MobileTabBar.module.css'

const TABS: Array<{ id: MobileTab; label: string; icon: ReactNode }> = [
  { id: 'inputs', label: 'Inputs', icon: <Images size={20} /> },
  { id: 'settings', label: 'Settings', icon: <SlidersHorizontal size={20} /> },
  { id: 'viewer', label: 'Viewer', icon: <Box size={20} /> },
  { id: 'history', label: 'History', icon: <GalleryVerticalEnd size={20} /> },
]

const TAB_KEYS: Record<string, (index: number) => number> = {
  ArrowRight: (index) => (index + 1) % TABS.length,
  ArrowLeft: (index) => (index - 1 + TABS.length) % TABS.length,
  Home: () => 0,
  End: () => TABS.length - 1,
}

/** Bottom navigation for the single-column mobile layout (WAI-ARIA tabs: roving focus, arrow keys). */
export function MobileTabBar() {
  const active = useUiStore((s) => s.mobileTab)
  const setTab = useUiStore((s) => s.setMobileTab)
  const imageCount = useReferenceStore((s) => s.set?.images.length ?? 0)
  const activeJob = useJobStore(selectActiveJob)
  const tuning = useMeshTunerStore((s) => Object.values(s.runs).some((run) => !isRunTerminal(run)))
  const running = (!!activeJob && !isTerminal(activeJob)) || tuning

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const move = TAB_KEYS[event.key]
    if (!move) return
    event.preventDefault()
    const next = TABS[move(Math.max(0, TABS.findIndex((tab) => tab.id === active)))]
    setTab(next.id)
    document.getElementById(mobileTabId(next.id))?.focus()
  }

  return (
    <nav className={styles.bar} aria-label="Sections">
      <div role="tablist" aria-label="Sections" className={styles.list} onKeyDown={onKeyDown}>
        {TABS.map((tab) => {
          const selected = tab.id === active
          const badge = tab.id === 'inputs' && imageCount > 0 ? String(imageCount) : null
          return (
            <button
              key={tab.id}
              id={mobileTabId(tab.id)}
              type="button"
              role="tab"
              aria-selected={selected}
              aria-controls={mobileTabPanelId(tab.id)}
              tabIndex={selected ? 0 : -1}
              className={`${styles.tab} ${selected ? styles.selected : ''}`}
              onClick={() => setTab(tab.id)}
            >
              <span className={styles.icon}>
                {tab.icon}
                {badge && (
                  <span className={styles.badge}>
                    {badge}
                    <span className="visually-hidden"> images</span>
                  </span>
                )}
                {tab.id === 'viewer' && running && (
                  <span className={styles.pulse}>
                    <span className="visually-hidden">{tuning ? 'auto tune running' : 'generation running'}</span>
                  </span>
                )}
              </span>
              <span className={styles.label}>{tab.label}</span>
            </button>
          )
        })}
      </div>
    </nav>
  )
}
