import { X } from 'lucide-react'
import { useId } from 'react'

import type { ShellProps } from '../../app/shellRegistry'
import { IconButton } from '../../components/common/Button'
import { NavRail } from '../../components/common/NavRail'
import { GenerateAction } from '../../components/generation/GenerateAction'
import { BackendBanner } from '../../components/layout/BackendBanner'
import { ViewerPanel } from '../../components/viewer/ViewerPanel'
import { useReveal } from '../../hooks/useReveal'
import { FEATURE_META } from '../../lib/featureMeta.ts'
import { useNavigationStore } from '../../store/navigationStore'
import { AppSettingsButton } from '../shared/AppSettingsButton'
import { useShellNav } from '../shared/navItems'
import { ProgressPill } from '../shared/ProgressPill'
import { SectionHost } from '../shared/SectionHost'
import { SheetHost } from '../shared/SheetHost'
import { applyRailReveal, TABLET_PANEL_ID } from './tabletNav'
import styles from './TabletShell.module.css'

function closePanel(): void {
  useNavigationStore.getState().setSidePanelOpen(false)
  document.getElementById('main-content')?.focus({ preventScroll: true })
}

/**
 * Tablet `rail` posture (UI_DESIGN §4.5.2): floating left `NavRail` (sections, Generate, App settings), a side panel
 * with the selected section right of it, the viewer filling the rest. Viewer, or re-selecting the open item, collapses
 * the panel. Only the transient sheets (review, progress, blocked, status) remain, as right-hand side sheets.
 */
export function RailLayout({ nextRetryAt }: ShellProps) {
  const nav = useShellNav({ kind: 'panel', panelId: TABLET_PANEL_ID })
  useReveal(applyRailReveal)
  const section = useNavigationStore((s) => s.section)
  const panelOpen = useNavigationStore((s) => s.sidePanelOpen)
  const open = panelOpen && section !== 'viewer'
  const titleId = useId()
  const title = FEATURE_META[section].title

  return (
    <>
      <NavRail
        label="Main"
        items={nav.items}
        value={nav.value}
        onSelect={nav.onSelect}
        footer={
          <>
            <GenerateAction variant="rail" />
            <AppSettingsButton />
          </>
        }
      />
      <div className={styles.railBody}>
        <BackendBanner nextRetryAt={nextRetryAt} />
        <div className={styles.railRow}>
          <aside id={TABLET_PANEL_ID} className={styles.sidePanel} aria-labelledby={titleId} hidden={!open}>
            {open && (
              <>
                <div className={styles.panelHeader}>
                  <h2 id={titleId} className={styles.panelTitle}>
                    {title}
                  </h2>
                  <IconButton label={`Close ${title}`} icon={<X size={18} />} size="sm" onClick={closePanel} />
                </div>
                <div className={styles.panelBody}>
                  <SectionHost section={section} active={open} />
                </div>
              </>
            )}
          </aside>
          <main id="main-content" tabIndex={-1} className={styles.viewerMain}>
            <ViewerPanel />
            <ProgressPill />
          </main>
        </div>
      </div>
      <SheetHost side="right" sections={false} />
    </>
  )
}
