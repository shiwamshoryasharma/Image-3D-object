/**
 * Sheet table of the Mobile / Tablet (bottom, compact) shells (UI_DESIGN §4.4): one row per `navigationStore.SheetId`
 * (a `Record`, so a new id fails the type check until it gets a row), the element ids the openers point
 * `aria-controls` at, and the sheet-based reveal mapping shared by Mobile (both postures) and Tablet compact.
 */
import { FEATURE_META, type FeatureId } from '../../lib/featureMeta.ts'
import { useNavigationStore, type RevealTarget, type SheetId, type SheetSnap } from '../../store/navigationStore'

export interface SheetSpec {
  /** Modal `<dialog>` (inert background) or a standard region beside the viewer. */
  readonly modal: boolean
  readonly snaps: readonly SheetSnap[]
  readonly defaultSnap: SheetSnap
  readonly title: string
}

const title = (id: FeatureId) => FEATURE_META[id].title

export const SHEET_SPECS: Readonly<Record<SheetId, SheetSpec>> = Object.freeze({
  create: { modal: true, snaps: ['half', 'full'], defaultSnap: 'full', title: title('create') },
  review: { modal: true, snaps: ['half', 'full'], defaultSnap: 'half', title: 'Review images' },
  parts: { modal: false, snaps: ['peek', 'half', 'full'], defaultSnap: 'half', title: title('parts') },
  library: { modal: true, snaps: ['full'], defaultSnap: 'full', title: title('library') },
  progress: { modal: true, snaps: ['half'], defaultSnap: 'half', title: 'Generation progress' },
  blocked: { modal: true, snaps: ['half'], defaultSnap: 'half', title: 'Generate is unavailable' },
  status: { modal: true, snaps: ['half'], defaultSnap: 'half', title: 'System status' },
})

/** Every sheet id, in render order (the `SheetHost` mount order). */
export const SHEET_IDS = Object.freeze(Object.keys(SHEET_SPECS) as SheetId[])

/** The section sheets; the Tablet rail shows these in its side panel instead (`SheetHost sections={false}`). */
export const SECTION_SHEETS: ReadonlySet<SheetId> = Object.freeze(new Set<SheetId>(['create', 'parts', 'library']))

/** DOM id of a sheet: the target of its opener's `aria-controls`. */
export const sheetElementId = (id: SheetId): string => `shell-sheet-${id}`

/** The stored snap when this sheet allows it, else the sheet's default (e.g. `blocked` after a `full` Create). */
export function resolveSheetSnap(id: SheetId, snap: SheetSnap): SheetSnap {
  const spec = SHEET_SPECS[id]
  return spec.snaps.includes(snap) ? snap : spec.defaultSnap
}

/** Open a sheet at its default snap (or `snap`); `null` closes the open sheet. */
export function openShellSheet(id: SheetId | null, snap?: SheetSnap): void {
  useNavigationStore.getState().openSheet(id, id === null ? undefined : (snap ?? SHEET_SPECS[id].defaultSnap))
}

/** Mobile reveal table (UI_DESIGN §4.4); also Tablet `bottom`/`compact`. */
export function applySheetReveal(target: RevealTarget): void {
  const nav = useNavigationStore.getState()
  switch (target) {
    case 'viewer':
      nav.openSheet(null)
      return
    case 'create':
    case 'prompt':
      nav.setCreateTab('input')
      openShellSheet('create')
      return
    case 'settings':
      nav.setCreateTab('settings')
      openShellSheet('create')
      return
    case 'review':
      openShellSheet('review')
      return
    case 'progress':
      openShellSheet('progress')
      return
    case 'parts':
    case 'export':
      openShellSheet('parts', 'half')
      return
    case 'history':
      openShellSheet('library')
      return
  }
}
