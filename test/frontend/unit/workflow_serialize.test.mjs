// Unit tests for workflow persistence and files (UI_DESIGN.md §4.3.10, slice F3): round trip, a newer or unreadable
// stored doc → backup + template signal, unknown types kept but bypassed, `migrate` when the definition is newer,
// tolerant normalisation (ids, edges, clamped layout), the import limits, exported files without user data, and
// `fromServerGraph` for catalog templates and stored run graphs.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { normalizeFeatures } from '../../../frontend/src/lib/featureFlags.ts'
import {
  LAYOUT_COORD_LIMIT,
  SERIALIZE_MESSAGES,
  WORKFLOW_FILE_MAX_BYTES,
  compileDoc,
  exportWorkflowFile,
  fromServerGraph,
  importWorkflowFile,
  loadPersistedDoc,
  normalizeDoc,
  registryFromCatalog,
  stripBoundValues,
  templateFromConfig,
  validateGraph,
} from '../../../frontend/src/workflow/engine/index.ts'
import { NODE_OVERLAYS } from '../../../frontend/src/workflow/nodes/index.ts'
import { readFixture, readFixtureText } from './_fixtures.mjs'

const CATALOG = readFixture('catalog.json')
const R = registryFromCatalog(CATALOG)
const IMAGE_PARTS = { source: 'image', segmentation: false, partExtraction: true }
const doc = () => ({ ...templateFromConfig(IMAGE_PARTS, R), revision: 5 })

test('round trip: export → import and persisted {doc} → load give the same doc', () => {
  const original = doc()
  const text = exportWorkflowFile(original, R, '1.2.3')
  const file = JSON.parse(text)
  assert.deepEqual(Object.keys(file), ['schema', 'version', 'app', 'doc'])
  assert.equal(file.schema, 'img3d.ui_workflow')
  assert.equal(file.version, 1)
  assert.equal(file.app, 'img3d-frontend@1.2.3')
  const imported = importWorkflowFile(text, R)
  assert.equal(imported.ok, true)
  assert.deepEqual(imported.doc, original)
  assert.deepEqual(imported.issues, [])

  for (const raw of [JSON.stringify({ doc: original }), { doc: original }, { state: { doc: original }, version: 1 }, original]) {
    const loaded = loadPersistedDoc(raw, R)
    assert.equal(loaded.kind, 'ok')
    assert.deepEqual(loaded.doc, original)
  }
})

test('nothing stored → empty; newer version or unreadable JSON → reset with the raw text as backup', () => {
  assert.deepEqual(loadPersistedDoc(null, R), { kind: 'empty' })
  assert.deepEqual(loadPersistedDoc(undefined, R), { kind: 'empty' })
  assert.deepEqual(loadPersistedDoc('', R), { kind: 'empty' })
  assert.deepEqual(loadPersistedDoc({ doc: null }, R), { kind: 'empty' })

  const newer = JSON.stringify({ doc: { ...doc(), version: 2 } })
  assert.deepEqual(loadPersistedDoc(newer, R), { kind: 'reset', reason: 'newer_version', backup: newer })
  assert.deepEqual(loadPersistedDoc('{not json', R), { kind: 'reset', reason: 'unreadable', backup: '{not json' })
  const wrongSchema = { doc: { ...doc(), schema: 'something.else' } }
  const reset = loadPersistedDoc(wrongSchema, R)
  assert.equal(reset.kind, 'reset')
  assert.equal(reset.reason, 'unreadable')
  assert.equal(reset.backup, JSON.stringify(wrongSchema))
  assert.equal(SERIALIZE_MESSAGES.restored, 'The saved workflow was made by a newer version; the default workflow was restored.')
})

test('unknown node types are kept, forced to bypass and reported (validation: warning, never blocking)', () => {
  const stored = doc()
  const withUnknown = {
    ...stored,
    graph: {
      nodes: [...stored.graph.nodes, { id: 'n_mystery', type: 'ui.mystery', version: 3, mode: 'active', values: { a: 1 } }],
      edges: [...stored.graph.edges, { from: { node: 'n_part_extraction', port: 'parts' }, to: { node: 'n_mystery', port: 'in' } }],
    },
  }
  const loaded = loadPersistedDoc({ doc: withUnknown }, R)
  assert.equal(loaded.kind, 'ok')
  const node = loaded.doc.graph.nodes.find((n) => n.id === 'n_mystery')
  assert.deepEqual(node, { id: 'n_mystery', type: 'ui.mystery', version: 3, mode: 'bypass', values: { a: 1 } })
  assert.deepEqual(loaded.issues.map((i) => [i.severity, i.code, i.nodeId, i.message]), [
    ['warning', 'unknown_node_type', 'n_mystery', 'Unknown node type "ui.mystery". It is ignored.'],
  ])
  const features = normalizeFeatures(readFixture('features/full.json'))
  const bound = { imageCount: 1, referenceSetId: 'rs_0123456789ab', promptLength: 1, promptMax: 2000, viewedJobCompleted: false }
  const issues = validateGraph(loaded.doc, R, { features, bound })
  assert.equal(issues.some((i) => i.severity === 'error'), false, JSON.stringify(issues))
})

test('migrate runs when the definition version is higher (and only then)', () => {
  const calls = []
  const catalog = structuredClone(CATALOG)
  catalog.node_types.push({ ...structuredClone(catalog.node_types.find((n) => n.type === 'part_refine')), type: 'ui_blur', category: 'parts', locked: false, version: 2, defaults: { radius_px: 1 } })
  const overlays = {
    ...NODE_OVERLAYS,
    ui_blur: {
      binds: 'none',
      isSource: false,
      bypass: { from: 'parts', to: 'parts' },
      description: '',
      migrate: (values, fromVersion) => {
        calls.push([values, fromVersion])
        return { radius_px: values.radius * 2 }
      },
    },
  }
  const r = registryFromCatalog(catalog, overlays)
  const base = doc()
  const old = { ...base, graph: { ...base.graph, nodes: [...base.graph.nodes, { id: 'n_ui_blur', type: 'ui_blur', version: 1, mode: 'bypass', values: { radius: 3 } }] } }
  const result = normalizeDoc(old, r)
  assert.equal(result.ok, true)
  assert.deepEqual(result.doc.graph.nodes.at(-1), { id: 'n_ui_blur', type: 'ui_blur', version: 2, mode: 'bypass', values: { radius_px: 6 } })
  assert.deepEqual(calls, [[{ radius: 3 }, 1]])

  calls.length = 0
  normalizeDoc(result.doc, r)
  assert.deepEqual(calls, [], 'current version: no migration')
})

test('normalisation: invalid / duplicate ids and dangling edges dropped, layout clamped and completed', () => {
  const base = doc()
  const messy = {
    ...base,
    revision: -3,
    graph: {
      nodes: [
        ...base.graph.nodes,
        { id: 'Bad Id', type: 'export', version: 1, mode: 'active' },
        { id: 'n_export', type: 'export', version: 1, mode: 'active' },
        { id: 'n_zz', type: 42 },
        'garbage',
      ],
      edges: [
        ...base.graph.edges,
        base.graph.edges[0],
        { from: { node: 'n_gone', port: 'images' }, to: { node: 'n_image_to_3d', port: 'images' } },
        { from: { node: 'n_input_image' }, to: { node: 'n_image_to_3d', port: 'images' } },
      ],
    },
    layout: {
      positions: { ...base.layout.positions, n_export: { x: 1e9, y: -Infinity }, n_prompt: 'nope', n_gone: { x: 1, y: 1 } },
      collapsed: ['n_export', 'n_export', 'n_gone', 7],
      viewport: { x: Number.NaN, y: 5, zoom: 40 },
    },
  }
  const result = normalizeDoc(messy, R)
  assert.equal(result.ok, true)
  const out = result.doc
  assert.equal(out.revision, 0)
  assert.deepEqual(out.graph.nodes.map((n) => n.id), base.graph.nodes.map((n) => n.id))
  assert.deepEqual(out.graph.edges, base.graph.edges)
  assert.deepEqual(out.layout.positions.n_export, { x: LAYOUT_COORD_LIMIT, y: 0 })
  assert.deepEqual(out.layout.positions.n_prompt, { x: 0, y: 260 }, 'missing position → template slot')
  assert.equal('n_gone' in out.layout.positions, false)
  assert.deepEqual(out.layout.collapsed, ['n_export'])
  assert.deepEqual(out.layout.viewport, { x: 0, y: 5, zoom: 2 })
})

test('built-in nodes never keep values (no ids, prompts or tokens in stored or exported files)', () => {
  const base = doc()
  const leaky = {
    ...base,
    graph: {
      ...base.graph,
      nodes: base.graph.nodes.map((n) =>
        n.type === 'input_image'
          ? { ...n, values: { reference_set_id: 'rs_0123456789ab', primary_image_id: 'img_0123456789ab' } }
          : n.type === 'prompt'
            ? { ...n, values: { prompt: 'a knight with a red cape and a sword', token: 'secret-token' } }
            : n,
      ),
    },
  }
  const text = exportWorkflowFile(leaky, R, '0.0.0')
  for (const needle of ['rs_', 'img_', 'job_', 'knight', 'secret-token', 'values']) assert.equal(text.includes(needle), false, needle)
  assert.deepEqual(stripBoundValues(leaky, R), base)
  assert.equal(stripBoundValues(base, R), base, 'nothing to strip → same doc')
  const normalized = normalizeDoc(leaky, R)
  assert.equal(JSON.stringify(normalized.doc).includes('knight'), false)

  // Compiling with bound user data never writes it back into the doc.
  const features = normalizeFeatures(readFixture('features/full.json'))
  const g7 = JSON.parse(readFixtureText('requests/G7.json'))
  const params = Object.fromEntries(g7.graph.nodes.map((n) => [n.type, n.params]))
  const bound = {
    input_image: params.input_image,
    prompt: null,
    job_result: { job_id: null },
    segmentation: {},
    image_to_3d: params.image_to_3d,
    part_extraction: params.part_extraction,
    export: params.export,
  }
  const before = JSON.stringify(base)
  compileDoc(base, R, bound, { features, bound: { imageCount: 1, referenceSetId: 'rs_0123456789ab', promptLength: 0, promptMax: 2000, viewedJobCompleted: false } })
  assert.equal(JSON.stringify(base), before)
  assert.equal(exportWorkflowFile(base, R, '0.0.0').includes('rs_'), false)
})

test('import limits: 256 KB text, maxNodes / maxEdges; malformed and newer files are refused without throwing', () => {
  const big = ' '.repeat(WORKFLOW_FILE_MAX_BYTES + 1)
  const tooBig = importWorkflowFile(big, R)
  assert.equal(tooBig.ok, false)
  assert.equal(tooBig.message, 'The workflow file is too large (max 256 KB).')
  assert.deepEqual(tooBig.issues.map((i) => i.code), ['graph_too_large'])

  const text = exportWorkflowFile(doc(), R, '0.0.0')
  const nodes = importWorkflowFile(text, R, { maxNodes: 5, maxEdges: 64 })
  assert.equal(nodes.ok, false)
  assert.equal(nodes.message, 'The workflow is too large (max 5 nodes, 64 links).')
  const edges = importWorkflowFile(text, R, { maxNodes: 32, maxEdges: 4 })
  assert.equal(edges.ok, false)
  assert.equal(edges.issues.at(-1).code, 'graph_too_large')
  assert.equal(importWorkflowFile(text, R, { maxNodes: 6, maxEdges: 5 }).ok, true, 'exactly at the limits')

  assert.deepEqual(importWorkflowFile('{oops', R), { ok: false, message: SERIALIZE_MESSAGES.notAWorkflow, issues: [] })
  assert.deepEqual(importWorkflowFile('{"hello":1}', R), { ok: false, message: SERIALIZE_MESSAGES.notAWorkflow, issues: [] })
  const newerFile = JSON.stringify({ schema: 'img3d.ui_workflow', version: 2, app: 'img3d-frontend@9', doc: doc() })
  assert.equal(importWorkflowFile(newerFile, R).message, SERIALIZE_MESSAGES.newerFile)
  const newerDoc = JSON.stringify({ schema: 'img3d.ui_workflow', version: 1, app: 'x', doc: { ...doc(), version: 3 } })
  assert.equal(importWorkflowFile(newerDoc, R).message, SERIALIZE_MESSAGES.newerFile)
  // A bare doc (no file wrapper) is accepted too.
  assert.equal(importWorkflowFile(JSON.stringify(doc()), R).ok, true)
})

test('fromServerGraph: catalog templates → active nodes at their template positions, params kept apart', () => {
  for (const template of CATALOG.templates) {
    const result = fromServerGraph(template.graph, null, R)
    assert.deepEqual(result.doc.graph.nodes.map((n) => n.id), template.graph.nodes.map((n) => n.id), template.id)
    assert.ok(result.doc.graph.nodes.every((n) => n.mode === 'active' && n.values === undefined), template.id)
    assert.equal(result.doc.graph.edges.length, template.graph.edges.length, template.id)
    assert.deepEqual(result.issues, [])
    for (const node of result.doc.graph.nodes) assert.ok(result.doc.layout.positions[node.id], `${template.id}: ${node.id} placed`)
    assert.deepEqual(Object.keys(result.params), template.graph.nodes.map((n) => n.id))
  }
  const parts = fromServerGraph(CATALOG.templates.find((t) => t.id === 'image_to_3d_parts').graph, null, R)
  assert.deepEqual(parts.doc.layout.positions, {
    n_input_image: { x: 0, y: 0 },
    n_image_to_3d: { x: 320, y: 260 },
    n_part_extraction: { x: 640, y: 130 },
    n_export: { x: 960, y: 260 },
  })
})

test('fromServerGraph: a stored run graph (G7) keeps its layout; params never enter the doc', () => {
  const g7 = JSON.parse(readFixtureText('requests/G7.json'))
  const result = fromServerGraph(g7.graph, g7.layout, R)
  assert.deepEqual(result.doc.layout.positions, g7.layout.positions)
  assert.deepEqual(result.doc.layout.viewport, g7.layout.viewport)
  assert.equal(JSON.stringify(result.doc).includes('rs_0123456789ab'), false)
  assert.deepEqual(result.params.n_input_image, { reference_set_id: 'rs_0123456789ab', primary_image_id: 'img_0123456789ab' })
  assert.deepEqual(result.params.n_export, { per_part_glbs: false })
  // Unknown server types are kept bypassed; garbage input gives an empty doc.
  const unknown = fromServerGraph({ ...g7.graph, nodes: [...g7.graph.nodes, { id: 'n_future', type: 'future_node', params: {} }] }, null, R)
  assert.equal(unknown.doc.graph.nodes.at(-1).mode, 'bypass')
  assert.deepEqual(unknown.issues.map((i) => i.code), ['unknown_node_type'])
  assert.deepEqual(fromServerGraph(null, null, R).doc.graph, { nodes: [], edges: [] })
})
