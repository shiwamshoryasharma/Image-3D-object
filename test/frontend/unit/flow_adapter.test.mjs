// Unit tests for the React Flow adapter (UI_DESIGN.md §4.3.12, slice F11): `toFlowNodes` carries topology only and the
// selection; `toFlowEdges` uses `edgeKey` ids, the source-port colour token, `animated` only for a running source when
// motion is allowed, and drops links to missing nodes; `mergeFlowNodes` keeps React Flow's measurements;
// `classifyChanges` maps drag stop / remove / select changes (in-progress drags stay local).
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { classifyChanges, mergeFlowNodes, sourcePortType, toFlowEdges, toFlowNodes } from '../../../frontend/src/shells/node/canvas/flowAdapter.ts'
import { edgeKey, registryFromCatalog, templateFromConfig } from '../../../frontend/src/workflow/engine/index.ts'
import { readFixture } from './_fixtures.mjs'

const REGISTRY = registryFromCatalog(readFixture('catalog.json'))
const DOC = templateFromConfig({ source: 'image', segmentation: true, partExtraction: true }, REGISTRY)
const idle = { state: 'idle', percent: null, message: null, thumbnailUrl: null }
const running = { state: 'running', percent: 40, message: null, thumbnailUrl: null }

test('toFlowNodes: one pipeline node per graph node, topology only, positions from the layout', () => {
  const nodes = toFlowNodes(DOC, REGISTRY, null)
  assert.equal(nodes.length, DOC.graph.nodes.length)
  for (const node of nodes) {
    const source = DOC.graph.nodes.find((n) => n.id === node.id)
    assert.ok(source)
    assert.equal(node.type, 'pipeline')
    assert.deepEqual(node.data, { id: source.id, type: source.type })
    assert.deepEqual(node.position, DOC.layout.positions[node.id])
    assert.equal(node.selected, false)
    assert.equal('status' in node.data, false, 'run status never goes through nodes')
  }
})

test('toFlowNodes: the selected node (and only a node selection) is marked', () => {
  const id = DOC.graph.nodes[0].id
  assert.deepEqual(toFlowNodes(DOC, REGISTRY, { kind: 'node', id }).filter((n) => n.selected).map((n) => n.id), [id])
  assert.equal(toFlowNodes(DOC, REGISTRY, { kind: 'edge', id }).some((n) => n.selected), false)
})

test('toFlowEdges: edgeKey ids, handles, source-port colour token and edge type per link style', () => {
  const edges = toFlowEdges(DOC, REGISTRY, {}, false, null, 'smoothstep')
  assert.equal(edges.length, DOC.graph.edges.length)
  for (const [index, edge] of edges.entries()) {
    const e = DOC.graph.edges[index]
    assert.equal(edge.id, edgeKey(e))
    assert.deepEqual([edge.source, edge.sourceHandle, edge.target, edge.targetHandle], [e.from.node, e.from.port, e.to.node, e.to.port])
    assert.equal(edge.type, 'smoothstep')
    const portType = sourcePortType(DOC, REGISTRY, e.from.node, e.from.port)
    assert.ok(portType, `${edge.id}: source port type known`)
    assert.equal(edge.data.portType, portType)
    assert.match(edge.style.stroke, /^var\(--port-(image|masks|mesh|parts)\)$/)
    assert.equal(edge.animated, false)
    assert.equal(edge.selected, false)
  }
  assert.equal(toFlowEdges(DOC, REGISTRY, {}, false, null)[0].type, 'default', 'bezier → React Flow default edge')
})

test('toFlowEdges: animated only while the source runs and motion is allowed; selection by edgeKey', () => {
  const first = DOC.graph.edges[0]
  const statuses = { [first.from.node]: running }
  const animated = toFlowEdges(DOC, REGISTRY, statuses, false, null).filter((e) => e.animated)
  assert.ok(animated.length >= 1)
  assert.ok(animated.every((e) => e.source === first.from.node))
  assert.equal(toFlowEdges(DOC, REGISTRY, statuses, true, null).some((e) => e.animated), false, 'reduced motion')
  assert.equal(toFlowEdges(DOC, REGISTRY, { [first.from.node]: idle }, false, null).some((e) => e.animated), false)
  const key = edgeKey(first)
  assert.deepEqual(toFlowEdges(DOC, REGISTRY, {}, false, { kind: 'edge', id: key }).filter((e) => e.selected).map((e) => e.id), [key])
})

test('toFlowEdges: links to a missing node are dropped', () => {
  const broken = { ...DOC, graph: { ...DOC.graph, edges: [...DOC.graph.edges, { from: { node: 'n_missing', port: 'mesh' }, to: { node: 'n_export', port: 'asset' } }] } }
  assert.equal(toFlowEdges(broken, REGISTRY, {}, false, null).length, DOC.graph.edges.length)
})

test('mergeFlowNodes: keeps measured/width/height/dragging by id, takes position and selection from next', () => {
  const prev = toFlowNodes(DOC, REGISTRY, null).map((n, i) => (i === 0 ? { ...n, measured: { width: 236, height: 120 }, width: 236, height: 120, dragging: false } : n))
  const moved = { ...DOC, layout: { ...DOC.layout, positions: { ...DOC.layout.positions, [prev[0].id]: { x: 999, y: 7 } } } }
  const next = toFlowNodes(moved, REGISTRY, { kind: 'node', id: prev[0].id })
  const merged = mergeFlowNodes(prev, next)
  assert.deepEqual(merged[0].measured, { width: 236, height: 120 })
  assert.equal(merged[0].width, 236)
  assert.equal(merged[0].dragging, false)
  assert.deepEqual(merged[0].position, { x: 999, y: 7 })
  assert.equal(merged[0].selected, true)
  assert.equal('measured' in merged[1], false, 'nothing invented for unmeasured nodes')
  assert.equal(mergeFlowNodes(prev, next.slice(1)).length, next.length - 1, 'removed nodes are dropped')
})

test('classifyChanges: drag stop moves, in-progress drag and dimensions stay local, remove and select', () => {
  const c = classifyChanges(
    [
      { id: 'n_a', type: 'position', position: { x: 1, y: 2 }, dragging: true },
      { id: 'n_b', type: 'position', position: { x: 3, y: 4 }, dragging: false },
      { id: 'n_c', type: 'dimensions', dimensions: { width: 10, height: 10 } },
      { id: 'n_d', type: 'remove' },
      { id: 'n_e', type: 'select', selected: false },
      { id: 'n_f', type: 'select', selected: true },
    ],
    [],
  )
  assert.deepEqual(c.moved, { n_b: { x: 3, y: 4 } })
  assert.deepEqual(c.removed, ['n_d'])
  assert.deepEqual(c.removedEdges, [])
  assert.deepEqual(c.selected, { kind: 'node', id: 'n_f' })
})

test('classifyChanges: deselect only → null; no select change → undefined; edges', () => {
  assert.equal(classifyChanges([{ id: 'n_a', type: 'select', selected: false }], []).selected, null)
  assert.equal(classifyChanges([{ id: 'n_a', type: 'position', position: { x: 0, y: 0 }, dragging: true }], []).selected, undefined)
  const key = edgeKey(DOC.graph.edges[0])
  const e = classifyChanges([], [{ id: key, type: 'select', selected: true }, { id: 'x', type: 'remove' }])
  assert.deepEqual(e.selected, { kind: 'edge', id: key })
  assert.deepEqual(e.removedEdges, ['x'])
  assert.deepEqual(e.moved, {})
})
