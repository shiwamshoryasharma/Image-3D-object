// Unit tests of the F0 test infrastructure data (UI_DESIGN.md §10.3, §10.4, slice F0): the shared fixtures, the
// `_fixtures.mjs` helpers (`findUnknownKeys` / `assertKnownKeys`, the walk the mock's strict mode and the request-builder
// tests share) and the procedural parts GLB, which must agree with `manifest_parts.json`.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { createHash } from 'node:crypto'
import { test } from 'node:test'

import { GRAPH_ISSUE_CODES } from '../../../frontend/src/api/partAwareTypes.ts'
import { buildPartsGlb, gray16Png, parseGlb, readAccessor, twoColorWebp } from '../mock/glb_parts.mjs'
import { canonical } from '../mock/part_aware_mock.mjs'
import { assertKnownKeys, findUnknownKeys, listFixtures, PLACEHOLDER_IDS, readFixture, readFixtureText, REQUEST_KEYS, withIds } from './_fixtures.mjs'

const SETTINGS = { resolution: 1024, seed: 0, randomize_seed: true, polygon_target: 500000, texture_size: 2048, remove_background: true, reference_mode: 'primary_only' }
const SEP = ['eyes', 'eyebrows', 'eyelashes', 'hair', 'clothes', 'accessories', 'detachable']
const GOLDEN_ROOTS = {
  G1: 'CreateJobRequest', G1a: 'CreateJobRequest', G1b: 'CreateJobRequest', G2: 'WorkflowRequest', G2a: 'WorkflowRequest', G3: 'WorkflowRequest',
  G4: 'WorkflowRequest', G5: 'ImageGenerationRequest', G6: 'UseCandidateRequest', G7: 'WorkflowRequest', G8: 'PartAnalysisOptions', G8a: 'PartAnalysisOptions',
}
const SCENARIOS = ['full', 'no-t2i', 'no-seg', 'disabled']
const types = (problems) => problems.map((problem) => `${problem.type}@${problem.loc.join('.')}`)

// ------------------------------------------------------------------------------------------------
// Golden request bodies (§10.3)
// ------------------------------------------------------------------------------------------------
test('every golden body exists, is compact JSON without a trailing newline and uses the placeholder ids', () => {
  assert.deepEqual(listFixtures('requests'), Object.keys(GOLDEN_ROOTS).sort())
  for (const id of Object.keys(GOLDEN_ROOTS)) {
    const text = readFixtureText(`requests/${id}.json`)
    assert.equal(JSON.stringify(JSON.parse(text)), text, `${id} is not compact`)
    for (const placeholder of text.match(/\b[a-z]+_[0-9a-f]{12}\b/g) ?? []) assert.ok(Object.values(PLACEHOLDER_IDS).includes(placeholder), `${id}: ${placeholder}`)
  }
})

test('golden bodies only use DESIGN keys (assertKnownKeys with their request root)', () => {
  for (const [id, root] of Object.entries(GOLDEN_ROOTS)) assertKnownKeys(readFixture(`requests/${id}.json`), root)
})

test('golden bodies have the §10.3 content (S, SEP, ids, no top-level export, num_images 1 in workflows)', () => {
  const g = (id) => readFixture(`requests/${id}.json`)
  assert.deepEqual(g('G1'), { reference_set_id: 'rs_0123456789ab', primary_image_id: 'img_0123456789ab', settings: SETTINGS })
  assert.deepEqual(Object.keys(g('G1a').settings).at(-1), 'advanced')
  assert.deepEqual(g('G1a').settings.advanced, { shape: { steps: 20 } })
  assert.equal(g('G1b').primary_image_id, null)
  assert.deepEqual(g('G2').parts, { strategy: 'semantic_parts', separate: SEP })
  assert.ok(!('export' in g('G2a')) && g('G2a').parts.export.per_part_glbs === true && g('G2a').segmentation && g('G2a').parts)
  for (const id of ['G3', 'G4']) assert.equal(g(id).source.generation.num_images, 1)
  assert.equal(g('G5').num_images, 2)
  assert.deepEqual(g('G6'), { slot: 'front', make_primary: true })
  assert.deepEqual(g('G7').graph.nodes.map((node) => node.id), ['n_input_image', 'n_image_to_3d', 'n_part_extraction', 'n_export'])
  assert.deepEqual(g('G7').graph.nodes[1].params, SETTINGS)
  assert.equal(g('G7').graph.nodes[2].params.strategy, 'semantic_parts')
  assert.deepEqual(g('G8'), { strategy: 'semantic_parts', separate: SEP })
  assert.deepEqual(g('G8a'), { strategy: 'semantic_parts', separate: SEP, export: { per_part_glbs: true } })
})

test('withIds substitutes the placeholder ids and rejects unknown kinds', () => {
  const text = readFixtureText('requests/G1.json')
  assert.equal(JSON.parse(withIds(text, { rs: 'rs_aaaaaaaaaaaa', img: 'img_bbbbbbbbbbbb' })).primary_image_id, 'img_bbbbbbbbbbbb')
  assert.equal(withIds(text, { rs: null }), text)
  assert.throws(() => withIds(text, { nope: 'x' }), /unknown id kind/)
})

// ------------------------------------------------------------------------------------------------
// request_keys.json and the shared walk (§2.6)
// ------------------------------------------------------------------------------------------------
test('request_keys.json: every alias resolves and every `paths` target names a key set', () => {
  const resolve = (name) => (typeof REQUEST_KEYS[name] === 'string' ? resolve(REQUEST_KEYS[name].slice(1)) : name)
  for (const [name, value] of Object.entries(REQUEST_KEYS)) {
    if (name === 'paths' || name === 'opaque') continue
    assert.ok(Array.isArray(REQUEST_KEYS[resolve(name)]), `${name} → ${value}`)
  }
  for (const [path, target] of Object.entries(REQUEST_KEYS.paths)) {
    const segments = path.split('.')
    const key = segments.pop().replace(/\[\]$/, '')
    const parent = resolve(segments.join('.'))
    assert.ok(Array.isArray(REQUEST_KEYS[parent]), `${path}: parent ${parent}`)
    assert.ok(key === '*' || REQUEST_KEYS[parent].includes(key), `${path}: ${key} is not a key of ${parent}`)
    if (!target.includes('<')) assert.ok(Array.isArray(REQUEST_KEYS[resolve(target)]), `${path} → ${target}`)
  }
  assert.deepEqual(REQUEST_KEYS.opaque, ['layout'])
  assert.ok(!REQUEST_KEYS.WorkflowRequest.includes('export'))
})

test('findUnknownKeys reports extra_forbidden at every nesting level the strict mock checks', () => {
  const g7 = readFixture('requests/G7.json')
  const g2 = readFixture('requests/G2.json')
  assert.deepEqual(types(findUnknownKeys({ ...g2, export: {} }, 'WorkflowRequest')), ['extra_forbidden@body.export'])
  assert.deepEqual(types(findUnknownKeys({ source: { kind: 'job', job_id: 'job_0123456789ab', x: 1 }, options: { profile: 'auto', y: 2 } }, 'SegmentationRequest')), ['extra_forbidden@body.source.x', 'extra_forbidden@body.options.y'])
  assert.deepEqual(types(findUnknownKeys({ ...g2, reconstruction: { settings: { ...SETTINGS, advanced: { shape: { steps: 2, z: 1 } } }, w: 1 } }, 'WorkflowRequest')), ['extra_forbidden@body.reconstruction.settings.advanced.shape.z', 'extra_forbidden@body.reconstruction.w'])
  // Paramless node types (refine / unsupported: "params model none", DESIGN §4.7.2) accept no params key at all.
  assert.deepEqual(types(findUnknownKeys({ graph: { ...g7.graph, nodes: [{ id: 'n_part_refine', type: 'part_refine', params: { strength: 1 } }] } }, 'WorkflowRequest')), ['extra_forbidden@body.graph.nodes.0.params.strength'])
  const node = structuredClone(g7)
  node.graph.nodes[0].x = 1
  node.graph.nodes[2].params.y = 1
  node.graph.edges[0].from.z = 1
  node.layout.anything = { deep: { goes: true } }
  assert.deepEqual(types(findUnknownKeys(node, 'WorkflowRequest')), ['extra_forbidden@body.graph.nodes.0.x', 'extra_forbidden@body.graph.nodes.2.params.y', 'extra_forbidden@body.graph.edges.0.from.z'])
  assert.deepEqual(types(findUnknownKeys({ ...g2, parts: { separate: SEP, export: { per_part_glbs: true, glb: 1 } } }, 'WorkflowRequest')), ['extra_forbidden@body.parts.export.glb'])
})

test('findUnknownKeys flags unknown kinds and objects at paths missing from the table', () => {
  assert.deepEqual(types(findUnknownKeys({ source: { kind: 'video', url: 'x' } }, 'WorkflowRequest')), ['unknown_object@body.source'])
  assert.deepEqual(types(findUnknownKeys({ prompt: 'a', width: { w: 1 } }, 'ImageGenerationRequest')), ['unnamed_object@body.width'])
  assert.throws(() => assertKnownKeys({ prompt: 'a', bogus: 1 }, 'ImageGenerationRequest'), /extra_forbidden at body\.bogus/)
})

// ------------------------------------------------------------------------------------------------
// Features, models, catalog (§10.3)
// ------------------------------------------------------------------------------------------------
function capabilityEntries(features) {
  const pipeline = Object.values(features.pipeline)
  return [...pipeline, features.workflow, ...features.pipeline.segmentation.profiles, ...features.pipeline.part_extraction.strategies, ...features.nodes]
}

test('features fixtures: complete FeaturesOut with available/code/reason on every capability entry', () => {
  const keys = Object.keys(readFixture('features/full.json'))
  const modelKeys = Object.keys(readFixture('models.json').items[0])
  for (const name of SCENARIOS) {
    const features = readFixture(`features/${name}.json`)
    assert.deepEqual(Object.keys(features), keys, name)
    assert.equal(features.schema, 'img3d.features/1')
    for (const entry of capabilityEntries(features)) {
      assert.equal(typeof entry.available, 'boolean')
      assert.equal(typeof entry.code, 'string')
      assert.equal(entry.reason === null, entry.available && entry.code === 'ok', `${name}: ${entry.id ?? entry.type ?? entry.code}`)
    }
    for (const model of features.models) assert.deepEqual(Object.keys(model), modelKeys, `${name}: ${model.id}`)
    assert.ok(features.models.every((model) => !model.hidden_by_default))
    for (const type of ['part_refine', 'mesh_tune', 'eye_correction', 'rig_correction']) {
      const node = features.nodes.find((item) => item.type === type)
      assert.ok(node.hidden_by_default && node.locked && !node.available && node.code === 'not_approved', `${name}: ${type}`)
    }
  }
})

test('features/full: both parser profiles, licence conditions only on Sapiens2 (exactly three)', () => {
  const full = readFixture('features/full.json')
  assert.ok(full.pipeline.segmentation.profiles.every((profile) => profile.available))
  const withConditions = full.models.filter((model) => model.license.conditions.length)
  assert.equal(withConditions.length, 1)
  assert.deepEqual(withConditions[0].license.conditions, ['No biometric identification', 'No deepfakes or impersonation', 'No military use'])
  assert.equal(full.mesh_tuning.enabled, false)
})

test('features/no-t2i, no-seg and disabled carry the §10.3 differences', () => {
  const noT2i = readFixture('features/no-t2i.json')
  assert.deepEqual([noT2i.pipeline.prompt_to_3d.available, noT2i.pipeline.prompt_to_3d.code, noT2i.pipeline.prompt_to_3d.reason], [false, 'not_installed', 'Z-Image-Turbo weights are not installed under backend/.model/t2i/Z-Image-Turbo'])
  assert.equal(noT2i.pipeline.prompt_to_3d.default_model_state, 'not_installed')
  assert.equal(noT2i.nodes.find((node) => node.type === 'prompt').available, false)
  const noSeg = readFixture('features/no-seg.json')
  assert.deepEqual([noSeg.pipeline.segmentation.available, noSeg.pipeline.segmentation.code], [false, 'not_installed'])
  assert.equal(noSeg.pipeline.part_extraction.default_strategy, 'components')
  assert.ok(noSeg.pipeline.part_extraction.strategies.filter((strategy) => strategy.id.startsWith('semantic')).every((strategy) => !strategy.available))
  assert.equal(noSeg.nodes.find((node) => node.type === 'segmentation').available, false)
  const disabled = readFixture('features/disabled.json')
  assert.ok(Object.values(disabled.flags).every((flag) => flag === false))
  for (const key of ['prompt_to_3d', 'segmentation', 'part_extraction']) {
    assert.deepEqual([disabled.pipeline[key].available, disabled.pipeline[key].code, disabled.pipeline[key].reason], [false, 'disabled', 'Part-aware features are turned off (IMG3D_PART_AWARE_ENABLED=false)'])
  }
  assert.equal(disabled.pipeline.image_to_3d.available, true)
  assert.equal(disabled.workflow.available, false)
})

test('catalog: every node type has params_schema.properties, 5 templates, refine hidden, ETag = sha256 of canonical JSON', () => {
  const catalog = readFixture('catalog.json')
  assert.ok(catalog.node_types.every((def) => def.params_schema && typeof def.params_schema.properties === 'object' && def.max_instances === 1))
  assert.deepEqual(catalog.templates.map((template) => template.id), ['image_to_3d', 'image_to_3d_parts', 'prompt_to_3d', 'prompt_to_3d_parts', 'rebuild_parts'])
  assert.deepEqual(Object.keys(catalog.node_types.find((def) => def.type === 'export').params_schema.properties), ['per_part_glbs', 'previews'])
  assert.ok(catalog.node_types.filter((def) => def.category === 'refine').every((def) => def.hidden_by_default && def.locked))
  const { etag, ...rest } = catalog
  assert.equal(etag, `"sha256-${createHash('sha256').update(canonical(rest)).digest('hex')}"`)
  assert.equal(readFixture('features/full.json').workflow.catalog_etag, etag)
})

// ------------------------------------------------------------------------------------------------
// Run fixtures
// ------------------------------------------------------------------------------------------------
test('run fixtures: result URLs, warning_count on workflow steps, step_failed details', () => {
  const pa = readFixture('part_analysis_completed.json')
  assert.ok(pa.result.assembled_url && pa.result.download_url && typeof pa.result.partner_filled_ratio === 'number' && 'cut_partner_consistency' in pa.result)
  const completed = readFixture('workflow_completed.json')
  assert.ok(completed.steps.every((step) => Number.isInteger(step.warning_count)))
  assert.equal(completed.steps.find((step) => step.id === 'text_to_image').warning_count, 1)
  const failed = readFixture('workflow_failed.json')
  assert.equal(failed.error.code, 'step_failed')
  assert.deepEqual(Object.keys(failed.error.details), ['step', 'child_kind', 'child_id', 'child_error'])
  assert.deepEqual([failed.error.details.step, failed.error.details.child_kind, failed.error.details.child_error.code, failed.error.details.child_error.details], ['part_extraction', 'part_analysis', 'partition_failed', null])
  assert.ok(readFixture('image_generation_completed.json').result.candidates.some((candidate) => candidate.readiness.issues.some((code) => code.startsWith('t2i_'))))
})

test('one legacy job fixture per stage and terminal status', () => {
  for (const name of listFixtures('jobs')) {
    const job = readFixture(`jobs/${name}.json`)
    assert.ok(job.progress.stage === name || job.status === name, name)
  }
  assert.equal(listFixtures('jobs').length, 13)
})

test('graph fixtures: expected_issues lists and server outcomes use the DESIGN issue codes', () => {
  const catalogTypes = new Set(readFixture('catalog.json').node_types.map((def) => def.type))
  assert.ok([...catalogTypes].every((type) => `node.${type}` in REQUEST_KEYS), 'every catalog node type has a params key set')
  const names = listFixtures('graphs')
  assert.ok(names.includes('segmentation_not_feeding_parts'))
  for (const name of names) {
    const fixture = readFixture(`graphs/${name}.json`)
    assert.ok(Array.isArray(fixture.expected_issues), name)
    if (!fixture.server) continue
    assert.ok(fixture.server.issues.every((issue) => GRAPH_ISSUE_CODES.includes(issue.code)), name)
    // Only a node type outside the catalog (the unknown_node_type case) has no key set.
    const problems = findUnknownKeys({ graph: fixture.server.graph }, 'WorkflowRequest').filter((problem) => !(problem.type === 'unknown_object' && !catalogTypes.has(problem.name.slice(5))))
    assert.deepEqual(types(problems), [], name)
  }
})

// ------------------------------------------------------------------------------------------------
// Procedural parts GLB vs manifest_parts.json (DESIGN §3.11, §3.12)
// ------------------------------------------------------------------------------------------------
test('parts GLB: nodes part:<id> with extras.img3d.id, face counts equal the manifest meshes and regions', () => {
  const manifest = readFixture('manifest_parts.json')
  const glb = buildPartsGlb()
  const parsed = parseGlb(glb.buffer)
  assert.equal(parsed.json.scenes[0].name, 'img3d_parts')
  const parts = parsed.json.nodes.slice(1)
  assert.deepEqual(parts.map((node) => node.name), manifest.meshes.map((mesh) => mesh.node_name))
  assert.ok(parts.every((node) => node.name === `part:${node.extras.img3d.id}`))
  assert.deepEqual(glb.nodes.map((node) => node.faces), manifest.meshes.map((mesh) => mesh.faces))
  const body = glb.nodes.find((node) => node.kind === 'base')
  for (const part of manifest.parts.filter((item) => item.kind !== 'physical')) {
    if (part.kind === 'base') assert.equal(body.regions[part.region.value], part.region.face_count)
    else assert.equal(manifest.parts.filter((item) => item.region.value === part.region.value && item.kind !== 'physical').reduce((sum, item) => sum + item.region.face_count, 0), body.regions[part.region.value], part.id)
  }
  // Rule 7 data: a value-0 overflow region and two regions sharing (host, value).
  assert.ok(manifest.parts.some((part) => part.kind === 'region' && part.region.value === 0))
  const shared = manifest.parts.filter((part) => part.kind === 'region' && part.region.value === 53)
  assert.equal(new Set(shared.map((part) => part.host)).size, 1)
  assert.equal(shared.length, 2)
})

test('parts GLB: _FEATURE_ID_0 is UNSIGNED_SHORT in its own bufferView with byteStride 4 and byteOffset 0', () => {
  const glb = buildPartsGlb()
  const parsed = parseGlb(glb.buffer)
  parsed.json.meshes.forEach((mesh, index) => {
    const accessorIndex = mesh.primitives[0].attributes._FEATURE_ID_0
    const accessor = parsed.json.accessors[accessorIndex]
    assert.deepEqual([accessor.componentType, accessor.byteOffset, parsed.json.bufferViews[accessor.bufferView].byteStride], [5123, 0, 4])
    // Per-face labels are exact: all three corners of every face carry the face's label.
    const ids = readAccessor(parsed, accessorIndex)
    const indices = readAccessor(parsed, mesh.primitives[0].indices)
    glb.nodes[index].faceLabels.forEach((label, face) => {
      for (let corner = 0; corner < 3; corner++) assert.equal(ids[indices[face * 3 + corner]], label)
    })
  })
  assert.ok(parseGlb(buildPartsGlb({ featureIds: false }).buffer).json.meshes.every((mesh) => !('_FEATURE_ID_0' in mesh.primitives[0].attributes)))
  assert.deepEqual(buildPartsGlb({ layout: 'components' }).nodes.slice(1).map((node) => node.id), ['component_001', 'component_002', 'component_003', 'component_004'])
})

test('encoders: two-colour WebP RIFF/VP8L header and 16-bit PNG header', () => {
  const webp = twoColorWebp(5, 3, (x) => x > 2, [255, 0, 0, 255], [0, 0, 255, 0])
  assert.deepEqual([webp.toString('ascii', 0, 4), webp.toString('ascii', 8, 16), webp.readUInt32LE(4), webp[20]], ['RIFF', 'WEBPVP8L', webp.length - 8, 0x2f])
  const bits = webp.readUInt32LE(21)
  assert.deepEqual([(bits & 0x3fff) + 1, ((bits >>> 14) & 0x3fff) + 1, (bits >>> 28) & 1], [5, 3, 1])
  const png = gray16Png(7, 2, () => 0)
  assert.deepEqual([png.readUInt32BE(16), png.readUInt32BE(20), png[24], png[25]], [7, 2, 16, 0])
})
