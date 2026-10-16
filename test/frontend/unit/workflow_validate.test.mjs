// Unit tests for client validation (UI_DESIGN.md §4.3.5, slice F3): every client code with a positive case (a
// `fixtures/graphs/*.json` case with `expected_issues`, exact messages) and negative cases, the scope rules that keep
// parked/skipped nodes from blocking a Run, the legacy registry (capability_unavailable only on active prompt,
// segmentation and part nodes), and the server-report merge.
//
// Fixture format (F0): `{features, limits, bound, doc, expected_issues, exhaustive}`. An expected issue with
// `message: null` is compared by code and severity only (its node/message name several nodes); otherwise code,
// severity, nodeId, port and message must match. `exhaustive: true` = no other issue may appear.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { LEGACY_FEATURES, normalizeFeatures } from '../../../frontend/src/lib/featureFlags.ts'
import {
  BUILTIN_REGISTRY,
  addNode,
  connect,
  disconnect,
  firstError,
  hasErrors,
  mergeIssues,
  registryFromCatalog,
  setMode,
  structuralErrors,
  templateFromConfig,
  validateGraph,
  withNodeStates,
} from '../../../frontend/src/workflow/engine/index.ts'
import { listFixtures, readFixture } from './_fixtures.mjs'

const CATALOG = readFixture('catalog.json')
const SCENARIOS = ['full', 'no-t2i', 'no-seg', 'disabled']
const FEATURES = Object.fromEntries(SCENARIOS.map((s) => [s, normalizeFeatures(readFixture(`features/${s}.json`))]))
const REGISTRY = Object.fromEntries(
  SCENARIOS.map((s) => [s, withNodeStates(registryFromCatalog(CATALOG), readFixture(`features/${s}.json`).nodes)]),
)
const BOUND = { imageCount: 1, referenceSetId: 'rs_0123456789ab', promptLength: 34, promptMax: 2000, viewedJobCompleted: true }
const R = REGISTRY.full

function ctxFor(features, bound = BOUND, limits = null) {
  return { features: limits ? { ...features, workflow: { ...features.workflow, ...limits } } : features, bound }
}

const GRAPHS = listFixtures('graphs').map((name) => readFixture(`graphs/${name}.json`))
const runFixture = (fx) => validateGraph(fx.doc, REGISTRY[fx.features], ctxFor(FEATURES[fx.features], fx.bound, fx.limits))
const codes = (issues) => issues.map((i) => i.code)
const ref = (node, port) => ({ node, port })

function matches(actual, expected) {
  if (actual.code !== expected.code || actual.severity !== expected.severity) return false
  if (expected.message === null) return true
  return actual.nodeId === expected.nodeId && actual.port === expected.port && actual.message === expected.message
}

for (const fx of GRAPHS) {
  test(`graph fixture ${fx.name}: ${fx.description}`, () => {
    const issues = runFixture(fx)
    for (const expected of fx.expected_issues) {
      assert.ok(
        issues.some((actual) => matches(actual, expected)),
        `missing ${JSON.stringify(expected)}\n  got ${JSON.stringify(issues.map(({ code, nodeId, port, message }) => ({ code, nodeId, port, message })))}`,
      )
    }
    if (fx.exhaustive) assert.equal(issues.length, fx.expected_issues.length, JSON.stringify(issues))
    for (const i of issues) {
      assert.equal(i.origin, 'client')
      assert.ok(i.message.length > 0)
    }
  })
}

test('every client code has a positive fixture; the valid fixtures are clean (negatives)', () => {
  const clientCodes = [
    'graph_too_large', 'unknown_node_type', 'unknown_port', 'type_mismatch', 'port_already_connected',
    'missing_required_input', 'no_source', 'multiple_sources', 'cycle', 'too_many_instances', 'node_locked',
    'unsupported_topology', 'capability_unavailable', 'feature_disabled', 'invalid_param', 'no_sink',
    'ui_parked_source', 'ui_unreachable_node',
  ]
  const produced = new Set(GRAPHS.flatMap((fx) => fx.expected_issues.map((i) => i.code)))
  for (const code of clientCodes) assert.ok(produced.has(code), `no fixture for ${code}`)
  for (const name of ['valid_image_to_3d', 'valid_image_parts_segmentation', 'valid_job_result_parts']) {
    const fx = GRAPHS.find((g) => g.name === name)
    assert.deepEqual(runFixture(fx), [], name)
  }
})

test('negatives at the boundaries: limits, prompt length, bypassed locked node, job-mode graph with workflows off', () => {
  const fx = GRAPHS.find((g) => g.name === 'valid_image_to_3d')
  const atLimit = validateGraph(fx.doc, R, ctxFor(FEATURES.full, BOUND, { maxNodes: 3, maxEdges: 2 }))
  assert.deepEqual(atLimit, [])
  const overEdges = validateGraph(fx.doc, R, ctxFor(FEATURES.full, BOUND, { maxNodes: 3, maxEdges: 1 }))
  assert.deepEqual(codes(overEdges), ['graph_too_large'])
  assert.equal(overEdges[0].message, 'The workflow is too large (max 3 nodes, 1 links).')

  const prompt = GRAPHS.find((g) => g.name === 'invalid_param_prompt_too_long')
  assert.deepEqual(validateGraph(prompt.doc, R, ctxFor(FEATURES.full, { ...prompt.bound, promptLength: 2000 })), [])

  // A bypassed (locked) refine node between Part Extraction and Export: passes through, nothing to report.
  const locked = GRAPHS.find((g) => g.name === 'node_locked')
  const bypassed = { ...locked.doc, graph: { ...locked.doc.graph, nodes: locked.doc.graph.nodes.map((n) => (n.type === 'part_refine' ? { ...n, mode: 'bypass' } : n)) } }
  assert.deepEqual(validateGraph(bypassed, R, ctxFor(FEATURES.full)), [])

  // Workflows off: a job-mode graph still runs (POST /jobs), so no feature_disabled.
  assert.deepEqual(validateGraph(fx.doc, REGISTRY.disabled, ctxFor(FEATURES.disabled)), [])
})

test('scope: a parked Prompt on a backend without text-to-image is only a parked source', () => {
  const doc = templateFromConfig({ source: 'image', segmentation: false, partExtraction: true }, REGISTRY['no-t2i'])
  const issues = validateGraph(doc, REGISTRY['no-t2i'], ctxFor(FEATURES['no-t2i'], { ...BOUND, promptLength: 0 }))
  assert.deepEqual(issues.map((i) => [i.code, i.nodeId]), [['ui_parked_source', 'n_prompt']])
  // …and a parked Input Image with no reference images does not block a prompt run.
  const promptDoc = templateFromConfig({ source: 'prompt', segmentation: false, partExtraction: false }, R)
  const promptIssues = validateGraph(promptDoc, R, ctxFor(FEATURES.full, { ...BOUND, imageCount: 0, referenceSetId: null }))
  assert.deepEqual(promptIssues.map((i) => [i.code, i.nodeId]), [['ui_parked_source', 'n_input_image']])
})

test('scope: an Export node with nothing connected is a missing input, not a parked run', () => {
  const fx = GRAPHS.find((g) => g.name === 'valid_image_to_3d')
  const doc = disconnect(fx.doc, 'n_image_to_3d.mesh->n_export.asset')
  const issues = validateGraph(doc, R, ctxFor(FEATURES.full))
  assert.deepEqual(issues.map((i) => [i.code, i.nodeId, i.port, i.message]), [
    ['missing_required_input', 'n_export', 'asset', 'Export: connect "Asset".'],
  ])
})

test('scope: a Segmentation node wired into the run but fed by nothing needs its Image input', () => {
  const fx = GRAPHS.find((g) => g.name === 'valid_image_parts_segmentation')
  const doc = disconnect(fx.doc, 'n_input_image.images->n_segmentation.images')
  const issues = validateGraph(doc, R, ctxFor(FEATURES.full))
  assert.deepEqual(issues.map((i) => [i.code, i.nodeId, i.port]), [['missing_required_input', 'n_segmentation', 'images']])
})

test('unknown node types: an error while active, a warning once bypassed (the import default)', () => {
  const fx = GRAPHS.find((g) => g.name === 'unknown_node_type')
  const bypassed = { ...fx.doc, graph: { ...fx.doc.graph, nodes: fx.doc.graph.nodes.map((n) => (n.id === 'n_mystery' ? { ...n, mode: 'bypass' } : n)) } }
  const issues = validateGraph(bypassed, R, ctxFor(FEATURES.full))
  assert.deepEqual(issues.map((i) => [i.severity, i.code, i.nodeId]), [['warning', 'unknown_node_type', 'n_mystery']])
  assert.equal(hasErrors(issues), false)
})

test('job_result → Image-to-3D: unsupported_topology on the Image-to-3D node, no extra no_source', () => {
  const fx = GRAPHS.find((g) => g.name === 'unsupported_topology_job_result')
  const issues = runFixture(fx)
  assert.deepEqual(issues.filter((i) => i.severity === 'error').map((i) => [i.code, i.nodeId, i.message]), [
    ['unsupported_topology', 'n_image_to_3d', 'Image-to-3D cannot be used here.'],
  ])
})

test('legacy registry: capability_unavailable only on active prompt, segmentation and part nodes', () => {
  const ctx = ctxFor(LEGACY_FEATURES)
  const unavailable = (doc) => validateGraph(doc, BUILTIN_REGISTRY, ctx).filter((i) => i.code === 'capability_unavailable').map((i) => i.nodeId)
  const config = (source, partExtraction, segmentation) => ({ source, partExtraction, segmentation })

  // The trivial graph (parts off, prompt parked, segmentation bypassed) is clean on a legacy backend.
  const trivial = templateFromConfig(config('image', false, false), BUILTIN_REGISTRY)
  assert.deepEqual(validateGraph(trivial, BUILTIN_REGISTRY, ctx).map((i) => i.code), ['ui_parked_source'])
  assert.deepEqual(unavailable(templateFromConfig(config('image', true, false), BUILTIN_REGISTRY)), ['n_part_extraction'])
  assert.deepEqual(unavailable(templateFromConfig(config('image', true, true), BUILTIN_REGISTRY)).sort(), ['n_part_extraction', 'n_segmentation'])
  assert.deepEqual(unavailable(templateFromConfig(config('prompt', false, false), BUILTIN_REGISTRY)), ['n_prompt'])
  assert.deepEqual(unavailable(templateFromConfig(config('job', true, false), BUILTIN_REGISTRY)).sort(), ['n_job_result', 'n_part_extraction'])
  const message = validateGraph(templateFromConfig(config('prompt', false, false), BUILTIN_REGISTRY), BUILTIN_REGISTRY, ctx).find(
    (i) => i.code === 'capability_unavailable',
  ).message
  assert.equal(message, 'Prompt is unavailable: Needs a newer Img3D backend.')
  // No feature_disabled from a non-ready snapshot (the run explains a legacy backend itself).
  assert.equal(codes(validateGraph(templateFromConfig(config('image', true, false), BUILTIN_REGISTRY), BUILTIN_REGISTRY, ctx)).includes('feature_disabled'), false)
})

test('a loading snapshot never claims "Workflows are turned off"', () => {
  const doc = templateFromConfig({ source: 'image', segmentation: false, partExtraction: true }, R)
  const loading = { ...FEATURES.full, status: 'loading', flags: { ...FEATURES.full.flags, workflows: false } }
  assert.equal(codes(validateGraph(doc, R, ctxFor(loading))).includes('feature_disabled'), false)
  const off = { ...FEATURES.full, flags: { ...FEATURES.full.flags, workflows: false } }
  assert.ok(codes(validateGraph(doc, R, ctxFor(off))).includes('feature_disabled'))
})

test('issue order: errors before warnings; firstError is the most structural problem', () => {
  let doc = templateFromConfig({ source: 'image', segmentation: false, partExtraction: true }, R)
  doc = connect(doc, ref('n_prompt', 'images'), ref('n_segmentation', 'images'), R)
  doc = setMode(doc, 'n_segmentation', 'active', R)
  const issues = validateGraph(doc, R, ctxFor(FEATURES.full, { ...BOUND, imageCount: 0, referenceSetId: null }))
  const firstWarning = issues.findIndex((i) => i.severity === 'warning')
  assert.ok(issues.slice(0, firstWarning === -1 ? issues.length : firstWarning).every((i) => i.severity === 'error'))
  assert.equal(firstError(issues).code, 'multiple_sources')
  assert.ok(codes(issues).includes('invalid_param'))
  assert.equal(firstError([]), null)
})

test('structuralErrors ignores bound values, limits and capabilities', () => {
  const fx = GRAPHS.find((g) => g.name === 'invalid_param_no_images')
  assert.deepEqual(structuralErrors(fx.doc, R), [])
  assert.deepEqual(codes(structuralErrors(GRAPHS.find((g) => g.name === 'multiple_sources').doc, R)), ['multiple_sources'])
  const capability = GRAPHS.find((g) => g.name === 'capability_unavailable')
  assert.deepEqual(structuralErrors(capability.doc, REGISTRY['no-t2i']), [])
})

test('mergeIssues: server issues first and authoritative, deduped by (code, nodeId, port)', () => {
  const fx = GRAPHS.find((g) => g.name === 'segmentation_not_feeding_parts')
  const client = runFixture(fx)
  const server = [
    { severity: 'error', code: 'unsupported_topology', message: 'Segmentation must feed part_extraction.masks', node_id: 'n_segmentation', port: null, edge_index: null, details: null },
    { severity: 'warning', code: 'no_sink', message: 'implicit export', node_id: null, port: null, edge_index: null, details: null },
  ]
  const merged = mergeIssues(client, server)
  assert.deepEqual(merged.map((i) => [i.origin, i.code, i.nodeId]), [
    ['server', 'unsupported_topology', 'n_segmentation'],
    ['server', 'no_sink', null],
    ['client', 'ui_unreachable_node', 'n_segmentation'],
  ])
  assert.ok(hasErrors(merged), 'an error only the server reports still blocks Run')
  const dup = mergeIssues([{ ...client[0], code: 'no_sink', nodeId: null }], server)
  assert.equal(dup.length, 2)
})

test('a second Image-to-3D added through an import is too_many_instances; addNode refuses it', () => {
  const fx = GRAPHS.find((g) => g.name === 'too_many_instances')
  assert.ok(codes(runFixture(fx)).includes('too_many_instances'))
  assert.throws(() => addNode(fx.doc, R.get('input_image'), { x: 0, y: 0 }))
})
