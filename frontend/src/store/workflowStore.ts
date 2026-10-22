/**
 * Node UI workflow document (UI_DESIGN §4.3.10; Node chunk only: imported by `shells/node/**` alone).
 *
 * - `doc` is persisted as `img3d.workflow` v1 = `{doc}` (built-in nodes carry no values: no ids, prompts or tokens).
 *   A newer or unreadable stored doc is copied raw to `img3d.workflow.backup` and the default workflow for the current
 *   settings is loaded (info toast).
 * - `past` / `future` (≤ 50, not persisted). Undo/redo restore a doc under a NEW revision, so revisions only grow
 *   (the canvas sync key and the server report are keyed by revision).
 * - The registry comes from the catalog (`featuresStore.ensureCatalog`, Node shell only) or `BUILTIN_REGISTRY`, with
 *   the live `/features.nodes` states applied (`registryNow`).
 * Layer (§1.4): `workflow → features, input, partOptions, settings, toast`; api (`validateWorkflow`,
 * `getWorkflowGraph`), lib, workflow/. It never imports actions/ or navigationStore (the Node shell writes
 * through, §4.3.6).
 */
import { create } from 'zustand'

import type { CatalogOut, FeatureNodeOut, ImageGenerationRequest, ValidationReport, WorkflowRequest } from '../api/partAwareTypes.ts'
import { getWorkflowGraph, validateWorkflow } from '../api/workflows'
import { effectivePartsEnabled, effectiveSource } from '../lib/generatePlan.ts'
import type { PartOptionsForm } from '../lib/forms.ts'
import { readString, STORAGE_KEYS, writeString } from '../lib/storage.ts'
import {
  addNode as addNodeOp,
  BUILTIN_REGISTRY,
  configFromStores,
  connect as connectOp,
  disconnect as disconnectOp,
  edgeKey,
  exportWorkflowFile,
  fromServerGraph,
  GraphError,
  importWorkflowFile,
  layeredLayout,
  loadPersistedDoc,
  moveNodes as moveNodesOp,
  nodeIdFor,
  reconcile,
  RegistryError,
  registryFromCatalog,
  removeNodes as removeNodesOp,
  SERIALIZE_MESSAGES,
  setCollapsed as setCollapsedOp,
  setMode as setModeOp,
  setViewport as setViewportOp,
  stripBoundValues,
  switchSource as switchSourceOp,
  templateFromConfig,
  tidyLayout,
  topologyConfig,
  withNodeStates,
  type GraphIssue,
  type NodeMode,
  type NodeRegistry,
  type Point,
  type PortRef,
  type PortType,
  type Viewport,
  type WorkflowDoc,
} from '../workflow/engine/index.ts'
import { useFeaturesStore } from './featuresStore'
import { useInputStore } from './inputStore'
import { usePartOptionsStore } from './partOptionsStore'
import { toast } from './toastStore'

const HISTORY_LIMIT = 50
const APP_VERSION = '0.0.0'

export type WorkflowSelection = { kind: 'node' | 'edge'; id: string }
export type RunKind = 'job' | 'workflow'
export interface RunIndexEntry {
  readonly kind: RunKind
  readonly nodeIds: readonly string[]
}
/** Add-node palette request: `accepts` filters by a compatible port type, `connectFrom` wires the new node. */
export interface PaletteRequest {
  readonly accepts: PortType | null
  readonly position: Point | null
  readonly connectFrom: (PortRef & { readonly side: 'output' | 'input' }) | null
}
export type ImportOutcome = { ok: true; issues: readonly GraphIssue[] } | { ok: false; message: string }

interface WorkflowState {
  doc: WorkflowDoc
  past: WorkflowDoc[]
  future: WorkflowDoc[]
  selection: WorkflowSelection | null
  serverReport: { revision: number; report: ValidationReport } | null
  runIndex: Record<string, RunIndexEntry>
  /** Issues from the last load/import (unknown types, dropped links), shown in Problems until the next import. */
  loadIssues: readonly GraphIssue[]
  problemsOpen: boolean
  palette: PaletteRequest | null

  addNode: (type: string, position?: Point | null, connectFrom?: PaletteRequest['connectFrom']) => boolean
  removeNodes: (ids: readonly string[]) => void
  connect: (from: PortRef, to: PortRef) => void
  disconnect: (key: string) => void
  setMode: (id: string, mode: NodeMode) => void
  /** One history entry (drag stop / keyboard move). */
  moveNodes: (positions: Readonly<Record<string, Point>>) => void
  setCollapsed: (id: string, collapsed: boolean) => void
  /** No history entry. */
  setViewport: (viewport: Viewport) => void
  select: (selection: WorkflowSelection | null) => void
  undo: () => void
  redo: () => void
  tidy: () => void
  switchSource: (sourceId: string) => void
  /** A catalog template id, or `'current'` = the default workflow for the current settings. */
  loadTemplate: (id: string) => boolean
  importFile: (text: string) => ImportOutcome
  exportFile: () => string
  syncFromStores: () => void
  loadServerGraph: (workflowId: string, withSettings?: boolean) => Promise<boolean>
  applyServerReport: (revision: number, report: ValidationReport) => void
  rememberRun: (id: string, kind: RunKind, nodeIds: readonly string[]) => void
  validateRemote: (revision: number, request: WorkflowRequest, signal?: AbortSignal) => Promise<ValidationReport | null>
  setProblemsOpen: (open: boolean) => void
  openPalette: (request: PaletteRequest | null) => void
}

// ------------------------------------------------------------------------------------------------
// Registry (cached by the catalog and `/features.nodes` references)
// ------------------------------------------------------------------------------------------------
let registryCache: { catalog: CatalogOut | null; nodes: readonly FeatureNodeOut[]; registry: NodeRegistry } | null = null

/** `withNodeStates(catalog ? registryFromCatalog(catalog) : BUILTIN_REGISTRY, nodes)`; a bad catalog falls back. */
export function registryFor(catalog: CatalogOut | null, nodes: readonly FeatureNodeOut[]): NodeRegistry {
  if (registryCache && registryCache.catalog === catalog && registryCache.nodes === nodes) return registryCache.registry
  let base = BUILTIN_REGISTRY
  if (catalog) {
    try {
      base = registryFromCatalog(catalog)
    } catch (error) {
      if (!(error instanceof RegistryError)) throw error
      if (import.meta.env.DEV) console.warn('[img3d] workflow catalog rejected:', error.message)
    }
  }
  const registry = nodes.length ? withNodeStates(base, nodes) : base
  registryCache = { catalog, nodes, registry }
  return registry
}

export function registryNow(): NodeRegistry {
  const features = useFeaturesStore.getState()
  return registryFor(features.catalog, features.snapshot.nodes)
}

// ------------------------------------------------------------------------------------------------
// Persistence
// ------------------------------------------------------------------------------------------------
function storeParts(): { source: 'image' | 'prompt'; parts: PartOptionsForm } {
  const features = useFeaturesStore.getState().snapshot
  const form = usePartOptionsStore.getState().form
  return {
    source: effectiveSource(useInputStore.getState().source, features),
    // UI11 / F3 request: a legacy backend forces parts off, so a stored `enabled` never reconciles Part Extraction on.
    parts: { ...form, enabled: effectivePartsEnabled(form, features) },
  }
}

function defaultDoc(revision = 0): WorkflowDoc {
  const { source, parts } = storeParts()
  return templateFromConfig(configFromStores(null, source, parts), registryNow(), revision)
}

function initialDoc(): { doc: WorkflowDoc; issues: readonly GraphIssue[] } {
  const result = loadPersistedDoc(readString(STORAGE_KEYS.workflow), registryNow())
  if (result.kind === 'ok') return { doc: result.doc, issues: result.issues }
  if (result.kind === 'reset') {
    writeString(STORAGE_KEYS.workflowBackup, result.backup)
    toast.info(SERIALIZE_MESSAGES.restored)
  }
  return { doc: defaultDoc(), issues: [] }
}

const pushPast = (past: WorkflowDoc[], doc: WorkflowDoc): WorkflowDoc[] =>
  past.length >= HISTORY_LIMIT ? [...past.slice(past.length - HISTORY_LIMIT + 1), doc] : [...past, doc]

/** Drop the removed node ids from every run entry, so a node re-added after a Run never inherits its state (F3). */
function pruneRunIndex(index: Record<string, RunIndexEntry>, removed: readonly string[]): Record<string, RunIndexEntry> {
  if (!removed.length) return index
  const gone = new Set(removed)
  let changed = false
  const next: Record<string, RunIndexEntry> = {}
  for (const [id, entry] of Object.entries(index)) {
    if (entry.nodeIds.some((nodeId) => gone.has(nodeId))) {
      changed = true
      next[id] = { kind: entry.kind, nodeIds: entry.nodeIds.filter((nodeId) => !gone.has(nodeId)) }
    } else next[id] = entry
  }
  return changed ? next : index
}

const strings = (value: unknown): string[] | null =>
  Array.isArray(value) && value.every((item) => typeof item === 'string') ? [...value] : null

/** "Load these settings": the known prompt, part, segmentation and export params of a server graph (others dropped). */
function applyServerParams(params: Readonly<Record<string, Readonly<Record<string, unknown>>>>, nodeTypes: Record<string, string>): number {
  let dropped = 0
  const parts: Partial<PartOptionsForm> = {}
  for (const [nodeId, values] of Object.entries(params)) {
    const type = nodeTypes[nodeId]
    if (type === 'prompt' && typeof values.prompt === 'string') {
      useInputStore.getState().loadPrompt(values as unknown as ImageGenerationRequest)
      continue
    }
    for (const [key, value] of Object.entries(values)) {
      if (type === 'part_extraction' && key === 'strategy' && (typeof value === 'string' || value === null)) parts.strategy = value
      else if (type === 'part_extraction' && key === 'separate' && strings(value)) parts.separate = strings(value)
      else if (type === 'part_extraction' && key === 'views' && (typeof value === 'number' || value === null)) parts.views = value
      else if (type === 'segmentation' && key === 'extra_terms' && strings(value)) parts.extraTerms = strings(value) ?? []
      else if (type === 'export' && key === 'per_part_glbs' && typeof value === 'boolean') parts.perPartGlbs = value
      else if (type !== 'image_to_3d' && type !== 'input_image' && type !== 'job_result') dropped += 1
    }
  }
  if (Object.keys(parts).length) usePartOptionsStore.getState().update(parts)
  return dropped
}

// ------------------------------------------------------------------------------------------------
// Store
// ------------------------------------------------------------------------------------------------
const initial = initialDoc()

export const useWorkflowStore = create<WorkflowState>()((set, get) => {
  /** Replace the doc with one history entry; a no-op op (same doc) records nothing. */
  const commit = (next: WorkflowDoc, extra: Partial<WorkflowState> = {}): void => {
    const { doc, past } = get()
    if (next === doc) {
      if (Object.keys(extra).length) set(extra)
      return
    }
    set({ doc: next, past: pushPast(past, doc), future: [], ...extra })
  }
  const guarded = (op: () => WorkflowDoc): WorkflowDoc | null => {
    try {
      return op()
    } catch (error) {
      if (error instanceof GraphError) {
        toast.warning('Cannot change the workflow', error.message)
        return null
      }
      throw error
    }
  }

  return {
    doc: initial.doc,
    past: [],
    future: [],
    selection: null,
    serverReport: null,
    runIndex: {},
    loadIssues: initial.issues,
    problemsOpen: false,
    palette: null,

    addNode: (type, position, connectFrom) => {
      const r = registryNow()
      const def = r.get(type)
      if (!def) return false
      const { doc } = get()
      const at = position ?? nextFreePosition(doc)
      let next = guarded(() => addNodeOp(doc, def, at))
      if (!next) return false
      const id = nodeIdFor(type)
      if (connectFrom) {
        const fromDef = r.get(doc.graph.nodes.find((n) => n.id === connectFrom.node)?.type ?? '')
        const base = next
        const fromRef: PortRef = { node: connectFrom.node, port: connectFrom.port }
        if (connectFrom.side === 'output') {
          const out = fromDef?.outputs.find((p) => p.id === connectFrom.port)
          const input = out ? def.inputs.find((p) => p.accepts.includes(out.type)) : undefined
          if (input) next = guarded(() => connectOp(base, fromRef, { node: id, port: input.id }, r)) ?? next
        } else {
          const accepts = fromDef?.inputs.find((p) => p.id === connectFrom.port)?.accepts ?? []
          const output = def.outputs.find((p) => accepts.includes(p.type))
          if (output) next = guarded(() => connectOp(base, { node: id, port: output.id }, fromRef, r)) ?? next
        }
      }
      commit(next, { selection: { kind: 'node', id }, palette: null })
      return true
    },
    removeNodes: (ids) => {
      const { doc, selection, runIndex } = get()
      const next = removeNodesOp(doc, ids)
      const selectionGone = selection?.kind === 'node' ? ids.includes(selection.id) : selection !== null && !next.graph.edges.some((e) => edgeKey(e) === selection.id)
      commit(next, { runIndex: pruneRunIndex(runIndex, ids), ...(selectionGone ? { selection: null } : {}) })
    },
    connect: (from, to) => {
      const next = guarded(() => connectOp(get().doc, from, to, registryNow()))
      if (next) commit(next)
    },
    disconnect: (key) => {
      const { doc, selection } = get()
      commit(disconnectOp(doc, key), selection?.kind === 'edge' && selection.id === key ? { selection: null } : {})
    },
    setMode: (id, mode) => {
      const next = guarded(() => setModeOp(get().doc, id, mode, registryNow()))
      if (next) commit(next)
    },
    moveNodes: (positions) => commit(moveNodesOp(get().doc, positions)),
    setCollapsed: (id, collapsed) => commit(setCollapsedOp(get().doc, id, collapsed)),
    setViewport: (viewport) => {
      const { doc } = get()
      const current = doc.layout.viewport
      if (current.x === viewport.x && current.y === viewport.y && current.zoom === viewport.zoom) return
      set({ doc: setViewportOp(doc, viewport) })
    },
    select: (selection) =>
      set((state) =>
        state.selection?.kind === selection?.kind && state.selection?.id === selection?.id ? state : { selection },
      ),
    undo: () => {
      const { doc, past, future } = get()
      const previous = past[past.length - 1]
      if (!previous) return
      set({ doc: { ...previous, revision: doc.revision + 1 }, past: past.slice(0, -1), future: [doc, ...future].slice(0, HISTORY_LIMIT) })
    },
    redo: () => {
      const { doc, past, future } = get()
      const nextDoc = future[0]
      if (!nextDoc) return
      set({ doc: { ...nextDoc, revision: doc.revision + 1 }, past: pushPast(past, doc), future: future.slice(1) })
    },
    tidy: () => commit(tidyLayout(get().doc)),
    switchSource: (sourceId) => commit(switchSourceOp(get().doc, sourceId, registryNow())),
    loadTemplate: (id) => {
      const r = registryNow()
      const { doc } = get()
      if (id === 'current') {
        commit(defaultDoc(doc.revision + 1), { selection: null })
        return true
      }
      const template = useFeaturesStore.getState().catalog?.templates.find((t) => t.id === id)
      if (!template) return false
      const loaded = fromServerGraph(template.graph, null, r)
      commit({ ...loaded.doc, revision: doc.revision + 1 }, { selection: null, loadIssues: loaded.issues })
      return true
    },
    importFile: (text) => {
      const { doc } = get()
      const limits = useFeaturesStore.getState().snapshot.workflow
      const result = importWorkflowFile(text, registryNow(), limits)
      if (!result.ok) return { ok: false, message: result.message }
      commit({ ...result.doc, revision: doc.revision + 1 }, { selection: null, loadIssues: result.issues })
      return { ok: true, issues: result.issues }
    },
    exportFile: () => exportWorkflowFile(get().doc, registryNow(), APP_VERSION),
    syncFromStores: () => {
      const r = registryNow()
      const { doc } = get()
      const { source, parts } = storeParts()
      const next = reconcile(doc, configFromStores(topologyConfig(doc, r)?.source ?? null, source, parts), r)
      // A store-driven projection, not a user edit: no history entry.
      if (next !== doc) set({ doc: next })
    },
    loadServerGraph: async (workflowId, withSettings = false) => {
      try {
        const { graph, layout } = await getWorkflowGraph(workflowId)
        const r = registryNow()
        const loaded = fromServerGraph(graph, layout, r)
        const { doc } = get()
        commit({ ...loaded.doc, revision: doc.revision + 1 }, { selection: null, loadIssues: loaded.issues })
        if (withSettings) {
          const types = Object.fromEntries(loaded.doc.graph.nodes.map((n) => [n.id, n.type]))
          const dropped = applyServerParams(loaded.params, types)
          if (dropped) toast.warning('Some settings were not loaded', `${dropped} unknown setting${dropped === 1 ? '' : 's'} ignored.`)
        }
        return true
      } catch {
        toast.error('Could not load the workflow', 'Check the connection and try again.')
        return false
      }
    },
    applyServerReport: (revision, report) => {
      if (revision !== get().doc.revision) return
      set({ serverReport: { revision, report } })
    },
    rememberRun: (id, kind, nodeIds) => set((state) => ({ runIndex: { ...state.runIndex, [id]: { kind, nodeIds } } })),
    validateRemote: async (revision, request, signal) => {
      try {
        const report = await validateWorkflow(request, signal)
        if (signal?.aborted) return null
        get().applyServerReport(revision, report)
        return report
      } catch {
        return null
      }
    },
    setProblemsOpen: (problemsOpen) => set((state) => (state.problemsOpen === problemsOpen ? state : { problemsOpen })),
    openPalette: (palette) => set({ palette }),
  }
})

/** Below the lowest node of the layered grid (new palette nodes never overlap). */
function nextFreePosition(doc: WorkflowDoc): Point {
  const positions = Object.values(doc.layout.positions)
  if (!positions.length) return layeredLayout(doc.graph)[doc.graph.nodes[0]?.id ?? ''] ?? { x: 0, y: 0 }
  const maxY = Math.max(...positions.map((p) => p.y))
  const minX = Math.min(...positions.map((p) => p.x))
  return { x: minX, y: maxY + 200 }
}

// Persist the doc (built-in values stripped) whenever it changes.
useWorkflowStore.subscribe((state, previous) => {
  if (state.doc === previous.doc) return
  writeString(STORAGE_KEYS.workflow, JSON.stringify({ doc: stripBoundValues(state.doc, registryNow()) }))
})
