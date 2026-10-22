/**
 * WorkflowDoc ↔ React Flow (UI_DESIGN §4.3.12). PURE (§1.5): `import type` from `@xyflow/react` only, so it runs
 * under Node's test runner (`flow_adapter.test.mjs`).
 * - Nodes carry topology only (`data = {id, type}`); run status goes through `NodeStatusContext`, never `nodes`.
 * - Edge ids are `edgeKey(e)`; colour = the source port type token; `animated` only while the source runs and motion
 *   is allowed.
 */
import type { Edge, EdgeChange, Node, NodeChange } from '@xyflow/react'

import { edgeKey } from '../../../workflow/engine/graph.ts'
import type { NodeRegistry } from '../../../workflow/engine/registry.ts'
import { PORT_META, type NodeStatusView, type PortType, type WorkflowDoc } from '../../../workflow/engine/types.ts'

export interface PipelineNodeData extends Record<string, unknown> {
  readonly id: string
  readonly type: string
}
export type PipelineFlowNode = Node<PipelineNodeData, 'pipeline'>
export interface PipelineEdgeData extends Record<string, unknown> {
  readonly portType: PortType | null
}
export type PipelineFlowEdge = Edge<PipelineEdgeData>
export type FlowSelection = { readonly kind: 'node' | 'edge'; readonly id: string }
/** `bezier` → React Flow's `default` edge. */
export type FlowLinkStyle = 'bezier' | 'smoothstep' | 'straight'

export interface ClassifiedChanges {
  /** Final positions (drag stop or keyboard move); in-progress drags stay local. */
  readonly moved: Readonly<Record<string, { x: number; y: number }>>
  readonly removed: readonly string[]
  readonly removedEdges: readonly string[]
  /** `undefined` = no selection change; `null` = deselected. */
  readonly selected: FlowSelection | null | undefined
}

const ORIGIN = Object.freeze({ x: 0, y: 0 })

export function toFlowNodes(doc: WorkflowDoc, _registry: NodeRegistry, selection: FlowSelection | null): PipelineFlowNode[] {
  return doc.graph.nodes.map((node) => ({
    id: node.id,
    type: 'pipeline',
    position: doc.layout.positions[node.id] ?? ORIGIN,
    data: { id: node.id, type: node.type },
    selected: selection?.kind === 'node' && selection.id === node.id,
  }))
}

/** Output port type of `node.port`, or null when the node or port is unknown. */
export function sourcePortType(doc: WorkflowDoc, registry: NodeRegistry, nodeId: string, portId: string): PortType | null {
  const type = doc.graph.nodes.find((n) => n.id === nodeId)?.type
  const def = type ? registry.get(type) : undefined
  return def?.outputs.find((p) => p.id === portId)?.type ?? null
}

export function toFlowEdges(
  doc: WorkflowDoc,
  registry: NodeRegistry,
  statuses: Readonly<Record<string, NodeStatusView>>,
  reducedMotion: boolean,
  selection: FlowSelection | null,
  linkStyle: FlowLinkStyle = 'bezier',
): PipelineFlowEdge[] {
  const ids = new Set(doc.graph.nodes.map((n) => n.id))
  const type = linkStyle === 'bezier' ? 'default' : linkStyle
  return doc.graph.edges
    .filter((e) => ids.has(e.from.node) && ids.has(e.to.node))
    .map((e) => {
      const id = edgeKey(e)
      const portType = sourcePortType(doc, registry, e.from.node, e.from.port)
      const stroke = portType ? `var(${PORT_META[portType].token})` : 'var(--color-border-strong)'
      return {
        id,
        source: e.from.node,
        sourceHandle: e.from.port,
        target: e.to.node,
        targetHandle: e.to.port,
        type,
        selected: selection?.kind === 'edge' && selection.id === id,
        animated: !reducedMotion && statuses[e.from.node]?.state === 'running',
        style: { stroke },
        data: { portType },
      }
    })
}

/** Keep React Flow's measurements (and an in-progress drag) from `prev` by id. */
export function mergeFlowNodes(prev: readonly PipelineFlowNode[], next: readonly PipelineFlowNode[]): PipelineFlowNode[] {
  const byId = new Map(prev.map((node) => [node.id, node]))
  return next.map((node) => {
    const old = byId.get(node.id)
    if (!old) return node
    const kept: Partial<PipelineFlowNode> = {}
    if (old.measured !== undefined) kept.measured = old.measured
    if (old.width !== undefined) kept.width = old.width
    if (old.height !== undefined) kept.height = old.height
    if (old.dragging !== undefined) kept.dragging = old.dragging
    return { ...node, ...kept }
  })
}

export function classifyChanges(
  nodeChanges: readonly NodeChange<PipelineFlowNode>[],
  edgeChanges: readonly EdgeChange<PipelineFlowEdge>[],
): ClassifiedChanges {
  const moved: Record<string, { x: number; y: number }> = {}
  const removed: string[] = []
  const removedEdges: string[] = []
  let selected: FlowSelection | null | undefined
  for (const change of nodeChanges) {
    if (change.type === 'position' && change.dragging === false && change.position) {
      moved[change.id] = { x: change.position.x, y: change.position.y }
    } else if (change.type === 'remove') removed.push(change.id)
    else if (change.type === 'select') {
      if (change.selected) selected = { kind: 'node', id: change.id }
      else if (selected === undefined) selected = null
    }
  }
  for (const change of edgeChanges) {
    if (change.type === 'remove') removedEdges.push(change.id)
    else if (change.type === 'select') {
      if (change.selected) selected = { kind: 'edge', id: change.id }
      else if (selected === undefined) selected = null
    }
  }
  return { moved, removed, removedEdges, selected }
}
