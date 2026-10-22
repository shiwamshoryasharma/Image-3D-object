/**
 * React Flow canvas (UI_DESIGN §4.3.12; lazy: the List view never downloads @xyflow). Controlled mode with the
 * measurements kept: local `nodes` state re-derived during render whenever the sync key (doc revision, selection,
 * catalog ETag) changes, merged so React Flow keeps `measured`/`width`/`height`/`dragging`. Edges are derived every
 * render (no measured state). Status reaches the custom nodes through `NodeStatusContext`.
 */
import { useEffect, useRef, useState, type PointerEvent as ReactPointerEvent } from 'react'
import {
  applyNodeChanges,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  ReactFlow,
  type Connection,
  type EdgeChange,
  type FinalConnectionState,
  type IsValidConnection,
  type NodeChange,
  type ReactFlowInstance,
  type Viewport as FlowViewport,
} from '@xyflow/react'
import '@xyflow/react/dist/base.css'

import { useViewport } from '../../../hooks/useViewport'
import { useFeaturesStore } from '../../../store/featuresStore'
import { usePreferencesStore } from '../../../store/preferencesStore'
import { useWorkflowStore, type PaletteRequest, type WorkflowSelection } from '../../../store/workflowStore'
import { canConnect } from '../../../workflow/engine/graph.ts'
import type { NodeRegistry } from '../../../workflow/engine/registry.ts'
import { CATEGORY_META, type NodeStatusView, type PortRef, type WorkflowDoc } from '../../../workflow/engine/types.ts'
import { registerCanvasFit } from '../canvasControls'
import {
  classifyChanges,
  mergeFlowNodes,
  sourcePortType,
  toFlowEdges,
  toFlowNodes,
  type FlowSelection,
  type PipelineFlowEdge,
  type PipelineFlowNode,
} from './flowAdapter.ts'
import { nodeTypes } from './nodeTypes'
import { NodeIssueContext, NodeStatusContext } from './statusContext'
import styles from './flow.module.css'

const LONG_PRESS_MS = 500
const LONG_PRESS_SLOP = 8
const SNAP_GRID: [number, number] = [16, 16]
const MINIMAP_MIN_WIDTH = 840
/** Grid like a node editor's: minor lines every snap cell pair, a stronger line every 5. */
const GRID_MINOR = 32
const FIT_VIEW_OPTIONS = { padding: 0.18, maxZoom: 1 }
const minimapColor = (registry: NodeRegistry, type: string): string => {
  const def = registry.get(type)
  return def ? `var(${CATEGORY_META[def.category].token})` : 'var(--color-surface-4)'
}
const ARIA_LABELS = {
  'node.a11yDescription.default': 'Press Enter to edit this node, the arrow keys to move it, Delete to remove it.',
  'node.a11yDescription.keyboardDisabled': 'Press Enter to edit this node.',
  'edge.a11yDescription.default': 'Press Delete to remove this link.',
  'controls.ariaLabel': 'Canvas controls',
  'controls.zoomIn.ariaLabel': 'Zoom in',
  'controls.zoomOut.ariaLabel': 'Zoom out',
  'controls.fitView.ariaLabel': 'Fit the workflow to the view',
  'minimap.ariaLabel': 'Workflow overview',
  'handle.ariaLabel': 'Port',
}

export interface NodeCanvasProps {
  doc: WorkflowDoc
  registry: NodeRegistry
  selection: WorkflowSelection | null
  statuses: Readonly<Record<string, NodeStatusView>>
  issueCounts: Readonly<Record<string, number>>
  reducedMotion: boolean
  /** Id of the graph summary (`aria-describedby`). */
  describedBy: string
  /** A node was selected on the canvas (opens the Inspector sheet on medium widths). */
  onNodeSelected?: (id: string) => void
}

const isPane = (target: EventTarget | null): boolean =>
  target instanceof Element && target.classList.contains('react-flow__pane')

export default function NodeCanvas({ doc, registry, selection, statuses, issueCounts, reducedMotion, describedBy, onNodeSelected }: NodeCanvasProps) {
  const catalogEtag = useFeaturesStore((s) => s.catalogEtag)
  const minimap = usePreferencesStore((s) => s.node.minimap)
  const snapToGrid = usePreferencesStore((s) => s.node.snapToGrid)
  const linkStyle = usePreferencesStore((s) => s.node.linkStyle)
  const { width, coarse } = useViewport()
  const instance = useRef<ReactFlowInstance<PipelineFlowNode, PipelineFlowEdge> | null>(null)
  const press = useRef<{ x: number; y: number; timer: number } | null>(null)

  const syncKey = `${doc.revision}|${selection?.kind ?? ''}:${selection?.id ?? ''}|${catalogEtag ?? ''}`
  const [nodes, setNodes] = useState(() => toFlowNodes(doc, registry, selection))
  const [syncedKey, setSyncedKey] = useState(syncKey)
  if (syncKey !== syncedKey) {
    setSyncedKey(syncKey)
    setNodes((prev) => mergeFlowNodes(prev, toFlowNodes(doc, registry, selection)))
  }
  const edges = toFlowEdges(doc, registry, statuses, reducedMotion, selection, linkStyle)

  useEffect(() => {
    registerCanvasFit(() => void instance.current?.fitView({ padding: 0.15, duration: reducedMotion ? 0 : 200 }))
    return () => registerCanvasFit(null)
  }, [reducedMotion])

  const forwardSelection = (next: FlowSelection | null | undefined, from: 'node' | 'edge') => {
    if (next === undefined) return
    const store = useWorkflowStore.getState()
    // React Flow reports the deselect of the old element and the select of the new one in separate callbacks.
    if (next === null) {
      if (store.selection?.kind === from) store.select(null)
      return
    }
    store.select(next)
    if (next.kind === 'node') onNodeSelected?.(next.id)
  }

  const onNodesChange = (changes: NodeChange<PipelineFlowNode>[]) => {
    setNodes((current) => applyNodeChanges(changes, current))
    const c = classifyChanges(changes, [])
    const store = useWorkflowStore.getState()
    if (Object.keys(c.moved).length) store.moveNodes(c.moved)
    if (c.removed.length) store.removeNodes(c.removed)
    forwardSelection(c.selected, 'node')
  }

  const onEdgesChange = (changes: EdgeChange<PipelineFlowEdge>[]) => {
    const c = classifyChanges([], changes)
    const store = useWorkflowStore.getState()
    for (const key of c.removedEdges) store.disconnect(key)
    forwardSelection(c.selected, 'edge')
  }

  const refsOf = (c: Connection): { from: PortRef; to: PortRef } | null =>
    c.sourceHandle && c.targetHandle
      ? { from: { node: c.source, port: c.sourceHandle }, to: { node: c.target, port: c.targetHandle } }
      : null

  const onConnect = (c: Connection) => {
    const refs = refsOf(c)
    if (refs) useWorkflowStore.getState().connect(refs.from, refs.to)
  }

  const isValidConnection: IsValidConnection<PipelineFlowEdge> = (c) => {
    const refs = c.sourceHandle && c.targetHandle ? { from: { node: c.source, port: c.sourceHandle }, to: { node: c.target, port: c.targetHandle } } : null
    return refs ? canConnect(useWorkflowStore.getState().doc, registry, refs.from, refs.to).ok : false
  }

  const openPaletteAt = (clientX: number, clientY: number, connectFrom: PaletteRequest['connectFrom'] = null) => {
    const position = instance.current?.screenToFlowPosition({ x: clientX, y: clientY }) ?? null
    const accepts = connectFrom?.side === 'output' ? sourcePortType(doc, registry, connectFrom.node, connectFrom.port) : null
    useWorkflowStore.getState().openPalette({ accepts, position, connectFrom })
  }

  const onConnectEnd = (event: MouseEvent | TouchEvent, state: FinalConnectionState) => {
    if (state.isValid || state.toNode || !state.fromHandle?.id || !isPane(event.target)) return
    const point = 'changedTouches' in event ? event.changedTouches[0] : event
    if (!point) return
    const side = state.fromHandle.type === 'source' ? 'output' : 'input'
    openPaletteAt(point.clientX, point.clientY, { node: state.fromHandle.nodeId, port: state.fromHandle.id, side })
  }

  const cancelPress = () => {
    if (press.current) window.clearTimeout(press.current.timer)
    press.current = null
  }
  const onPointerDown = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerType !== 'touch' || !isPane(event.target)) return
    const { clientX, clientY } = event
    cancelPress()
    press.current = { x: clientX, y: clientY, timer: window.setTimeout(() => openPaletteAt(clientX, clientY), LONG_PRESS_MS) }
  }
  const onPointerMove = (event: ReactPointerEvent<HTMLDivElement>) => {
    const start = press.current
    if (start && Math.hypot(event.clientX - start.x, event.clientY - start.y) > LONG_PRESS_SLOP) cancelPress()
  }

  const onMoveEnd = (_event: MouseEvent | TouchEvent | null, viewport: FlowViewport) => {
    useWorkflowStore.getState().setViewport({ x: Math.round(viewport.x), y: Math.round(viewport.y), zoom: viewport.zoom })
  }

  return (
    <NodeStatusContext value={statuses}>
      <NodeIssueContext value={issueCounts}>
        <div
          className={styles.canvas}
          onDoubleClick={(event) => {
            if (isPane(event.target)) openPaletteAt(event.clientX, event.clientY)
          }}
          onPointerDown={onPointerDown}
          onPointerMove={onPointerMove}
          onPointerUp={cancelPress}
          onPointerCancel={cancelPress}
        >
          <ReactFlow<PipelineFlowNode, PipelineFlowEdge>
            nodes={nodes}
            edges={edges}
            nodeTypes={nodeTypes}
            onNodesChange={onNodesChange}
            onEdgesChange={onEdgesChange}
            onConnect={onConnect}
            onConnectEnd={onConnectEnd}
            isValidConnection={isValidConnection}
            onMoveEnd={onMoveEnd}
            onInit={(flow) => {
              instance.current = flow
            }}
            defaultViewport={doc.layout.viewport}
            fitView
            fitViewOptions={FIT_VIEW_OPTIONS}
            colorMode="dark"
            minZoom={0.25}
            maxZoom={2}
            zoomOnDoubleClick={false}
            zoomOnPinch
            panOnDrag
            deleteKeyCode={null}
            selectionOnDrag={false}
            connectOnClick
            connectionRadius={coarse ? 32 : 20}
            snapToGrid={snapToGrid}
            snapGrid={SNAP_GRID}
            ariaLabelConfig={ARIA_LABELS}
            aria-describedby={describedBy}
          >
            <Background id="grid-minor" variant={BackgroundVariant.Lines} gap={GRID_MINOR} lineWidth={1} />
            <Background
              id="grid-major"
              variant={BackgroundVariant.Lines}
              gap={GRID_MINOR * 5}
              lineWidth={1}
              color="color-mix(in srgb, var(--color-border-strong) 70%, transparent)"
            />
            <Controls showInteractive={false} />
            {minimap && width >= MINIMAP_MIN_WIDTH ? (
              <MiniMap<PipelineFlowNode>
                pannable
                zoomable
                nodeBorderRadius={6}
                nodeColor={(node) => minimapColor(registry, node.data.type)}
                style={{ inlineSize: 150, blockSize: 96 }}
              />
            ) : null}
          </ReactFlow>
        </div>
      </NodeIssueContext>
    </NodeStatusContext>
  )
}
