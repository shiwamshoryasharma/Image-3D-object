/**
 * The four shell destinations (UI_DESIGN §4.4, §4.5.2): Create, Viewer, Parts, Library, with their badges and the
 * selection rules of both navigation styles.
 * - `sheets` (Mobile, Tablet bottom/compact): Create/Parts/Library open their sheet (`aria-expanded` +
 *   `aria-controls`); Viewer closes any sheet and is `aria-current="page"` while no sheet is open.
 * - `panel` (Tablet rail, Mobile landscape rail with a side panel): the item selects `navigationStore.section` and
 *   opens the side panel; Viewer, or re-selecting the active item, collapses it.
 * Badges: Create = reference image count ("P" when the effective source is the prompt); Parts = part count of the
 * viewed model; Library = a dot while a run is active.
 */
import { createElement } from 'react'

import type { NavItem } from '../../components/common/FloatingNav'
import { useActiveRun } from '../../hooks/useActiveRun'
import { useFeatures } from '../../hooks/useFeatures'
import { FEATURE_META } from '../../lib/featureMeta.ts'
import { effectiveSource } from '../../lib/generatePlan.ts'
import { useInputStore } from '../../store/inputStore'
import { useJobStore } from '../../store/jobStore'
import { useNavigationStore, type SheetId, type ShellSection } from '../../store/navigationStore'
import { selectLatestSet, usePartStore } from '../../store/partStore'
import { useReferenceStore } from '../../store/referenceStore'
import { FeatureIcon } from './FeatureIcon'
import { openShellSheet, sheetElementId } from './sheets'

export const SHELL_SECTIONS: readonly ShellSection[] = Object.freeze(['create', 'viewer', 'parts', 'library'])

/** The sheet a section opens in the `sheets` style (Viewer has none). */
export const SECTION_SHEET: Readonly<Record<ShellSection, SheetId | null>> = Object.freeze({
  create: 'create',
  viewer: null,
  parts: 'parts',
  library: 'library',
})

export type ShellNavStyle = { kind: 'sheets' } | { kind: 'panel'; panelId: string }

export interface ShellNav {
  items: NavItem[]
  /** The highlighted item (`FloatingNav` / `NavRail` `value`). */
  value: ShellSection
  onSelect: (id: string) => void
}

const isSection = (id: string): id is ShellSection => (SHELL_SECTIONS as readonly string[]).includes(id)

/** Pure selection rule of the `panel` style (UI_DESIGN §4.5.2). */
export function selectPanelSection(id: ShellSection): void {
  const nav = useNavigationStore.getState()
  if (id === 'viewer' || (nav.sidePanelOpen && nav.section === id)) {
    nav.setSidePanelOpen(false)
    if (id === 'viewer') nav.setSection('viewer')
    return
  }
  nav.setSection(id)
  nav.setSidePanelOpen(true)
}

/** Selection rule of the `sheets` style: Viewer closes; an item opens its sheet (re-selecting the open one closes it). */
export function selectSheetSection(id: ShellSection): void {
  const sheet = SECTION_SHEET[id]
  if (sheet === null || useNavigationStore.getState().sheet === sheet) openShellSheet(null)
  else openShellSheet(sheet)
}

function useBadges(): Readonly<Record<ShellSection, string | null>> {
  const features = useFeatures()
  const source = useInputStore((s) => s.source)
  const imageCount = useReferenceStore((s) => s.set?.images.length ?? 0)
  const viewedJobId = useJobStore((s) => s.viewedJobId)
  const partCount = usePartStore((s) => selectLatestSet(s, viewedJobId)?.set?.counts.parts ?? 0)
  const running = useActiveRun() !== null
  const prompt = effectiveSource(source, features) === 'prompt'
  return {
    create: prompt ? 'P' : imageCount > 0 ? String(imageCount) : null,
    viewer: null,
    parts: partCount > 0 ? String(partCount) : null,
    library: running ? '' : null,
  }
}

export function useShellNav(style: ShellNavStyle): ShellNav {
  const badges = useBadges()
  const sheet = useNavigationStore((s) => s.sheet)
  const section = useNavigationStore((s) => s.section)
  const panelOpen = useNavigationStore((s) => s.sidePanelOpen)

  const panel = style.kind === 'panel'
  const panelId = style.kind === 'panel' ? style.panelId : null
  const openSection: ShellSection = panel
    ? panelOpen
      ? section
      : 'viewer'
    : (SHELL_SECTIONS.find((id) => SECTION_SHEET[id] === sheet) ?? 'viewer')

  const items: NavItem[] = SHELL_SECTIONS.map((id) => {
    const viewer = id === 'viewer'
    return {
      id,
      label: FEATURE_META[id].shortTitle,
      icon: createElement(FeatureIcon, { id }),
      badge: badges[id],
      controls: viewer ? undefined : (panelId ?? sheetElementId(SECTION_SHEET[id] as SheetId)),
      expanded: viewer ? undefined : openSection === id,
      current: viewer ? (panel ? openSection === 'viewer' : sheet === null) : undefined,
    }
  })

  const onSelect = (id: string) => {
    if (!isSection(id)) return
    if (panel) selectPanelSection(id)
    else selectSheetSection(id)
  }

  return { items, value: openSection, onSelect }
}
