// Unit tests for the pure graph operations (UI_DESIGN.md §4.3.4, slice F3): every op returns a new doc with
// `revision + 1` and shares untouched subtrees (deep-frozen inputs are never mutated; a no-op returns the input),
// ids are `n_<type>`, a second instance throws, `canConnect` over 4 output types × every input (self loops and
// cycles refused), single-input replacement, `topoOrder` ties by id, and the Tidy layout.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  GraphError,
  addNode,
  canConnect,
  connect,
  disconnect,
  edgeKey,
  emptyDoc,
  isValidNodeId,
  layeredLayout,
  moveNodes,
  nodeIdFor,
  presentTypes,
  registryFromCatalog,
  removeNodes,
  setCollapsed,
  setMode,
  setValues,
  setViewport,
  templateFromConfig,
  tidyLayout,
  topoOrder,
} from '../../../frontend/src/workflow/engine/index.ts'
import { readFixture } from './_fixtures.mjs'

const R = registryFromCatalog(readFixture('catalog.json'))
const IMAGE_PARTS = { source: 'image', segmentation: false, partExtraction: true }

function deepFreeze(value) {
  if (value && typeof value === 'object' && !Object.isFrozen(value)) {
    Object.freeze(value)
    for (const child of Object.values(value)) deepFreeze(child)
  }
  return value
}

const template = () => deepFreeze(templateFromConfig(IMAGE_PARTS, R, 7))
const ref = (node, port) => ({ node, port })
const edgeKeys = (doc) => doc.graph.edges.map(edgeKey)

/** A doc with one node of every user type plus two locked refine nodes, no edges. */
function allNodes() {
  let doc = emptyDoc()
  for (const type of ['input_image', 'prompt', 'job_result', 'segmentation', 'image_to_3d', 'part_extraction', 'export', 'part_refine', 'mesh_tune']) {
    doc = addNode(doc, R.get(type), { x: 0, y: 0 })
  }
  return deepFreeze(doc)
}

test('addNode: id n_<type>, revision + 1, position stored, input untouched', () => {
  const start = deepFreeze(emptyDoc(3))
  const doc = addNode(start, R.get('image_to_3d'), { x: 12, y: 34 })
  assert.equal(start.graph.nodes.length, 0)
  assert.equal(doc.revision, 4)
  assert.deepEqual(doc.graph.nodes, [{ id: 'n_image_to_3d', type: 'image_to_3d', version: 1, mode: 'active' }])
  assert.deepEqual(doc.layout.positions.n_image_to_3d, { x: 12, y: 34 })
  assert.equal(doc.graph.edges, start.graph.edges, 'edges shared')
  assert.equal(doc.layout.viewport, start.layout.viewport, 'viewport shared')
  assert.ok(isValidNodeId(doc.graph.nodes[0].id))
})

test('addNode: a second instance throws too_many_instances', () => {
  const doc = addNode(emptyDoc(), R.get('export'), { x: 0, y: 0 })
  assert.throws(
    () => addNode(deepFreeze(doc), R.get('export'), { x: 1, y: 1 }),
    (e) => e instanceof GraphError && e.code === 'too_many_instances' && e.message === 'Only one Export node is supported.',
  )
})

test('addNode: locked types start bypassed; binds "none" nodes get their default values', () => {
  const doc = addNode(emptyDoc(), R.get('part_refine'), { x: 0, y: 0 })
  assert.deepEqual(doc.graph.nodes[0], { id: 'n_part_refine', type: 'part_refine', version: 1, mode: 'bypass', values: {} })
  assert.equal(nodeIdFor('ui.mystery'), 'n_ui_mystery')
  assert.equal(nodeIdFor('A'.repeat(60)).length, 32)
})

test('presentTypes lists the doc types', () => {
  assert.deepEqual([...presentTypes(template())].sort(), ['export', 'image_to_3d', 'input_image', 'part_extraction', 'prompt', 'segmentation'])
})

test('removeNodes drops incident edges, positions and collapsed flags; unknown ids are a no-op', () => {
  const start = deepFreeze(setCollapsed(template(), 'n_segmentation', true))
  const doc = removeNodes(start, ['n_segmentation'])
  assert.equal(doc.revision, start.revision + 1)
  assert.equal(doc.graph.nodes.some((n) => n.id === 'n_segmentation'), false)
  assert.equal(edgeKeys(doc).some((k) => k.includes('n_segmentation')), false)
  assert.equal('n_segmentation' in doc.layout.positions, false)
  assert.deepEqual(doc.layout.collapsed, [])
  assert.equal(doc.layout.viewport, start.layout.viewport)
  assert.equal(removeNodes(start, ['n_nothing']), start)
})

test('connect replaces the link into a single-input port; an existing link is a no-op', () => {
  const start = template()
  const doc = connect(start, ref('n_prompt', 'images'), ref('n_image_to_3d', 'images'), R)
  assert.equal(doc.revision, start.revision + 1)
  assert.ok(edgeKeys(doc).includes('n_prompt.images->n_image_to_3d.images'))
  assert.equal(edgeKeys(doc).includes('n_input_image.images->n_image_to_3d.images'), false)
  assert.equal(edgeKeys(doc).filter((k) => k.endsWith('n_image_to_3d.images')).length, 1)
  assert.equal(doc.graph.nodes, start.graph.nodes, 'nodes shared')
  assert.equal(doc.layout, start.layout, 'layout shared')
  assert.equal(connect(doc, ref('n_prompt', 'images'), ref('n_image_to_3d', 'images'), R), doc)
})

test('disconnect by edge key', () => {
  const start = template()
  const key = 'n_part_extraction.parts->n_export.asset'
  const doc = disconnect(start, key)
  assert.equal(edgeKeys(doc).includes(key), false)
  assert.equal(doc.graph.edges.length, start.graph.edges.length - 1)
  assert.equal(disconnect(doc, key), doc)
  assert.equal(edgeKey({ from: ref('a', 'p'), to: ref('b', 'q') }), 'a.p->b.q')
})

test('setMode: needs a bypass rule; locked and unknown types never become active', () => {
  const start = template()
  const bypassed = setMode(start, 'n_part_extraction', 'bypass', R)
  assert.equal(bypassed.graph.nodes.find((n) => n.id === 'n_part_extraction').mode, 'bypass')
  assert.equal(bypassed.graph.edges, start.graph.edges)
  assert.equal(bypassed.revision, start.revision + 1)
  assert.equal(setMode(start, 'n_input_image', 'bypass', R), start, 'sources have no bypass rule')
  assert.equal(setMode(start, 'n_image_to_3d', 'bypass', R), start)
  assert.equal(setMode(start, 'n_part_extraction', 'active', R), start, 'unchanged mode')
  const locked = deepFreeze(addNode(start, R.get('mesh_tune'), { x: 0, y: 0 }))
  assert.equal(setMode(locked, 'n_mesh_tune', 'active', R), locked)
  const unknown = deepFreeze({ ...start, graph: { ...start.graph, nodes: [...start.graph.nodes, { id: 'n_x', type: 'ui.x', version: 1, mode: 'bypass' }] } })
  assert.equal(setMode(unknown, 'n_x', 'active', R), unknown)
})

test('moveNodes, setCollapsed, setViewport share the graph; no-ops return the input', () => {
  const start = template()
  const moved = moveNodes(start, { n_export: { x: 1000, y: 300 }, n_missing: { x: 1, y: 1 }, n_prompt: { x: Number.NaN, y: 0 } })
  assert.deepEqual(moved.layout.positions.n_export, { x: 1000, y: 300 })
  assert.deepEqual(moved.layout.positions.n_prompt, start.layout.positions.n_prompt)
  assert.equal(moved.graph, start.graph)
  assert.equal(moved.layout.viewport, start.layout.viewport)
  assert.equal(moveNodes(start, { n_export: { ...start.layout.positions.n_export } }), start)

  const collapsed = setCollapsed(start, 'n_export', true)
  assert.deepEqual(collapsed.layout.collapsed, ['n_export'])
  assert.equal(collapsed.graph, start.graph)
  assert.equal(setCollapsed(collapsed, 'n_export', true), collapsed)
  assert.deepEqual(setCollapsed(collapsed, 'n_export', false).layout.collapsed, [])

  const viewport = setViewport(start, { x: 1, y: 2, zoom: 1.5 })
  assert.deepEqual(viewport.layout.viewport, { x: 1, y: 2, zoom: 1.5 })
  assert.equal(viewport.layout.positions, start.layout.positions)
  assert.equal(setViewport(start, { ...start.layout.viewport }), start)
})

test('setValues only on binds "none" nodes', () => {
  const start = deepFreeze(addNode(template(), R.get('part_refine'), { x: 0, y: 0 }))
  const doc = setValues(start, 'n_part_refine', { strength: 2 }, R)
  assert.deepEqual(doc.graph.nodes.find((n) => n.id === 'n_part_refine').values, { strength: 2 })
  assert.equal(setValues(start, 'n_image_to_3d', { resolution: 1536 }, R), start, 'built-ins bind to the stores')
})

test('every op increments the revision by exactly one', () => {
  let doc = template()
  const ops = [
    (d) => addNode(d, R.get('job_result'), { x: 0, y: 500 }),
    (d) => connect(d, ref('n_job_result', 'images'), ref('n_segmentation', 'images'), R),
    (d) => disconnect(d, 'n_job_result.images->n_segmentation.images'),
    (d) => setMode(d, 'n_segmentation', 'active', R),
    (d) => moveNodes(d, { n_job_result: { x: 5, y: 5 } }),
    (d) => setCollapsed(d, 'n_job_result', true),
    (d) => setViewport(d, { x: 0, y: 0, zoom: 1 }),
    (d) => removeNodes(d, ['n_job_result']),
    (d) => tidyLayout(d),
  ]
  for (const op of ops) {
    const next = op(deepFreeze(doc))
    assert.equal(next.revision, doc.revision + 1)
    doc = next
  }
})

test('canConnect: 4 output types × every input, self loops refused', () => {
  const doc = allNodes()
  const outputs = [ref('n_input_image', 'images'), ref('n_segmentation', 'masks'), ref('n_image_to_3d', 'mesh'), ref('n_part_extraction', 'parts')]
  const outType = { images: 'image_set', masks: 'segmentation', mesh: 'mesh', parts: 'part_set' }
  const inputs = [
    [ref('n_segmentation', 'images'), ['image_set']],
    [ref('n_image_to_3d', 'images'), ['image_set']],
    [ref('n_part_extraction', 'mesh'), ['mesh']],
    [ref('n_part_extraction', 'masks'), ['segmentation']],
    [ref('n_export', 'asset'), ['mesh', 'part_set']],
    [ref('n_part_refine', 'parts'), ['part_set']],
  ]
  let checked = 0
  for (const from of outputs) {
    for (const [to, accepts] of inputs) {
      const result = canConnect(doc, R, from, to)
      checked += 1
      const label = `${from.node}.${from.port} → ${to.node}.${to.port}`
      if (from.node === to.node) assert.deepEqual([result.ok, result.code], [false, 'cycle'], label)
      else if (accepts.includes(outType[from.port])) assert.deepEqual(result, { ok: true }, label)
      else assert.deepEqual([result.ok, result.code], [false, 'type_mismatch'], label)
    }
  }
  assert.equal(checked, 24)
  assert.deepEqual(canConnect(doc, R, ref('n_image_to_3d', 'mesh'), ref('n_segmentation', 'images')), {
    ok: false,
    code: 'type_mismatch',
    message: 'Image-to-3D → Segmentation: Mesh cannot connect to Image.',
  })
  assert.equal(canConnect(doc, R, ref('n_part_refine', 'parts'), ref('n_part_refine', 'parts')).code, 'cycle')
})

test('canConnect refuses cycles and unknown ports or nodes', () => {
  let doc = allNodes()
  doc = connect(doc, ref('n_part_refine', 'parts'), ref('n_mesh_tune', 'parts'), R)
  assert.deepEqual(canConnect(doc, R, ref('n_mesh_tune', 'parts'), ref('n_part_refine', 'parts')), {
    ok: false,
    code: 'cycle',
    message: 'This link would create a loop.',
  })
  assert.equal(canConnect(doc, R, ref('n_image_to_3d', 'nope'), ref('n_export', 'asset')).code, 'unknown_port')
  assert.equal(canConnect(doc, R, ref('n_image_to_3d', 'mesh'), ref('n_export', 'nope')).message, 'Export: unknown port "nope".')
  assert.equal(canConnect(doc, R, ref('n_gone', 'mesh'), ref('n_export', 'asset')).code, 'unknown_port')
})

test('topoOrder: Kahn with ties broken by id; cycle members are left out', () => {
  const node = (id) => ({ id, type: 't', version: 1, mode: 'active' })
  const e = (a, b) => ({ from: ref(a, 'o'), to: ref(b, 'i') })
  assert.deepEqual(topoOrder({ nodes: [node('b'), node('c'), node('a')], edges: [] }), ['a', 'b', 'c'])
  assert.deepEqual(topoOrder({ nodes: [node('z'), node('b'), node('a'), node('m')], edges: [e('z', 'm'), e('b', 'm'), e('a', 'z')] }), ['a', 'b', 'z', 'm'])
  // `d` becomes ready after `a`, before `x` (ready list stays sorted).
  assert.deepEqual(topoOrder({ nodes: [node('x'), node('a'), node('d')], edges: [e('a', 'd')] }), ['a', 'd', 'x'])
  assert.deepEqual(topoOrder({ nodes: [node('a'), node('b'), node('c')], edges: [e('a', 'b'), e('b', 'a'), e('c', 'c')] }), [])
  assert.deepEqual(topoOrder({ nodes: [node('a')], edges: [e('a', 'ghost')] }), ['a'])
})

test('layeredLayout / tidyLayout: columns by depth, 320 × 260 grid', () => {
  const doc = template()
  const positions = layeredLayout(doc.graph)
  assert.deepEqual(positions.n_input_image, { x: 0, y: 0 })
  assert.deepEqual(positions.n_prompt, { x: 0, y: 260 })
  assert.equal(positions.n_image_to_3d.x, 320)
  assert.equal(positions.n_segmentation.x, 320)
  assert.equal(positions.n_part_extraction.x, 640)
  assert.equal(positions.n_export.x, 960)
  const tidy = tidyLayout(doc)
  assert.deepEqual(tidy.layout.positions, positions)
  assert.equal(tidy.graph, doc.graph)
})
