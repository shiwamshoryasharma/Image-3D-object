/**
 * Graph operations (UI_DESIGN.md §4.3.4). Every op is pure: it returns a new doc with `revision + 1` and shares every
 * untouched subtree with the input; an op that changes nothing returns the input doc itself (no revision bump), so
 * the store can skip history entries for no-ops.
 *
 * PURE (UI_DESIGN §1.5).
 */
import type { NodeRegistry } from './registry.ts'
import { NODE_ID_PATTERN } from './types.ts'
import type { Graph, GraphEdge, GraphNode, IssueCode, NodeMode, NodeTypeDef, Point, PortRef, Viewport, WorkflowDoc } from './types.ts'

/** Thrown by `addNode` for a second instance of a type (`too_many_instances`). */
export class GraphError extends Error {
  readonly code: IssueCode

  constructor(code: IssueCode, message: string) {
    super(message)
    this.name = 'GraphError'
    this.code = code
  }
}

export type ConnectCheck = { readonly ok: true } | { readonly ok: false; readonly code: IssueCode; readonly message: string }

/** Template / auto-layout grid (UI_DESIGN §4.3.6: columns 320 px apart). */
export const COLUMN_GAP = 320
export const ROW_GAP = 260

export function edgeKey(e: GraphEdge): string {
  return `${e.from.node}.${e.from.port}->${e.to.node}.${e.to.port}`
}

/** `n_<type>`, reduced to the server id rule `^[a-z][a-z0-9_]{0,31}$`. */
export function nodeIdFor(type: string): string {
  return `n_${type.toLowerCase().replace(/[^a-z0-9_]/g, '_')}`.slice(0, 32)
}

export function findNode(graph: Graph, id: string): GraphNode | undefined {
  return graph.nodes.find((node) => node.id === id)
}

/** First node of a type (every v1 type has at most one instance). */
export function nodeOfType(graph: Graph, type: string): GraphNode | undefined {
  return graph.nodes.find((node) => node.type === type)
}

export function presentTypes(doc: WorkflowDoc): ReadonlySet<string> {
  return new Set(doc.graph.nodes.map((node) => node.type))
}

function withChanges(doc: WorkflowDoc, changes: { graph?: Graph; layout?: WorkflowDoc['layout'] }): WorkflowDoc {
  return { ...doc, revision: doc.revision + 1, graph: changes.graph ?? doc.graph, layout: changes.layout ?? doc.layout }
}

function uniqueId(graph: Graph, base: string): string {
  if (!findNode(graph, base)) return base
  for (let index = 2; ; index += 1) {
    const suffix = `_${index}`
    const id = `${base.slice(0, 32 - suffix.length)}${suffix}`
    if (!findNode(graph, id)) return id
  }
}

/** Add a node of `def` at `position` (id `n_<type>`; locked types start bypassed). Throws on a second instance. */
export function addNode(doc: WorkflowDoc, def: NodeTypeDef, position: Point): WorkflowDoc {
  const count = doc.graph.nodes.filter((node) => node.type === def.type).length
  if (count >= def.maxInstances) {
    throw new GraphError('too_many_instances', `Only one ${def.title} node is supported.`)
  }
  const id = uniqueId(doc.graph, nodeIdFor(def.type))
  const node: GraphNode = {
    id,
    type: def.type,
    version: def.version,
    mode: def.locked ? 'bypass' : 'active',
    ...(def.binds === 'none' ? { values: { ...def.defaults } } : {}),
  }
  return withChanges(doc, {
    graph: { ...doc.graph, nodes: [...doc.graph.nodes, node] },
    layout: { ...doc.layout, positions: { ...doc.layout.positions, [id]: { x: position.x, y: position.y } } },
  })
}

/** Remove nodes together with their incident edges, positions and collapsed flags. */
export function removeNodes(doc: WorkflowDoc, ids: readonly string[]): WorkflowDoc {
  const remove = new Set(ids.filter((id) => findNode(doc.graph, id)))
  if (remove.size === 0) return doc
  const positions = { ...doc.layout.positions }
  for (const id of remove) delete positions[id]
  const edges = doc.graph.edges.filter((edge) => !remove.has(edge.from.node) && !remove.has(edge.to.node))
  const collapsed = doc.layout.collapsed.filter((id) => !remove.has(id))
  return withChanges(doc, {
    graph: {
      nodes: doc.graph.nodes.filter((node) => !remove.has(node.id)),
      edges: edges.length === doc.graph.edges.length ? doc.graph.edges : edges,
    },
    layout: {
      ...doc.layout,
      positions,
      collapsed: collapsed.length === doc.layout.collapsed.length ? doc.layout.collapsed : collapsed,
    },
  })
}

/**
 * Add the edge `from → to`. An existing edge into the same input is replaced unless the registry says the input
 * accepts several links (`multiple`; none does in v1). Call `canConnect` first: this op only edits the topology.
 */
export function connect(doc: WorkflowDoc, from: PortRef, to: PortRef, r?: NodeRegistry): WorkflowDoc {
  const edge: GraphEdge = { from: { node: from.node, port: from.port }, to: { node: to.node, port: to.port } }
  const key = edgeKey(edge)
  if (doc.graph.edges.some((e) => edgeKey(e) === key)) return doc
  const target = findNode(doc.graph, to.node)
  const multiple = target ? r?.get(target.type)?.inputs.find((port) => port.id === to.port)?.multiple === true : false
  const kept = multiple ? doc.graph.edges : doc.graph.edges.filter((e) => e.to.node !== to.node || e.to.port !== to.port)
  return withChanges(doc, { graph: { ...doc.graph, edges: [...kept, edge] } })
}

export function disconnect(doc: WorkflowDoc, key: string): WorkflowDoc {
  const edges = doc.graph.edges.filter((edge) => edgeKey(edge) !== key)
  if (edges.length === doc.graph.edges.length) return doc
  return withChanges(doc, { graph: { ...doc.graph, edges } })
}

/**
 * Switch a node between active and bypassed. Bypass needs a bypass rule (`def.bypass !== null`); a locked or
 * unknown type can never become active. Refused or unchanged → the input doc.
 */
export function setMode(doc: WorkflowDoc, id: string, mode: NodeMode, r: NodeRegistry): WorkflowDoc {
  const node = findNode(doc.graph, id)
  if (!node || node.mode === mode) return doc
  const def = r.get(node.type)
  if (mode === 'bypass' && def && def.bypass === null) return doc
  if (mode === 'active' && (!def || def.locked)) return doc
  return withChanges(doc, {
    graph: { ...doc.graph, nodes: doc.graph.nodes.map((n) => (n.id === id ? { ...n, mode } : n)) },
  })
}

/** Merge new positions (unknown ids ignored). */
export function moveNodes(doc: WorkflowDoc, positions: Readonly<Record<string, Point>>): WorkflowDoc {
  let next: Record<string, Point> | null = null
  for (const [id, point] of Object.entries(positions)) {
    if (!findNode(doc.graph, id) || !Number.isFinite(point.x) || !Number.isFinite(point.y)) continue
    const current = doc.layout.positions[id]
    if (current && current.x === point.x && current.y === point.y) continue
    next ??= { ...doc.layout.positions }
    next[id] = { x: point.x, y: point.y }
  }
  if (next === null) return doc
  return withChanges(doc, { layout: { ...doc.layout, positions: next } })
}

export function setCollapsed(doc: WorkflowDoc, id: string, collapsed: boolean): WorkflowDoc {
  if (!findNode(doc.graph, id) || doc.layout.collapsed.includes(id) === collapsed) return doc
  const next = collapsed ? [...doc.layout.collapsed, id] : doc.layout.collapsed.filter((c) => c !== id)
  return withChanges(doc, { layout: { ...doc.layout, collapsed: next } })
}

export function setViewport(doc: WorkflowDoc, viewport: Viewport): WorkflowDoc {
  const current = doc.layout.viewport
  if (current.x === viewport.x && current.y === viewport.y && current.zoom === viewport.zoom) return doc
  return withChanges(doc, { layout: { ...doc.layout, viewport: { x: viewport.x, y: viewport.y, zoom: viewport.zoom } } })
}

/** Merge values into a `binds: 'none'` node (built-in nodes keep no values; refused → the input doc). */
export function setValues(
  doc: WorkflowDoc,
  id: string,
  values: Readonly<Record<string, unknown>>,
  r: NodeRegistry,
): WorkflowDoc {
  const node = findNode(doc.graph, id)
  if (!node || r.get(node.type)?.binds !== 'none') return doc
  const merged = { ...node.values, ...values }
  return withChanges(doc, {
    graph: { ...doc.graph, nodes: doc.graph.nodes.map((n) => (n.id === id ? { ...n, values: merged } : n)) },
  })
}

/** True when `target` can be reached from `start` along the edges. */
export function reaches(graph: Graph, start: string, target: string): boolean {
  const seen = new Set<string>()
  const stack = [start]
  while (stack.length > 0) {
    const id = stack.pop() as string
    if (id === target) return true
    if (seen.has(id)) continue
    seen.add(id)
    for (const edge of graph.edges) if (edge.from.node === id) stack.push(edge.to.node)
  }
  return false
}

/** Can `from` (an output) be linked to `to` (an input)? Ports exist, types match, no self loop, no cycle. */
export function canConnect(doc: WorkflowDoc, r: NodeRegistry, from: PortRef, to: PortRef): ConnectCheck {
  const fromNode = findNode(doc.graph, from.node)
  const toNode = findNode(doc.graph, to.node)
  if (!fromNode || !toNode) return { ok: false, code: 'unknown_port', message: 'That node is no longer in the workflow.' }
  const fromDef = r.get(fromNode.type)
  const toDef = r.get(toNode.type)
  if (!fromDef) return { ok: false, code: 'unknown_node_type', message: `Unknown node type "${fromNode.type}".` }
  if (!toDef) return { ok: false, code: 'unknown_node_type', message: `Unknown node type "${toNode.type}".` }
  const output = fromDef.outputs.find((port) => port.id === from.port)
  if (!output) return { ok: false, code: 'unknown_port', message: `${fromDef.title}: unknown port "${from.port}".` }
  const input = toDef.inputs.find((port) => port.id === to.port)
  if (!input) return { ok: false, code: 'unknown_port', message: `${toDef.title}: unknown port "${to.port}".` }
  if (from.node === to.node) return { ok: false, code: 'cycle', message: `${toDef.title} cannot connect to itself.` }
  if (!input.accepts.includes(output.type)) {
    return {
      ok: false,
      code: 'type_mismatch',
      message: `${fromDef.title} → ${toDef.title}: ${output.label} cannot connect to ${input.label}.`,
    }
  }
  if (reaches(doc.graph, to.node, from.node)) return { ok: false, code: 'cycle', message: 'This link would create a loop.' }
  return { ok: true }
}

/**
 * Topological order (Kahn; ready nodes are taken in id order). Edges to unknown nodes are ignored; nodes on a cycle
 * are left out, so `graph.nodes.length - topoOrder(graph).length` is the number of nodes in cycles.
 */
export function topoOrder(graph: Graph): readonly string[] {
  const ids = new Set(graph.nodes.map((node) => node.id))
  const indegree = new Map<string, number>()
  for (const id of ids) indegree.set(id, 0)
  const targets = new Map<string, string[]>()
  const seenPairs = new Set<string>()
  for (const edge of graph.edges) {
    const pair = `${edge.from.node}\u0000${edge.to.node}`
    if (!ids.has(edge.from.node) || !ids.has(edge.to.node) || seenPairs.has(pair)) continue
    seenPairs.add(pair)
    indegree.set(edge.to.node, (indegree.get(edge.to.node) ?? 0) + 1)
    const list = targets.get(edge.from.node)
    if (list) list.push(edge.to.node)
    else targets.set(edge.from.node, [edge.to.node])
  }
  const ready = [...ids].filter((id) => indegree.get(id) === 0).sort()
  const order: string[] = []
  while (ready.length > 0) {
    const id = ready.shift() as string
    order.push(id)
    for (const next of targets.get(id) ?? []) {
      const left = (indegree.get(next) ?? 0) - 1
      indegree.set(next, left)
      if (left === 0) {
        const at = ready.findIndex((other) => other > next)
        if (at === -1) ready.push(next)
        else ready.splice(at, 0, next)
      }
    }
  }
  return order
}

/** Layered auto-layout ("Tidy"): column = longest path from a root, rows in topological order. */
export function layeredLayout(graph: Graph): Record<string, Point> {
  const order = topoOrder(graph)
  const placed = new Set(order)
  const depth = new Map<string, number>()
  for (const id of order) {
    let d = 0
    for (const edge of graph.edges) {
      if (edge.to.node === id && placed.has(edge.from.node)) d = Math.max(d, (depth.get(edge.from.node) ?? 0) + 1)
    }
    depth.set(id, d)
  }
  const rows = new Map<number, number>()
  const positions: Record<string, Point> = {}
  for (const id of [...order, ...graph.nodes.map((node) => node.id).filter((id) => !placed.has(id))]) {
    const column = depth.get(id) ?? 0
    const row = rows.get(column) ?? 0
    rows.set(column, row + 1)
    positions[id] = { x: column * COLUMN_GAP, y: row * ROW_GAP }
  }
  return positions
}

/** "Tidy": move every node onto the layered grid. */
export function tidyLayout(doc: WorkflowDoc): WorkflowDoc {
  return moveNodes(doc, layeredLayout(doc.graph))
}

/** True for ids the server accepts (`^[a-z][a-z0-9_]{0,31}$`). */
export function isValidNodeId(id: unknown): id is string {
  return typeof id === 'string' && NODE_ID_PATTERN.test(id)
}
