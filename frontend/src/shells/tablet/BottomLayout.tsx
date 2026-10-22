import { useState } from 'react'

import type { ShellProps } from '../../app/shellRegistry'
import { FloatingNav } from '../../components/common/FloatingNav'
import { Sheet } from '../../components/common/Sheet'
import { GenerateAction } from '../../components/generation/GenerateAction'
import { BackendBanner } from '../../components/layout/BackendBanner'
import { ViewerPanel } from '../../components/viewer/ViewerPanel'
import { useReveal } from '../../hooks/useReveal'
import { useSoftKeyboard } from '../../hooks/useSoftKeyboard'
import { FEATURE_META } from '../../lib/featureMeta.ts'
import { useNavigationStore, type SheetSnap } from '../../store/navigationStore'
import { CompactTopBar } from '../shared/CompactTopBar'
import { useShellNav } from '../shared/navItems'
import { ProgressPill } from '../shared/ProgressPill'
import { SectionHost } from '../shared/SectionHost'
import { SheetHost } from '../shared/SheetHost'
import { applyBottomReveal, SECTION_SNAPS, TABLET_SHEET_ID, type PanelSection } from './tabletNav'
import styles from './TabletShell.module.css'

export interface BottomLayoutProps extends ShellProps {
  /** Snap of the section sheet; held by `TabletShell`, so it survives a flip to `rail` and back. */
  snap: SheetSnap
  onSnapChange: (snap: SheetSnap) => void
}

function closeSection(): void {
  useNavigationStore.getState().setSidePanelOpen(false)
}

/**
 * Tablet `bottom` posture (UI_DESIGN §4.5.2): `CompactTopBar`, the viewer on top (it ends above the section sheet's
 * peek height and the floating nav), the floating nav with the Generate FAB, and the selected section in one standard
 * sheet (`peek`/`half`/`full`; Create gets the sticky Generate footer). The transient sheets come from `SheetHost`.
 */
export function BottomLayout({ nextRetryAt, snap, onSnapChange }: BottomLayoutProps) {
  const keyboardOpen = useSoftKeyboard()
  const nav = useShellNav({ kind: 'panel', panelId: TABLET_SHEET_ID })
  useReveal((target) => applyBottomReveal(target, onSnapChange))
  const section = useNavigationStore((s) => s.section)
  const panelOpen = useNavigationStore((s) => s.sidePanelOpen)
  const open = panelOpen && section !== 'viewer'
  // The last section stays rendered while the sheet plays its exit transition (Viewer sets the section to `viewer`).
  const [shown, setShown] = useState<PanelSection>(section === 'viewer' ? 'create' : section)
  if (section !== 'viewer' && section !== shown) setShown(section)

  return (
    <>
      <CompactTopBar />
      <BackendBanner nextRetryAt={nextRetryAt} />
      <main id="main-content" tabIndex={-1} className={styles.bottomMain}>
        <ViewerPanel />
        <ProgressPill />
      </main>
      <FloatingNav
        label="Main"
        items={nav.items}
        value={nav.value}
        onSelect={nav.onSelect}
        center={<GenerateAction variant="fab" />}
        hidden={keyboardOpen}
      />
      <Sheet
        id={TABLET_SHEET_ID}
        title={FEATURE_META[shown].title}
        open={open}
        onClose={closeSection}
        modal={false}
        snaps={SECTION_SNAPS}
        snap={snap}
        onSnapChange={onSnapChange}
        footer={shown === 'create' ? <GenerateAction variant="bar" /> : undefined}
      >
        <SectionHost section={shown} active={open} />
      </Sheet>
      <SheetHost sections={false} />
    </>
  )
}
