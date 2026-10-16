// Unit tests for the graph ↔ store projection (UI_DESIGN.md §4.3.6, slice F3): `deriveConfig` for the canonical
// topologies (two sources with one parked, bypassed parts, Existing Result, missing Export, Segmentation with Part
// Extraction bypassed), `templateFromConfig` → `deriveConfig` round trips for all 8 valid configs, `configFromStores`
// never asking for segmentation without parts, and `reconcile` / `switchSource` being minimal and idempotent.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { normalizeFeatures } from '../../../frontend/src/lib/featureFlags.ts'
import {
  addNode,
  configFromStores,
  connect,
  deriveConfig,
  emptyDoc,
  normalizeConfig,
  reconcile,
  registryFromCatalog,
  removeNodes,
  sameConfig,
  setMode,
  switchSource,
  templateFromConfig,
  topologyConfig,
  withNodeStates,
} from '../../../frontend/src/workflow/engine/index.ts'
import { readFixture } from './_fixtures.mjs'

const CATALOG = readFixture('catalog.json')
const FEATURES = normalizeFeatures(readFixture('features/full.json'))
const R = withNodeStates(registryFromCatalog(CATALOG), readFixture('features/full.json').nodes)
const CTX = {
  features: FEATURES,
  bound: { imageCount: 1, referenceSetId: 'rs_0123456789ab', promptLength: 34, promptMax: 2000, viewedJobCompleted: true },
}
const cfg = (source, partExtraction = false, segmentation = false) => ({ source, segmentation, partExtraction })
const CONFIGS = [
  cfg('image'), cfg('image', true), cfg('image', true, true),
  cfg('prompt'), cfg('prompt', true), cfg('prompt', true, true),
  cfg('job', true), cfg('job', true, true),
]
const ref = (node, port) => ({ node, port })
const graphFixture = (name) => readFixture(`graphs/${name}.json`).doc

test('deriveConfig: the canonical topologies', () => {
  const cases = [
    ['parked_source', cfg('image', true), [['ui_parked_source', 'n_prompt']]],
    ['segmentation_parts_bypassed', cfg('image'), [['ui_unreachable_node', 'n_segmentation']]],
    ['valid_job_result_parts', cfg('job', true), []],
    ['no_sink', cfg('image'), [['no_sink', null]]],
    ['valid_image_parts_segmentation', cfg('image', true, true), []],
    ['segmentation_not_feeding_parts', cfg('image', true), [['ui_unreachable_node', 'n_segmentation']]],
  ]
  for (const [name, config, warnings] of cases) {
    const { config: derived, issues } = deriveConfig(graphFixture(name), R, CTX)
    assert.deepEqual(derived, config, name)
    assert.deepEqual(issues.map((i) => [i.code, i.nodeId]), warnings, name)
  }
  // Two sources with one parked: the connected one wins.
  const promptDoc = templateFromConfig(cfg('prompt', true), R)
  assert.deepEqual(deriveConfig(promptDoc, R, CTX).config, cfg('prompt', true))
})

test('an active Segmentation node with Part Extraction bypassed → segmentation: false + ui_unreachable_node', () => {
  let doc = templateFromConfig(cfg('image', true, true), R)
  doc = setMode(doc, 'n_part_extraction', 'bypass', R)
  const { config, issues } = deriveConfig(doc, R, CTX)
  assert.deepEqual(config, cfg('image'))
  const skipped = issues.find((i) => i.code === 'ui_unreachable_node')
  assert.equal(skipped.nodeId, 'n_segmentation')
  assert.equal(skipped.message, 'Segmentation is skipped: connect its Masks to an active Part Extraction.')
})

test('structural errors → config null; bound-value and capability errors keep the projection', () => {
  assert.equal(deriveConfig(graphFixture('multiple_sources'), R, CTX).config, null)
  assert.equal(deriveConfig(graphFixture('cycle'), R, CTX).config, null)
  assert.equal(deriveConfig(graphFixture('no_source'), R, CTX).config, null)
  const noImages = deriveConfig(graphFixture('invalid_param_no_images'), R, { ...CTX, bound: { ...CTX.bound, imageCount: 0, referenceSetId: null } })
  assert.deepEqual(noImages.config, cfg('image'))
  assert.equal(noImages.issues[0].code, 'invalid_param')
  const noT2i = withNodeStates(registryFromCatalog(CATALOG), readFixture('features/no-t2i.json').nodes)
  assert.deepEqual(deriveConfig(graphFixture('capability_unavailable'), noT2i, CTX).config, cfg('prompt'))
})

test('templateFromConfig → deriveConfig round trip for all 8 valid configs (clean except the parked source)', () => {
  for (const config of CONFIGS) {
    const doc = templateFromConfig(config, R)
    const { config: derived, issues } = deriveConfig(doc, R, CTX)
    assert.deepEqual(derived, config, JSON.stringify(config))
    assert.ok(issues.every((i) => i.severity === 'warning' && i.code === 'ui_parked_source'), JSON.stringify(issues))
    assert.equal(issues.length, config.source === 'job' ? 0 : 1)
    assert.deepEqual(topologyConfig(doc, R), config)
  }
})

test('template layout: columns 320 px apart, viewport {40, 40, 0.9}, modes follow the config', () => {
  const doc = templateFromConfig(cfg('image', true), R, 4)
  assert.equal(doc.revision, 4)
  assert.deepEqual(doc.layout.positions, {
    n_input_image: { x: 0, y: 0 },
    n_prompt: { x: 0, y: 260 },
    n_segmentation: { x: 320, y: 0 },
    n_image_to_3d: { x: 320, y: 260 },
    n_part_extraction: { x: 640, y: 130 },
    n_export: { x: 960, y: 260 },
  })
  assert.deepEqual(doc.layout.viewport, { x: 40, y: 40, zoom: 0.9 })
  const modes = (d) => Object.fromEntries(d.graph.nodes.map((n) => [n.id, n.mode]))
  assert.equal(modes(doc).n_part_extraction, 'active')
  assert.equal(modes(doc).n_segmentation, 'bypass')
  assert.equal(modes(templateFromConfig(cfg('image'), R)).n_part_extraction, 'bypass')
  assert.equal(modes(templateFromConfig(cfg('image', true, true), R)).n_segmentation, 'active')
  // Invalid configs are normalized: segmentation needs parts, a job source needs parts.
  assert.deepEqual(normalizeConfig(cfg('image', false, true)), cfg('image'))
  assert.deepEqual(normalizeConfig(cfg('job', false)), cfg('image'))
  assert.deepEqual(topologyConfig(templateFromConfig(cfg('job', false, true), R), R), cfg('image'))
})

test('configFromStores never yields segmentation without parts; a job source survives only with parts on', () => {
  for (const docSource of [null, 'image', 'prompt', 'job']) {
    for (const input of ['image', 'prompt']) {
      for (const enabled of [false, true]) {
        for (const previewSegmentation of [false, true]) {
          const c = configFromStores(docSource, input, { enabled, previewSegmentation })
          assert.ok(!c.segmentation || c.partExtraction, JSON.stringify(c))
          assert.equal(c.partExtraction, enabled)
          assert.equal(c.source, docSource === 'job' && enabled ? 'job' : input)
        }
      }
    }
  }
})

test('reconcile is idempotent and lands on the target config from every template', () => {
  for (const from of CONFIGS) {
    for (const target of CONFIGS) {
      const doc = templateFromConfig(from, R, 10)
      const once = reconcile(doc, target, R)
      assert.deepEqual(topologyConfig(once, R), target, `${JSON.stringify(from)} → ${JSON.stringify(target)}`)
      assert.equal(reconcile(once, target, R), once, 'idempotent')
      if (sameConfig(from, target)) assert.equal(once, doc, 'no change → same doc')
      else assert.equal(once.revision, 11, 'one revision later')
    }
  }
})

test('reconcile is minimal: parts on → off only flips modes; source switch only rewires source links', () => {
  const doc = templateFromConfig(cfg('image', true), R)
  const off = reconcile(doc, cfg('image'), R)
  assert.equal(off.graph.edges, doc.graph.edges, 'edges untouched')
  assert.equal(off.layout, doc.layout, 'layout untouched')
  assert.deepEqual(off.graph.nodes.filter((n, i) => n !== doc.graph.nodes[i]).map((n) => [n.id, n.mode]), [['n_part_extraction', 'bypass']])

  const prompt = reconcile(doc, cfg('prompt', true), R)
  assert.equal(prompt.graph.nodes, doc.graph.nodes, 'nodes untouched')
  assert.equal(prompt.layout, doc.layout)
  const keys = (d) => d.graph.edges.map((e) => `${e.from.node}.${e.from.port}->${e.to.node}.${e.to.port}`).sort()
  assert.deepEqual(keys(prompt), [
    'n_image_to_3d.mesh->n_part_extraction.mesh',
    'n_part_extraction.parts->n_export.asset',
    'n_prompt.images->n_image_to_3d.images',
    'n_prompt.images->n_segmentation.images',
    'n_segmentation.masks->n_part_extraction.masks',
  ])
})

test('reconcile keeps user nodes: a bypassed refine node between Part Extraction and Export survives', () => {
  let doc = templateFromConfig(cfg('image'), R)
  doc = addNode(doc, R.get('part_refine'), { x: 800, y: 400 })
  doc = connect(doc, ref('n_part_extraction', 'parts'), ref('n_part_refine', 'parts'), R)
  doc = connect(doc, ref('n_part_refine', 'parts'), ref('n_export', 'asset'), R)
  assert.deepEqual(topologyConfig(doc, R), cfg('image'))
  const on = reconcile(doc, cfg('image', true), R)
  assert.deepEqual(topologyConfig(on, R), cfg('image', true))
  assert.equal(on.graph.edges, doc.graph.edges)
  assert.ok(on.graph.nodes.some((n) => n.id === 'n_part_refine'))
})

test('reconcile inserts missing nodes at their template positions', () => {
  const empty = emptyDoc(2)
  const built = reconcile(empty, cfg('image', true, true), R)
  assert.deepEqual(topologyConfig(built, R), cfg('image', true, true))
  assert.deepEqual(built.layout.positions.n_part_extraction, { x: 640, y: 130 })
  assert.equal(built.revision, 3)

  // Part Extraction deleted by the user, then parts turned on in Default mode.
  const noParts = removeNodes(templateFromConfig(cfg('image'), R), ['n_part_extraction'])
  const restored = reconcile(noParts, cfg('image', true), R)
  assert.deepEqual(topologyConfig(restored, R), cfg('image', true))
  assert.ok(restored.graph.edges.some((e) => e.from.node === 'n_part_extraction' && e.to.node === 'n_export'))

  // From an Existing Result graph back to an image source.
  const fromJob = reconcile(templateFromConfig(cfg('job', true), R), cfg('image', true), R)
  assert.deepEqual(topologyConfig(fromJob, R), cfg('image', true))
})

test('reconcile falls back to the template for a structurally broken doc (keeping the viewport)', () => {
  let doc = templateFromConfig(cfg('image'), R)
  doc = { ...doc, layout: { ...doc.layout, viewport: { x: 7, y: 8, zoom: 1.2 } } }
  doc = connect(doc, ref('n_prompt', 'images'), ref('n_segmentation', 'images'), R)
  doc = setMode(doc, 'n_segmentation', 'active', R)
  doc = setMode(doc, 'n_part_extraction', 'active', R)
  // A duplicate Input Image (too_many_instances, only possible through an import): modes and links cannot fix it.
  doc = { ...doc, graph: { ...doc.graph, nodes: [...doc.graph.nodes, { id: 'n_input_image_2', type: 'input_image', version: 1, mode: 'active' }] } }
  const fixed = reconcile(doc, cfg('image', true), R)
  assert.deepEqual(topologyConfig(fixed, R), cfg('image', true))
  assert.deepEqual(fixed.layout.viewport, { x: 7, y: 8, zoom: 1.2 })
  assert.equal(fixed.revision, doc.revision + 1)
})

test('switchSource ("Use this source") rewires a parked source in one edit', () => {
  const doc = templateFromConfig(cfg('image', true, true), R)
  const next = switchSource(doc, 'n_prompt', R)
  assert.deepEqual(topologyConfig(next, R), cfg('prompt', true, true))
  assert.equal(next.revision, doc.revision + 1)
  assert.equal(switchSource(next, 'n_prompt', R), next, 'already the source')
  assert.equal(switchSource(doc, 'n_missing', R), doc)
  // Existing Result → Input Image takes Part Extraction's mesh from Image-to-3D again.
  let job = templateFromConfig(cfg('image', true), R)
  job = addNode(job, R.get('job_result'), { x: 0, y: 520 })
  job = switchSource(job, 'n_job_result', R)
  assert.deepEqual(topologyConfig(job, R), cfg('job', true))
  const back = switchSource(job, 'n_input_image', R)
  assert.deepEqual(topologyConfig(back, R), cfg('image', true))
  assert.ok(back.graph.edges.some((e) => e.from.node === 'n_image_to_3d' && e.to.node === 'n_part_extraction' && e.to.port === 'mesh'))
})
