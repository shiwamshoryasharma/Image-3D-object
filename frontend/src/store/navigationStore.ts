/**
 * Cross-shell navigation state (UI_DESIGN §3.4; not persisted). Every shell maps `reveal` requests onto its own
 * navigation (`hooks/useReveal.ts`). `pipelineBlock` and `runOverride` are written ONLY by the Node shell (UI10).
 */
import { create } from 'zustand'

import type { UiMode } from '../lib/uiModes.ts'

export const REVEAL_TARGETS = Object.freeze([
  'create',
  'prompt',
  'review',
  'settings',
  'viewer',
  'progress',
  'parts',
  'export',
  'history',
] as const)
export type RevealTarget = (typeof REVEAL_TARGETS)[number]
export type SheetId = 'create' | 'parts' | 'library' | 'progress' | 'blocked' | 'status' | 'review'
export type SheetSnap = 'peek' | 'half' | 'full'
export type ShellSection = 'create' | 'viewer' | 'parts' | 'library'
export type AppSettingsSection = 'interface' | 'node' | 'features' | 'about'
export type CreateTab = 'input' | 'settings'

/** Modes whose shell is included in this build; F6 replaces it with `app/shellRegistry.READY_MODES` at boot. */
const DEFAULT_READY_MODES: ReadonlySet<UiMode> = Object.freeze(new Set<UiMode>(['default']))

interface NavigationState {
  /** Monotonic `seq`: the same target can be requested again. */
  reveal: { target: RevealTarget; seq: number } | null
  appSettingsOpen: boolean
  appSettingsSection: AppSettingsSection
  /** From `?ui=` or "Use Default UI for this session"; never persisted. */
  sessionUiOverride: UiMode | null
  /** Modes whose shell has landed (`SHELL_READY`); read by `useResolvedUiMode` (hooks cannot import `app/`). */
  readyModes: ReadonlySet<UiMode>
  /** Set ONLY by the Node shell (UI10). */
  pipelineBlock: string | null
  /** Set ONLY by the Node shell: Generate runs its graph instead of the shared plan. */
  runOverride: (() => Promise<boolean>) | null
  sheet: SheetId | null
  sheetSnap: SheetSnap
  createTab: CreateTab
  section: ShellSection
  sidePanelOpen: boolean

  requestReveal: (target: RevealTarget) => void
  setAppSettingsOpen: (open: boolean, section?: AppSettingsSection) => void
  setSessionUiOverride: (mode: UiMode | null) => void
  setReadyModes: (modes: ReadonlySet<UiMode>) => void
  setPipelineBlock: (reason: string | null) => void
  setRunOverride: (fn: (() => Promise<boolean>) | null) => void
  openSheet: (id: SheetId | null, snap?: SheetSnap) => void
  setSheetSnap: (snap: SheetSnap) => void
  setCreateTab: (tab: CreateTab) => void
  setSection: (section: ShellSection) => void
  setSidePanelOpen: (open: boolean) => void
}

export const useNavigationStore = create<NavigationState>()((set) => ({
  reveal: null,
  appSettingsOpen: false,
  appSettingsSection: 'interface',
  sessionUiOverride: null,
  readyModes: DEFAULT_READY_MODES,
  pipelineBlock: null,
  runOverride: null,
  sheet: null,
  sheetSnap: 'half',
  createTab: 'input',
  section: 'viewer',
  sidePanelOpen: false,

  requestReveal: (target) => set((state) => ({ reveal: { target, seq: (state.reveal?.seq ?? 0) + 1 } })),
  setAppSettingsOpen: (open, section) =>
    set((state) => ({ appSettingsOpen: open, appSettingsSection: section ?? state.appSettingsSection })),
  setSessionUiOverride: (mode) => set((state) => (state.sessionUiOverride === mode ? state : { sessionUiOverride: mode })),
  setReadyModes: (modes) =>
    set(() => ({ readyModes: Object.freeze(new Set<UiMode>(['default', ...modes])) as ReadonlySet<UiMode> })),
  setPipelineBlock: (reason) => set((state) => (state.pipelineBlock === reason ? state : { pipelineBlock: reason })),
  setRunOverride: (fn) => set((state) => (state.runOverride === fn ? state : { runOverride: fn })),
  openSheet: (id, snap) => set((state) => ({ sheet: id, sheetSnap: snap ?? state.sheetSnap })),
  setSheetSnap: (snap) => set((state) => (state.sheetSnap === snap ? state : { sheetSnap: snap })),
  setCreateTab: (tab) => set((state) => (state.createTab === tab ? state : { createTab: tab })),
  setSection: (section) => set((state) => (state.section === section ? state : { section })),
  setSidePanelOpen: (open) => set((state) => (state.sidePanelOpen === open ? state : { sidePanelOpen: open })),
}))
