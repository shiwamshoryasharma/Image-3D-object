// Unit tests for lib/featureFlags.ts (UI_DESIGN.md §2.5, §3.9, §6.2, Q4, §10.2; slice F2): `/features` normalisation
// for the four fixture scenarios, tolerance of malformed payloads, the fixed LOADING / ERROR / LEGACY snapshots, the
// tuner gate truth table and the derived questions (T2I models, default groups, licence notices).
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { readFixture } from './_fixtures.mjs'
import {
  ERROR_FEATURES,
  FALLBACK_LEGACY_JOB_NODE_MAP,
  FALLBACK_LIMITS,
  LEGACY_FEATURES,
  LOADING_FEATURES,
  defaultSeparate,
  isTunerVisible,
  licenceNotices,
  normalizeFeatures,
  selectableT2IModels,
} from '../../../frontend/src/lib/featureFlags.ts'

const SCENARIOS = ['full', 'no-t2i', 'no-seg', 'disabled']
const raw = (name) => readFixture(`features/${name}.json`)
const SEP = ['eyes', 'eyebrows', 'eyelashes', 'hair', 'clothes', 'accessories', 'detachable']
const SAPIENS2_CONDITIONS = ['No biometric identification', 'No deepfakes or impersonation', 'No military use']

function isDeepFrozen(value) {
  if (value === null || typeof value !== 'object') return true
  return Object.isFrozen(value) && Object.values(value).every(isDeepFrozen)
}

test('full: every capability available, with the DESIGN §6.1 values', () => {
  const f = normalizeFeatures(raw('full'))
  assert.equal(f.status, 'ready')
  assert.equal(f.apiVersion, raw('full').api.version)
  assert.deepEqual(f.flags, { partAware: true, imageGeneration: true, segmentation: true, partAnalysis: true, workflows: true, meshTuning: false, partRefinement: false })
  for (const cap of [f.imageTo3d, f.prompt, f.segmentation, f.parts, f.workflow]) assert.deepEqual([cap.available, cap.code, cap.reason], [true, 'ok', null])
  assert.deepEqual(f.prompt.presets, ['character', 'object', 'raw'])
  assert.deepEqual(f.prompt.styles, ['realistic', 'stylized', 'anime'])
  assert.equal(f.prompt.maxChars, 2000)
  assert.equal(f.prompt.negativeMaxChars, 1000)
  assert.equal(f.prompt.maxImages, 4)
  assert.equal(f.prompt.coldStartS, 337)
  assert.equal(f.prompt.defaultModelState, 'installed')
  assert.equal(f.prompt.supportsNegativePrompt, false)
  assert.deepEqual(f.prompt.sizes, raw('full').pipeline.prompt_to_3d.sizes)
  assert.deepEqual(f.segmentation.subjects, ['auto', 'anime_character', 'realistic_human', 'object'])
  assert.deepEqual(f.segmentation.profiles.map((p) => [p.id, p.available]), [['baseline', true], ['anime', true], ['realistic', true]])
  assert.equal(f.segmentation.maxExtraTerms, 16)
  assert.equal(f.parts.defaultStrategy, 'semantic_parts')
  assert.deepEqual(f.parts.separableGroups.map((g) => g.id), SEP)
  assert.deepEqual(f.parts.regionGroups.map((g) => g.id), ['face', 'body'])
  assert.deepEqual([f.parts.maxViews, f.parts.defaultViews, f.parts.rebuild], [24, 8, true])
  assert.equal(f.workflow.catalogEtag, raw('full').workflow.catalog_etag)
  assert.deepEqual([f.workflow.maxNodes, f.workflow.maxEdges, f.workflow.graphMaxBytes], [32, 64, 65536])
  assert.deepEqual(f.workflow.legacyJobNodeMap, raw('full').workflow.legacy_job_node_map)
  assert.equal(f.nodes.length, raw('full').nodes.length)
  assert.deepEqual(f.meshTuning, { enabled: false, reason: raw('full').mesh_tuning.reason })
  assert.equal(f.models.length, raw('full').models.length)
  assert.equal(f.taxonomy.version, 1)
  assert.deepEqual(f.runQueue, { pending: 0, runningId: null, maxSize: 8 })
})

test('no-t2i: prompt-to-3D unavailable with the backend code and reason; the rest stays available', () => {
  const f = normalizeFeatures(raw('no-t2i'))
  assert.deepEqual([f.prompt.available, f.prompt.code], [false, 'not_installed'])
  assert.equal(f.prompt.reason, 'Z-Image-Turbo weights are not installed under backend/.model/t2i/Z-Image-Turbo')
  assert.equal(f.prompt.defaultModelState, 'not_installed')
  assert.equal(f.nodes.find((n) => n.type === 'prompt').available, false)
  assert.equal(f.parts.available && f.segmentation.available && f.workflow.available, true)
  assert.deepEqual(selectableT2IModels(f), [])
})

test('no-seg: segmentation unavailable, semantic strategies unavailable, components is the default', () => {
  const f = normalizeFeatures(raw('no-seg'))
  assert.deepEqual([f.segmentation.available, f.segmentation.code], [false, 'not_installed'])
  assert.equal(f.parts.available, true)
  assert.equal(f.parts.defaultStrategy, 'components')
  const strategies = Object.fromEntries(f.parts.strategies.map((s) => [s.id, s.available]))
  assert.deepEqual(strategies, { components: true, semantic_regions: false, semantic_parts: false, per_part_regeneration: false })
  assert.equal(f.nodes.find((n) => n.type === 'segmentation').available, false)
  assert.deepEqual(licenceNotices(f), []) // the Sapiens2 model is not usable here
})

test('disabled: every flag off, every new pipeline entry disabled with its reason, image-to-3D available', () => {
  const f = normalizeFeatures(raw('disabled'))
  assert.equal(Object.values(f.flags).some(Boolean), false)
  const reason = 'Part-aware features are turned off (IMG3D_PART_AWARE_ENABLED=false)'
  for (const cap of [f.prompt, f.segmentation, f.parts, f.workflow]) assert.deepEqual([cap.available, cap.code, cap.reason], [false, 'disabled', reason])
  assert.deepEqual([f.imageTo3d.available, f.imageTo3d.code], [true, 'ok'])
})

test('every strategy, profile and node keeps its code and reason', () => {
  for (const name of SCENARIOS) {
    const source = raw(name)
    const f = normalizeFeatures(source)
    const strategies = source.pipeline.part_extraction.strategies
    assert.deepEqual(f.parts.strategies.map((s) => [s.id, s.available, s.code, s.reason, s.requires_segmentation]), strategies.map((s) => [s.id, s.available, s.code, s.reason, s.requires_segmentation]), name)
    const profiles = source.pipeline.segmentation.profiles
    assert.deepEqual(f.segmentation.profiles.map((p) => [p.id, p.available, p.code, p.reason]), profiles.map((p) => [p.id, p.available, p.code, p.reason]), name)
    assert.deepEqual(f.nodes.map((n) => [n.type, n.available, n.code, n.reason, n.locked, n.hidden_by_default]), source.nodes.map((n) => [n.type, n.available, n.code, n.reason, n.locked, n.hidden_by_default]), name)
  }
  const unavailable = normalizeFeatures(raw('full')).parts.strategies.find((s) => s.id === 'per_part_regeneration')
  assert.equal(unavailable.reason, 'Per-part regeneration is not implemented in this milestone.')
})

test('snapshots are deeply frozen and never freeze or mutate the payload', () => {
  const payload = raw('full')
  const before = JSON.stringify(payload)
  const f = normalizeFeatures(payload)
  assert.equal(isDeepFrozen(f), true)
  assert.equal(JSON.stringify(payload), before)
  assert.equal(Object.isFrozen(payload.models[0]), false)
  for (const fixed of [LOADING_FEATURES, ERROR_FEATURES, LEGACY_FEATURES]) assert.equal(isDeepFrozen(fixed), true)
})

test('malformed sub-objects become {available:false, code:"missing"}; nothing throws', () => {
  for (const value of [null, undefined, 42, 'x', [], {}]) {
    const f = normalizeFeatures(value)
    assert.equal(f.status, 'ready')
    for (const cap of [f.imageTo3d, f.prompt, f.segmentation, f.parts, f.workflow]) assert.deepEqual([cap.available, cap.code], [false, 'missing'])
    assert.deepEqual([f.models, f.nodes, f.parts.strategies], [[], [], []])
    assert.equal(f.runQueue, null)
  }
  const broken = raw('full')
  broken.pipeline.prompt_to_3d = 'oops'
  broken.pipeline.segmentation = { available: 'yes' }
  broken.workflow = null
  broken.pipeline.part_extraction.strategies = [null, { label: 'no id' }, { id: 'components', available: true, code: 'ok', reason: null }]
  broken.models = [{ id: 'm' }, ...broken.models]
  const f = normalizeFeatures(broken)
  assert.deepEqual([f.prompt.available, f.prompt.code], [false, 'missing'])
  assert.deepEqual([f.segmentation.available, f.segmentation.code], [false, 'missing'])
  assert.deepEqual([f.workflow.available, f.workflow.code], [false, 'missing'])
  assert.equal(f.workflow.maxNodes, FALLBACK_LIMITS.maxNodes)
  assert.deepEqual(f.workflow.legacyJobNodeMap, FALLBACK_LEGACY_JOB_NODE_MAP)
  assert.deepEqual(f.parts.strategies.map((s) => s.id), ['components'])
  assert.equal(f.models.length, raw('full').models.length) // the incomplete model entry is skipped
  assert.equal(f.parts.available, true) // untouched siblings are unaffected
})

test('unknown enum values in payload lists are dropped; unknown future fields are ignored', () => {
  const r = raw('full')
  r.pipeline.prompt_to_3d.presets = ['character', 'cartoon', 'raw', 'raw']
  r.pipeline.segmentation.subjects = ['auto', 'vehicle']
  r.pipeline.prompt_to_3d.sizes = [[1024, 1024], [0, 5], 'x', [512]]
  r.something_new = { a: 1 }
  const f = normalizeFeatures(r)
  assert.deepEqual(f.prompt.presets, ['character', 'raw'])
  assert.deepEqual(f.segmentation.subjects, ['auto'])
  assert.deepEqual(f.prompt.sizes, [[1024, 1024]])
  assert.equal('something_new' in f, false)
})

test('LOADING / ERROR / LEGACY snapshots', () => {
  assert.equal(LOADING_FEATURES.status, 'loading')
  assert.deepEqual([LOADING_FEATURES.prompt.available, LOADING_FEATURES.prompt.code, LOADING_FEATURES.prompt.reason], [false, 'loading', 'Checking backend features…'])
  assert.equal(ERROR_FEATURES.status, 'error')
  assert.deepEqual([ERROR_FEATURES.parts.code, ERROR_FEATURES.parts.reason], ['error', 'Could not read backend features. Retrying…'])
  assert.equal(LEGACY_FEATURES.status, 'legacy')
  for (const cap of [LEGACY_FEATURES.prompt, LEGACY_FEATURES.segmentation, LEGACY_FEATURES.parts, LEGACY_FEATURES.workflow]) {
    assert.deepEqual([cap.available, cap.code, cap.reason], [false, 'legacy_backend', 'Needs a newer Img3D backend.'])
  }
  for (const f of [LOADING_FEATURES, ERROR_FEATURES, LEGACY_FEATURES]) {
    assert.deepEqual([f.imageTo3d.available, f.imageTo3d.code], [true, 'ok'])
    assert.equal(f.meshTuning.enabled, false)
    assert.deepEqual([f.models, f.nodes], [[], []])
    assert.deepEqual(f.workflow.legacyJobNodeMap, FALLBACK_LEGACY_JOB_NODE_MAP)
    assert.equal(f.prompt.maxChars, FALLBACK_LIMITS.promptMaxChars)
  }
})

test('FALLBACK_LEGACY_JOB_NODE_MAP equals DESIGN §4.7.2 (the full fixture carries the same map)', () => {
  assert.deepEqual(FALLBACK_LEGACY_JOB_NODE_MAP, raw('full').workflow.legacy_job_node_map)
})

test('isTunerVisible: build flag AND ready AND mesh_tuning.enabled', () => {
  const enabled = (on) => {
    const r = raw('full')
    r.mesh_tuning = { enabled: on, reason: null }
    return normalizeFeatures(r)
  }
  const cases = [
    [false, enabled(false), false],
    [false, enabled(true), false],
    [true, enabled(false), false],
    [true, enabled(true), true],
    [true, LEGACY_FEATURES, false],
    [true, LOADING_FEATURES, false],
    [true, ERROR_FEATURES, false],
    [false, LEGACY_FEATURES, false],
  ]
  for (const [flag, f, expected] of cases) assert.equal(isTunerVisible(flag, f), expected, `${flag} × ${f.status} × ${f.meshTuning.enabled}`)
})

test('selectableT2IModels: approved text-to-image models that are installed, loading or loaded', () => {
  const r = raw('full')
  const t2i = r.models.find((m) => m.kind === 'text_to_image')
  assert.deepEqual(selectableT2IModels(normalizeFeatures(r)).map((m) => m.id), [t2i.id])
  for (const [state, approved, usable] of [['loading', true, true], ['loaded', true, true], ['not_installed', true, false], ['error', true, false], ['installed', false, false]]) {
    const copy = raw('full')
    const model = copy.models.find((m) => m.kind === 'text_to_image')
    Object.assign(model, { state, approved })
    assert.equal(selectableT2IModels(normalizeFeatures(copy)).length, usable ? 1 : 0, `${state}/${approved}`)
  }
})

test('defaultSeparate: groups marked default_selected, in /features order', () => {
  assert.deepEqual(defaultSeparate(normalizeFeatures(raw('full'))), SEP)
  const r = raw('full')
  for (const g of r.pipeline.part_extraction.separable_groups) g.default_selected = ['hair', 'eyes', 'detachable'].includes(g.id)
  assert.deepEqual(defaultSeparate(normalizeFeatures(r)), ['eyes', 'hair', 'detachable'])
  assert.deepEqual(defaultSeparate(LEGACY_FEATURES), [])
})

test('licenceNotices finds exactly the three Sapiens2 conditions in `full` (no model id hard-coded)', () => {
  const notices = licenceNotices(normalizeFeatures(raw('full')))
  assert.equal(notices.length, 1)
  assert.deepEqual([...notices[0].conditions], SAPIENS2_CONDITIONS)
  const model = raw('full').models.find((m) => m.id === notices[0].modelId)
  assert.equal(model.kind, 'segmentation')
  assert.equal(notices[0].name, model.license.name)
  assert.deepEqual(licenceNotices(normalizeFeatures(raw('disabled'))), []) // unusable models give no notice
  assert.deepEqual(licenceNotices(LEGACY_FEATURES), [])
})
