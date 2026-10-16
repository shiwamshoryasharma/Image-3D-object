/**
 * Persistence, versioning, import/export and server-graph loading (UI_DESIGN.md §4.3.10).
 *
 * - Persisted `img3d.workflow` v1 = `{doc}`. `loadPersistedDoc` never throws: a newer doc version or unreadable JSON
 *   returns `kind: 'reset'` with the raw text as `backup`; the store copies it to `img3d.workflow.backup`, loads
 *   `templateFromConfig(configFromStores(null, …))` and shows the info toast.
 * - Every load / import goes through `normalizeDoc`: malformed entries are dropped, node `migrate` runs when the
 *   definition is newer, unknown types are kept but bypassed (`unknown_node_type`), built-in nodes lose any values
 *   (they bind to the stores), layout numbers are clamped to finite ±100000.
 * - File export/import: `.img3d-workflow.json` = `{schema, version: 1, app: 'img3d-frontend@<version>', doc}`. It
 *   never contains reference set ids, job ids, prompts or tokens (built-ins have no values). Import limits: ≤ 256 KB
 *   text, ≤ `maxNodes` nodes, ≤ `maxEdges` edges; issues are reported, nothing throws.
 * - `fromServerGraph`: `GET /workflows/{id}/graph` → topology + positions; node params are returned separately for
 *   the optional "Load these settings" action and are never written into the doc.
 *
 * PURE (UI_DESIGN §1.5).
 */
import type { WorkflowGraph } from '../../api/partAwareTypes.ts'
import { utf8Length } from './compile.ts'
import { edgeKey, isValidNodeId, layeredLayout } from './graph.ts'
import type { NodeRegistry } from './registry.ts'
import { TEMPLATE_POSITIONS, TEMPLATE_VIEWPORT } from './template.ts'
import { GRAPH_LIMITS_FALLBACK, WORKFLOW_DOC_SCHEMA, WORKFLOW_DOC_VERSION } from './types.ts'
import type { GraphEdge, GraphIssue, GraphNode, Layout, NodeMode, Point, Viewport, WorkflowDoc } from './types.ts'
import { tooLargeMessage } from './validate.ts'

/** Largest workflow file the importer reads. */
export const WORKFLOW_FILE_MAX_BYTES = 256 * 1024
/** Layout coordinates are clamped to ±this. */
export const LAYOUT_COORD_LIMIT = 100_000
/** Canvas zoom range (React Flow `minZoom` / `maxZoom`, UI_DESIGN §4.3.12). */
export const MIN_ZOOM = 0.25
export const MAX_ZOOM = 2
/** File name suffix of exported workflows (UI_DESIGN §4.3.10). */
export const WORKFLOW_FILE_SUFFIX = '.img3d-workflow.json'

export interface DocLimits {
  readonly maxNodes: number
  readonly maxEdges: number
}

export interface NormalizedDoc {
  readonly doc: WorkflowDoc
  readonly issues: readonly GraphIssue[]
}

export type NormalizeResult = ({ readonly ok: true } & NormalizedDoc) | { readonly ok: false; readonly reason: 'newer_version' | 'unreadable' }

export type LoadResult =
  | ({ readonly kind: 'ok' } & NormalizedDoc)
  /** Nothing stored: load the default workflow. */
  | { readonly kind: 'empty' }
  /** Newer or unreadable: keep `backup` in `img3d.workflow.backup`, load the default workflow, show the info toast. */
  | { readonly kind: 'reset'; readonly reason: 'newer_version' | 'unreadable'; readonly backup: string }

export type ImportResult = ({ readonly ok: true } & NormalizedDoc) | { readonly ok: false; readonly message: string; readonly issues: readonly GraphIssue[] }

export interface ServerGraphDoc extends NormalizedDoc {
  /** Server params by node id (built-in nodes only), for "Load these settings". */
  readonly params: Readonly<Record<string, Readonly<Record<string, unknown>>>>
}

export const SERIALIZE_MESSAGES = {
  restored: 'The saved workflow was made by a newer version; the default workflow was restored.',
  notAWorkflow: 'This file is not an Img3D workflow.',
  newerFile: 'This workflow was made by a newer version of Img3D.',
  fileTooLarge: 'The workflow file is too large (max 256 KB).',
} as const

type Json = Record<string, unknown>

const isRecord = (value: unknown): value is Json => typeof value === 'object' && value !== null && !Array.isArray(value)

function issue(severity: GraphIssue['severity'], code: GraphIssue['code'], message: string, nodeId: string | null = null): GraphIssue {
  return { severity, code, message, nodeId, port: null, edgeIndex: null, origin: 'client' }
}

const clampCoord = (value: unknown): number =>
  typeof value === 'number' && Number.isFinite(value) ? Math.min(LAYOUT_COORD_LIMIT, Math.max(-LAYOUT_COORD_LIMIT, value)) : 0

function readPoint(value: unknown): Point | null {
  return isRecord(value) ? { x: clampCoord(value.x), y: clampCoord(value.y) } : null
}

function readViewport(value: unknown): Viewport {
  if (!isRecord(value)) return { ...TEMPLATE_VIEWPORT }
  const zoom = typeof value.zoom === 'number' && Number.isFinite(value.zoom) && value.zoom > 0 ? value.zoom : 1
  return { x: clampCoord(value.x), y: clampCoord(value.y), zoom: Math.min(MAX_ZOOM, Math.max(MIN_ZOOM, zoom)) }
}

function readPortRef(value: unknown): { node: string; port: string } | null {
  if (!isRecord(value) || typeof value.node !== 'string' || typeof value.port !== 'string' || !value.port) return null
  return { node: value.node, port: value.port }
}

const positiveInt = (value: unknown, fallback: number): number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 1 ? value : fallback

/** Nodes, edges and layout read tolerantly; every id is unique and valid, every edge joins two existing nodes. */
function readGraph(
  rawNodes: readonly unknown[],
  rawEdges: readonly unknown[],
  rawLayout: unknown,
  r: NodeRegistry,
  node: (item: Json, id: string, type: string) => GraphNode,
): NormalizedDoc {
  const issues: GraphIssue[] = []
  const nodes: GraphNode[] = []
  const ids = new Set<string>()
  for (const item of rawNodes) {
    if (!isRecord(item) || !isValidNodeId(item.id) || ids.has(item.id)) continue
    if (typeof item.type !== 'string' || !item.type || item.type.length > 64) continue
    const parsed = node(item, item.id, item.type)
    if (!r.get(parsed.type)) issues.push(issue('warning', 'unknown_node_type', `Unknown node type "${parsed.type}". It is ignored.`, parsed.id))
    ids.add(parsed.id)
    nodes.push(parsed)
  }
  const edges: GraphEdge[] = []
  const keys = new Set<string>()
  for (const item of rawEdges) {
    if (!isRecord(item)) continue
    const from = readPortRef(item.from)
    const to = readPortRef(item.to)
    if (!from || !to || !ids.has(from.node) || !ids.has(to.node)) continue
    const edge = { from, to }
    const key = edgeKey(edge)
    if (keys.has(key)) continue
    keys.add(key)
    edges.push(edge)
  }
  const layout = isRecord(rawLayout) ? rawLayout : {}
  const rawPositions = isRecord(layout.positions) ? layout.positions : {}
  const positions: Record<string, Point> = {}
  const missing: GraphNode[] = []
  for (const n of nodes) {
    const point = Object.hasOwn(rawPositions, n.id) ? readPoint(rawPositions[n.id]) : null
    if (point) positions[n.id] = point
    else missing.push(n)
  }
  if (missing.length > 0) {
    // Template slots first (server graphs carry no layout), then the layered grid.
    const taken = new Set(Object.values(positions).map((p) => `${p.x},${p.y}`))
    const layered = layeredLayout({ nodes, edges })
    for (const n of missing) {
      const slot = Object.hasOwn(TEMPLATE_POSITIONS, n.type) ? TEMPLATE_POSITIONS[n.type] : null
      const point = slot && !taken.has(`${slot.x},${slot.y}`) ? slot : layered[n.id]
      positions[n.id] = { x: point.x, y: point.y }
      taken.add(`${point.x},${point.y}`)
    }
  }
  const collapsed = Array.isArray(layout.collapsed)
    ? [...new Set(layout.collapsed.filter((id): id is string => typeof id === 'string' && ids.has(id)))]
    : []
  const result: Layout = { positions, collapsed, viewport: readViewport(layout.viewport) }
  return {
    doc: { schema: WORKFLOW_DOC_SCHEMA, version: WORKFLOW_DOC_VERSION, revision: 0, graph: { nodes, edges }, layout: result },
    issues,
  }
}

/**
 * A stored or imported doc → a valid doc. `newer_version` for a doc version above 1, `unreadable` for anything that
 * is not an `img3d.ui_workflow` doc.
 */
export function normalizeDoc(value: unknown, r: NodeRegistry): NormalizeResult {
  if (!isRecord(value) || value.schema !== WORKFLOW_DOC_SCHEMA) return { ok: false, reason: 'unreadable' }
  if (typeof value.version === 'number' && value.version > WORKFLOW_DOC_VERSION) return { ok: false, reason: 'newer_version' }
  if (value.version !== WORKFLOW_DOC_VERSION) return { ok: false, reason: 'unreadable' }
  const graph = value.graph
  if (!isRecord(graph) || !Array.isArray(graph.nodes) || !Array.isArray(graph.edges)) return { ok: false, reason: 'unreadable' }

  const read = readGraph(graph.nodes, graph.edges, value.layout, r, (item, id, type) => {
    const def = r.get(type)
    const stored = positiveInt(item.version, 1)
    const values = isRecord(item.values) ? item.values : null
    if (!def) {
      // Kept (so a newer app can still use it) but bypassed: it never runs here.
      return { id, type, version: stored, mode: 'bypass', ...(values ? { values } : {}) }
    }
    const mode: NodeMode = item.mode === 'bypass' ? 'bypass' : 'active'
    const version = Math.max(stored, def.version)
    if (def.binds !== 'none') return { id, type, version, mode } // built-ins bind to the stores
    let current: Readonly<Record<string, unknown>> = values ?? { ...def.defaults }
    if (def.version > stored && def.migrate) current = def.migrate(current, stored)
    return { id, type, version, mode, values: current }
  })
  const revision = typeof value.revision === 'number' && Number.isInteger(value.revision) && value.revision >= 0 ? value.revision : 0
  return { ok: true, doc: { ...read.doc, revision }, issues: read.issues }
}

/**
 * Persisted value (the store's `{doc}` state, a bare doc, a zustand `{state: {doc}}` envelope or its JSON text) →
 * a doc, or the reset signal.
 */
export function loadPersistedDoc(raw: unknown, r: NodeRegistry): LoadResult {
  if (raw === null || raw === undefined || raw === '') return { kind: 'empty' }
  let value: unknown = raw
  if (typeof raw === 'string') {
    try {
      value = JSON.parse(raw)
    } catch {
      return { kind: 'reset', reason: 'unreadable', backup: raw }
    }
  }
  const backup = typeof raw === 'string' ? raw : JSON.stringify(raw)
  if (isRecord(value) && isRecord(value.state)) value = value.state
  if (isRecord(value) && 'doc' in value) {
    if (value.doc === null || value.doc === undefined) return { kind: 'empty' }
    value = value.doc
  }
  const result = normalizeDoc(value, r)
  if (!result.ok) return { kind: 'reset', reason: result.reason, backup: backup ?? '' }
  return { kind: 'ok', doc: result.doc, issues: result.issues }
}

/** The doc as stored or exported: built-in nodes carry no values (no ids, prompts or tokens can leak). */
export function stripBoundValues(doc: WorkflowDoc, r: NodeRegistry): WorkflowDoc {
  const nodes = doc.graph.nodes.map((node) => {
    const def = r.get(node.type)
    if (node.values === undefined || !def || def.binds === 'none') return node
    return { id: node.id, type: node.type, version: node.version, mode: node.mode }
  })
  return nodes.every((node, i) => node === doc.graph.nodes[i]) ? doc : { ...doc, graph: { ...doc.graph, nodes } }
}

/** `.img3d-workflow.json` text. */
export function exportWorkflowFile(doc: WorkflowDoc, r: NodeRegistry, appVersion: string): string {
  const file = { schema: WORKFLOW_DOC_SCHEMA, version: WORKFLOW_DOC_VERSION, app: `img3d-frontend@${appVersion}`, doc: stripBoundValues(doc, r) }
  return JSON.stringify(file, null, 2)
}

/** Read a workflow file (or a bare doc). The caller gives the imported doc the next revision of its current doc. */
export function importWorkflowFile(text: string, r: NodeRegistry, limits: DocLimits = GRAPH_LIMITS_FALLBACK): ImportResult {
  if (utf8Length(text) > WORKFLOW_FILE_MAX_BYTES) {
    return { ok: false, message: SERIALIZE_MESSAGES.fileTooLarge, issues: [issue('error', 'graph_too_large', SERIALIZE_MESSAGES.fileTooLarge)] }
  }
  let parsed: unknown
  try {
    parsed = JSON.parse(text)
  } catch {
    return { ok: false, message: SERIALIZE_MESSAGES.notAWorkflow, issues: [] }
  }
  let value: unknown = parsed
  if (isRecord(parsed) && parsed.schema === WORKFLOW_DOC_SCHEMA && 'doc' in parsed) {
    if (typeof parsed.version === 'number' && parsed.version > WORKFLOW_DOC_VERSION) {
      return { ok: false, message: SERIALIZE_MESSAGES.newerFile, issues: [] }
    }
    value = parsed.doc
  }
  const result = normalizeDoc(value, r)
  if (!result.ok) {
    return { ok: false, message: result.reason === 'newer_version' ? SERIALIZE_MESSAGES.newerFile : SERIALIZE_MESSAGES.notAWorkflow, issues: [] }
  }
  const { nodes, edges } = result.doc.graph
  if (nodes.length > limits.maxNodes || edges.length > limits.maxEdges) {
    const message = tooLargeMessage(limits.maxNodes, limits.maxEdges)
    return { ok: false, message, issues: [...result.issues, issue('error', 'graph_too_large', message)] }
  }
  return { ok: true, doc: result.doc, issues: result.issues }
}

/**
 * A server graph (`GET /workflows/{id}/graph`, a catalog template) → a doc: topology and positions only. Every node
 * is active (the server graph holds the run nodes); unknown types are kept bypassed. Missing positions take the
 * template slot of their type, else the layered grid.
 */
export function fromServerGraph(graph: WorkflowGraph | null | undefined, layout: unknown, r: NodeRegistry): ServerGraphDoc {
  const params: Record<string, Readonly<Record<string, unknown>>> = {}
  const rawNodes = isRecord(graph) && Array.isArray(graph.nodes) ? graph.nodes : []
  const rawEdges = isRecord(graph) && Array.isArray(graph.edges) ? graph.edges : []
  const read = readGraph(rawNodes, rawEdges, layout, r, (item, id, type) => {
    const def = r.get(type)
    const nodeParams = isRecord(item.params) ? item.params : {}
    if (!def) return { id, type, version: 1, mode: 'bypass' }
    if (def.binds === 'none') return { id, type, version: def.version, mode: 'active', values: { ...nodeParams } }
    params[id] = { ...nodeParams }
    return { id, type, version: def.version, mode: 'active' }
  })
  return { doc: read.doc, issues: read.issues, params }
}

