/**
 * Tablet navigation rules (UI_DESIGN §4.5.2). `rail` and `bottom` keep the selected section in
 * `navigationStore.section` + `sidePanelOpen` (the side panel, or the bottom section sheet); `compact` is
 * `PhoneLayout`, whose sections are `navigationStore.sheet`. `carryNavigation` hands the open section over when the
 * posture changes, so an orientation flip keeps it.
 */
import type { TabletPosture } from '../../lib/viewport.ts'
import { useNavigationStore, type RevealTarget, type SheetSnap, type ShellSection } from '../../store/navigationStore'
import { SECTION_SHEET, SHELL_SECTIONS } from '../shared/navItems'
import { openShellSheet, SECTION_SHEETS } from '../shared/sheets'

/** `aria-controls` targets of the section items: the rail's side panel and the bottom posture's section sheet. */
export const TABLET_PANEL_ID = 'tablet-section-panel'
export const TABLET_SHEET_ID = 'tablet-section-sheet'

/** Snaps of the bottom posture's standard section sheet (default `half`). */
export const SECTION_SNAPS: readonly SheetSnap[] = Object.freeze(['peek', 'half', 'full'])
export const DEFAULT_SECTION_SNAP: SheetSnap = 'half'

export type PanelSection = Exclude<ShellSection, 'viewer'>

function showSection(section: PanelSection): void {
  const nav = useNavigationStore.getState()
  nav.setSection(section)
  nav.setSidePanelOpen(true)
}

/** The reveal targets both panel postures map alike (§4.4 table, sections instead of sheets); false for `viewer`. */
function revealSection(target: RevealTarget): boolean {
  const nav = useNavigationStore.getState()
  switch (target) {
    case 'create':
    case 'prompt':
      nav.setCreateTab('input')
      showSection('create')
      return true
    case 'settings':
      nav.setCreateTab('settings')
      showSection('create')
      return true
    case 'review':
      openShellSheet('review')
      return true
    case 'progress':
      openShellSheet('progress')
      return true
    case 'parts':
    case 'export':
      showSection('parts')
      return true
    case 'history':
      showSection('library')
      return true
    case 'viewer':
      return false
  }
}

/** Rail: set the section and open the panel; `viewer` keeps the panel open and only focuses the viewer. */
export function applyRailReveal(target: RevealTarget): void {
  if (revealSection(target)) return
  useNavigationStore.getState().openSheet(null)
  document.getElementById('main-content')?.focus({ preventScroll: true })
}

/** Bottom: as Mobile, with the section sheet (`parts`/`export` at `half`); `viewer` closes every sheet. */
export function applyBottomReveal(target: RevealTarget, setSnap: (snap: SheetSnap) => void): void {
  if (target === 'parts' || target === 'export') setSnap('half')
  if (revealSection(target)) return
  const nav = useNavigationStore.getState()
  nav.openSheet(null)
  nav.setSidePanelOpen(false)
}

/**
 * Moves the open section between the panel state (rail, bottom) and PhoneLayout's section sheets (compact). Called
 * with the posture the shell now shows (also once on mount, which adopts a section sheet left open by Mobile mode).
 * Transient sheets (review, progress, blocked, status) stay as they are.
 */
export function carryNavigation(posture: TabletPosture): void {
  const nav = useNavigationStore.getState()
  if (posture === 'compact') {
    if (!nav.sidePanelOpen) return
    const sheet = SECTION_SHEET[nav.section]
    nav.setSidePanelOpen(false)
    if (sheet !== null && (nav.sheet === null || SECTION_SHEETS.has(nav.sheet))) openShellSheet(sheet)
    return
  }
  if (nav.sheet === null || !SECTION_SHEETS.has(nav.sheet)) return
  const section = SHELL_SECTIONS.find((id) => SECTION_SHEET[id] === nav.sheet)
  nav.openSheet(null)
  if (section !== undefined && section !== 'viewer') showSection(section)
}
