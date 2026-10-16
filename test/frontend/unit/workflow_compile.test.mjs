// Unit tests for compiling a Node doc to `WorkflowRequest {graph, layout}` (UI_DESIGN.md §4.3.7, slice F3): golden
// fixture G7 byte for byte, the template docs of all 8 valid configs (graph form ≡ simple form against the F2
// builders), `mode: 'job'` exactly for the trivial graph, bypass rewiring, exclusion of parked / unreachable nodes
// and of a Segmentation node feeding no Part Extraction, deterministic order, every request's keys inside
// `fixtures/request_keys.json`, the byte limit and the future-node `contribute` extension point.
//
// `graphToSimpleForm` (test-only, mirrors DESIGN §4.7.2 "Compiles to"): Input Image / Prompt / Existing Result →
// `source`, Segmentation → top-level `segmentation`, Image-to-3D → `reconstruction.settings`, Part Extraction →
// `parts` with the Export params as `parts.export` (default-valued keys removed; an empty object → key absent).
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { SAMPLER_KEYS } from '../../../frontend/src/api/types.ts'
import { normalizeFeatures } from '../../../frontend/src/lib/featureFlags.ts'
import { DEFAULT_PART_OPTIONS_FORM, DEFAULT_PROMPT_FORM } from '../../../frontend/src/lib/forms.ts'
import { toRequestSettings } from '../../../frontend/src/lib/settingsRequest.ts'
import {
  buildExportParams,
  buildGeneration,
  buildImageSource,
  buildPartAnalysisOptions,
  buildPromptSource,
  buildSegmentationOptions,
  buildWorkflowRequest,
} from '../../../frontend/src/lib/workflowRequest.ts'
import {
  compileDoc,
  registryFromCatalog,
  setMode,
  templateFromConfig,
  utf8Length,
  withNodeStates,
} from '../../../frontend/src/workflow/engine/index.ts'
import { NODE_OVERLAYS } from '../../../frontend/src/workflow/nodes/index.ts'
import { assertKnownKeys, PLACEHOLDER_IDS, readFixture, readFixtureText } from './_fixtures.mjs'

const CATALOG = readFixture('catalog.json')
const FEATURES = normalizeFeatures(readFixture('features/full.json'))
const R = withNodeStates(registryFromCatalog(CATALOG), readFixture('features/full.json').nodes)
const BUILD = { features: FEATURES }
const PROMPT_TEXT = 'a knight with a red cape and a sword'

const SETTINGS_FORM = {
  resolution: 1024,
  seed: 0,
  randomize_seed: true,
  polygon_target: 500000,
  texture_size: 2048,
  remove_background: true,
  advanced: Object.fromEntries(SAMPLER_KEYS.map((key) => [key, { steps: null, guidance_strength: null, guidance_rescale: null, rescale_t: null }])),
}
const SETTINGS = toRequestSettings(SETTINGS_FORM, 'primary_only')
const PROMPT_FORM = { ...DEFAULT_PROMPT_FORM, prompt: PROMPT_TEXT }

/** Two part-option forms: the defaults and a G2a-like one (strategy, groups, subject, extra term, part files). */
const PART_FORMS = {
  default: { ...DEFAULT_PART_OPTIONS_FORM },
  custom: {
    ...DEFAULT_PART_OPTIONS_FORM,
    strategy: 'semantic_parts',
    separate: ['eyes', 'hair', 'clothes'],
    subject: 'anime_character',
    extraTerms: ['katana'],
    perPartGlbs: true,
  },
}

const CONFIGS = [
  { source: 'image', segmentation: false, partExtraction: false },
  { source: 'image', segmentation: false, partExtraction: true },
  { source: 'image', segmentation: true, partExtraction: true },
  { source: 'prompt', segmentation: false, partExtraction: false },
  { source: 'prompt', segmentation: false, partExtraction: true },
  { source: 'prompt', segmentation: true, partExtraction: true },
  { source: 'job', segmentation: false, partExtraction: true },
  { source: 'job', segmentation: true, partExtraction: true },
]

/** Stores → `BoundParams`, as `shells/node/bindings.ts` gathers them (UI_DESIGN §4.3.7). */
function boundFor(partForm) {
  const parts = buildPartAnalysisOptions(partForm, BUILD)
  delete parts.export
  return {
    input_image: { reference_set_id: PLACEHOLDER_IDS.rs, primary_image_id: PLACEHOLDER_IDS.img },
    prompt: buildGeneration(PROMPT_FORM, 1, BUILD),
    job_result: { job_id: PLACEHOLDER_IDS.job },
    segmentation: buildSegmentationOptions(partForm, BUILD) ?? {},
    image_to_3d: SETTINGS,
    part_extraction: parts,
    export: buildExportParams(partForm),
  }
}

const CTX = {
  features: FEATURES,
  bound: { imageCount: 1, referenceSetId: PLACEHOLDER_IDS.rs, promptLength: PROMPT_TEXT.length, promptMax: 2000, viewedJobCompleted: true },
}

function graphToSimpleForm(request) {
  const params = new Map(request.graph.nodes.map((n) => [n.type, n.params]))
  const out = {}
  if (params.has('input_image')) out.source = { kind: 'image', ...params.get('input_image') }
  else if (params.has('prompt')) out.source = { kind: 'prompt', generation: params.get('prompt') }
  else if (params.has('job_result')) out.source = { kind: 'job', ...params.get('job_result') }
  if (params.has('segmentation')) out.segmentation = params.get('segmentation')
  if (params.has('image_to_3d')) out.reconstruction = { settings: params.get('image_to_3d') }
  if (params.has('part_extraction')) {
    const parts = { ...params.get('part_extraction') }
    const exportParams = { ...(params.get('export') ?? {}) }
    if (exportParams.per_part_glbs === false) delete exportParams.per_part_glbs
    if (exportParams.previews === true) delete exportParams.previews
    if (Object.keys(exportParams).length > 0) parts.export = exportParams
    out.parts = parts
  }
  return out
}

function simpleFormFor(config, partForm) {
  const source =
    config.source === 'image'
      ? buildImageSource(PLACEHOLDER_IDS.rs, PLACEHOLDER_IDS.img)
      : config.source === 'prompt'
        ? buildPromptSource(PROMPT_FORM, BUILD)
        : { kind: 'job', job_id: PLACEHOLDER_IDS.job }
  const partOptions = { ...partForm, enabled: config.partExtraction, previewSegmentation: config.segmentation }
  return buildWorkflowRequest({ source, partOptions, settings: SETTINGS }, BUILD)
}

const IMAGE_PARTS = { source: 'image', segmentation: false, partExtraction: true }

test('G7: template "image + parts" (segmentation bypassed) compiles to the golden bytes', () => {
  const doc = templateFromConfig(IMAGE_PARTS, R)
  const out = compileDoc(doc, R, boundFor(PART_FORMS.default), CTX)
  assert.equal(out.ok, true, JSON.stringify(out.issues))
  assert.equal(out.mode, 'workflow')
  assert.equal(JSON.stringify(out.request), readFixtureText('requests/G7.json'))
  assert.deepEqual(out.nodeIds, ['n_input_image', 'n_image_to_3d', 'n_part_extraction', 'n_export'])
  assert.deepEqual(out.config, IMAGE_PARTS)
  assert.deepEqual(out.warnings.map((w) => [w.code, w.nodeId]), [['ui_parked_source', 'n_prompt']])
  assertKnownKeys(out.request, 'WorkflowRequest')
  assertKnownKeys(JSON.parse(readFixtureText('requests/G7.json')), 'WorkflowRequest')
})

for (const [formName, partForm] of Object.entries(PART_FORMS)) {
  for (const config of CONFIGS) {
    const label = `${config.source}${config.partExtraction ? ' + parts' : ''}${config.segmentation ? ' + segmentation' : ''}`
    test(`8 configs (${formName} part options): ${label} compiles; graph form ≡ simple form`, () => {
      const doc = templateFromConfig(config, R)
      const out = compileDoc(doc, R, boundFor(partForm), CTX)
      assert.equal(out.ok, true, JSON.stringify(out.issues))
      assert.deepEqual(out.config, config)
      const trivial = config.source === 'image' && !config.partExtraction
      assert.equal(out.mode, trivial ? 'job' : 'workflow')
      assertKnownKeys(out.request, 'WorkflowRequest')
      assert.deepEqual(Object.keys(out.request), ['graph', 'layout'])
      assert.equal(JSON.stringify(graphToSimpleForm(out.request)), JSON.stringify(simpleFormFor(config, partForm)))
      // Only the run nodes are sent, and the layout names exactly them.
      assert.deepEqual(Object.keys(out.request.layout.positions), out.nodeIds)
      assert.deepEqual(out.request.graph.nodes.map((n) => n.id), out.nodeIds)
      const types = out.request.graph.nodes.map((n) => n.type)
      assert.equal(types.includes('segmentation'), config.segmentation)
      assert.equal(types.includes('part_extraction'), config.partExtraction)
      assert.equal(types.filter((t) => ['input_image', 'prompt', 'job_result'].includes(t)).length, 1)
    })
  }
}

test('custom part options reach the graph: parts.export only through the Export node', () => {
  const out = compileDoc(templateFromConfig({ source: 'image', segmentation: true, partExtraction: true }, R), R, boundFor(PART_FORMS.custom), CTX)
  const params = Object.fromEntries(out.request.graph.nodes.map((n) => [n.type, n.params]))
  assert.deepEqual(params.export, { per_part_glbs: true })
  assert.equal('export' in params.part_extraction, false)
  assert.deepEqual(params.part_extraction.separate, ['eyes', 'hair', 'clothes'])
  assert.deepEqual(params.segmentation, { subject: 'anime_character', extra_terms: ['katana'] })
  assert.equal('export' in out.request, false, 'never a top-level export')
})

test('bound part options with export / segmentation_id keys are stripped from the Part Extraction params', () => {
  const bound = { ...boundFor(PART_FORMS.default), part_extraction: { separate: ['hair'], export: { per_part_glbs: true }, segmentation_id: 'sg_0123456789ab' } }
  const out = compileDoc(templateFromConfig(IMAGE_PARTS, R), R, bound, CTX)
  assert.deepEqual(out.request.graph.nodes.find((n) => n.type === 'part_extraction').params, { separate: ['hair'] })
})

test('bypass rewiring: Part Extraction bypassed → mesh straight to Export, masks edge and Segmentation dropped', () => {
  let doc = templateFromConfig({ source: 'image', segmentation: true, partExtraction: true }, R)
  doc = setMode(doc, 'n_part_extraction', 'bypass', R)
  const out = compileDoc(doc, R, boundFor(PART_FORMS.default), CTX)
  assert.equal(out.ok, true, JSON.stringify(out.issues))
  assert.equal(out.mode, 'job')
  assert.deepEqual(out.request.graph.nodes.map((n) => n.id), ['n_input_image', 'n_image_to_3d', 'n_export'])
  assert.deepEqual(
    out.request.graph.edges.map((e) => `${e.from.node}.${e.from.port}->${e.to.node}.${e.to.port}`),
    ['n_input_image.images->n_image_to_3d.images', 'n_image_to_3d.mesh->n_export.asset'],
  )
  assert.deepEqual(out.warnings.map((w) => [w.code, w.nodeId]), [['ui_parked_source', 'n_prompt'], ['ui_unreachable_node', 'n_segmentation']])
})

test('Segmentation feeding no Part Extraction and parked sources are excluded, never sent', () => {
  const fx = readFixture('graphs/segmentation_not_feeding_parts.json')
  const out = compileDoc(fx.doc, R, boundFor(PART_FORMS.default), CTX)
  assert.equal(out.ok, true, JSON.stringify(out.issues))
  assert.equal(out.request.graph.nodes.some((n) => n.type === 'segmentation'), false)
  assert.equal(out.request.graph.edges.some((e) => e.from.node === 'n_segmentation' || e.to.node === 'n_segmentation'), false)
  assert.equal('n_segmentation' in out.request.layout.positions, false)
  assert.deepEqual(out.warnings.map((w) => [w.code, w.nodeId]), [['ui_unreachable_node', 'n_segmentation']])
  // The server would have rejected the full graph with unsupported_topology (fixture `server` block).
  assert.equal(fx.server.issues[0].code, 'unsupported_topology')
})

test('mode "job" exactly for the trivial graph (also without an Export node)', () => {
  const bound = boundFor(PART_FORMS.default)
  const trivial = compileDoc(readFixture('graphs/valid_image_to_3d.json').doc, R, bound, CTX)
  assert.equal(trivial.mode, 'job')
  const noSink = compileDoc(readFixture('graphs/no_sink.json').doc, R, bound, CTX)
  assert.equal(noSink.ok, true)
  assert.equal(noSink.mode, 'job')
  assert.deepEqual(noSink.request.graph.nodes.map((n) => n.type), ['input_image', 'image_to_3d'])
  assert.deepEqual(noSink.warnings.map((w) => w.code), ['no_sink'])
  assert.equal(compileDoc(templateFromConfig({ source: 'prompt', segmentation: false, partExtraction: false }, R), R, bound, CTX).mode, 'workflow')
  assert.equal(compileDoc(readFixture('graphs/valid_job_result_parts.json').doc, R, bound, CTX).mode, 'workflow')
})

test('server graphs of the valid fixtures: same mode and topology (bytes for the default-params case)', () => {
  const edgeSet = (graph) => graph.edges.map((e) => `${e.from.node}.${e.from.port}->${e.to.node}.${e.to.port}`).sort()
  for (const name of ['valid_image_to_3d', 'valid_image_parts_segmentation']) {
    const fx = readFixture(`graphs/${name}.json`)
    const out = compileDoc(fx.doc, R, boundFor(PART_FORMS.default), CTX)
    assert.equal(out.mode, fx.server.mode, name)
    assert.deepEqual(out.request.graph.nodes.map((n) => n.id).sort(), fx.server.graph.nodes.map((n) => n.id).sort(), name)
    assert.deepEqual(edgeSet(out.request.graph), edgeSet(fx.server.graph), name)
  }
  // The trivial fixture's server graph uses the default params, so the compiled bytes match exactly.
  const fx = readFixture('graphs/valid_image_to_3d.json')
  const out = compileDoc(fx.doc, R, boundFor(PART_FORMS.default), CTX)
  assert.equal(JSON.stringify(out.request.graph), JSON.stringify(fx.server.graph))
})

test('deterministic bytes: node and edge order in the doc do not matter', () => {
  const doc = templateFromConfig({ source: 'image', segmentation: true, partExtraction: true }, R)
  const shuffled = { ...doc, graph: { nodes: [...doc.graph.nodes].reverse(), edges: [...doc.graph.edges].reverse() } }
  const bound = boundFor(PART_FORMS.default)
  assert.equal(JSON.stringify(compileDoc(shuffled, R, bound, CTX).request), JSON.stringify(compileDoc(doc, R, bound, CTX).request))
})

test('layout: run positions rounded to integers, viewport copied', () => {
  const doc = templateFromConfig(IMAGE_PARTS, R)
  const moved = { ...doc, layout: { ...doc.layout, positions: { ...doc.layout.positions, n_export: { x: 960.6, y: 259.4 } }, viewport: { x: 1.5, y: 2, zoom: 1.25 } } }
  const out = compileDoc(moved, R, boundFor(PART_FORMS.default), CTX)
  assert.deepEqual(out.request.layout.positions.n_export, { x: 961, y: 259 })
  assert.deepEqual(out.request.layout.viewport, { x: 1.5, y: 2, zoom: 1.25 })
})

test('errors stop the compile: no request for an invalid doc', () => {
  const fx = readFixture('graphs/invalid_param_no_images.json')
  const out = compileDoc(fx.doc, R, boundFor(PART_FORMS.default), { ...CTX, bound: fx.bound })
  assert.equal(out.ok, false)
  assert.deepEqual(out.issues.map((i) => i.code), ['invalid_param'])
  // Bound params that disagree with the validation facts are still caught.
  const unbound = compileDoc(templateFromConfig(IMAGE_PARTS, R), R, { ...boundFor(PART_FORMS.default), input_image: { reference_set_id: null, primary_image_id: null } }, CTX)
  assert.equal(unbound.ok, false)
  assert.equal(unbound.issues.at(-1).message, 'Add at least one reference image.')
})

test('graph + layout over graph_max_bytes → graph_too_large', () => {
  const ctx = { ...CTX, features: { ...FEATURES, workflow: { ...FEATURES.workflow, graphMaxBytes: 200 } } }
  const out = compileDoc(templateFromConfig(IMAGE_PARTS, R), R, boundFor(PART_FORMS.default), ctx)
  assert.equal(out.ok, false)
  assert.equal(out.issues.at(-1).code, 'graph_too_large')
  assert.equal(out.issues.at(-1).message, 'The workflow is too large (max 200 bytes).')
  assert.equal(utf8Length('aé€😀'), 1 + 2 + 3 + 4)
})

test('extension point: a future binds "none" node contributes its own params (no compiler change)', () => {
  const catalog = structuredClone(CATALOG)
  catalog.node_types.push({
    ...structuredClone(catalog.node_types.find((n) => n.type === 'image_to_3d')),
    type: 'mesh_smooth',
    category: 'parts',
    title: 'Mesh Smooth',
    outputs: [{ id: 'mesh', label: 'Mesh', type: 'mesh' }],
    inputs: [{ id: 'mesh', label: 'Mesh', accepts: ['mesh'], required: true, multiple: false }],
    defaults: { strength: 1 },
  })
  const overlays = {
    ...NODE_OVERLAYS,
    mesh_smooth: { binds: 'none', isSource: false, bypass: { from: 'mesh', to: 'mesh' }, description: '', contribute: (v) => ({ strength: v.strength * 2 }) },
  }
  const r = registryFromCatalog(catalog, overlays)
  const base = readFixture('graphs/valid_image_to_3d.json').doc
  const doc = {
    ...base,
    graph: {
      nodes: [...base.graph.nodes, { id: 'n_mesh_smooth', type: 'mesh_smooth', version: 1, mode: 'active', values: { strength: 1 } }],
      edges: [
        base.graph.edges[0],
        { from: { node: 'n_image_to_3d', port: 'mesh' }, to: { node: 'n_mesh_smooth', port: 'mesh' } },
        { from: { node: 'n_mesh_smooth', port: 'mesh' }, to: { node: 'n_export', port: 'asset' } },
      ],
    },
  }
  const out = compileDoc(doc, r, boundFor(PART_FORMS.default), CTX)
  assert.equal(out.ok, true, JSON.stringify(out.issues))
  assert.equal(out.mode, 'workflow')
  assert.deepEqual(out.request.graph.nodes.find((n) => n.type === 'mesh_smooth').params, { strength: 2 })
  // Bypassed, it passes the mesh through and the graph is trivial again.
  const bypassed = setMode(doc, 'n_mesh_smooth', 'bypass', r)
  assert.equal(compileDoc(bypassed, r, boundFor(PART_FORMS.default), CTX).mode, 'job')
})
