// Unit tests for lib/workflowRequest.ts (UI_DESIGN.md §2.6, §2.8, §10.2; slice F2): golden bodies G2–G8a byte for
// byte, the §2.8 key order and omission rules, `separate` resolution, and — recursively, through the F0 helper
// `assertKnownKeys` — that every key of every built object is a DESIGN v1 key (`fixtures/request_keys.json`).
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { PLACEHOLDER_IDS, assertKnownKeys, findUnknownKeys, readFixture, readFixtureText } from './_fixtures.mjs'
import { normalizeFeatures } from '../../../frontend/src/lib/featureFlags.ts'
import { DEFAULT_PART_OPTIONS_FORM, DEFAULT_PROMPT_FORM } from '../../../frontend/src/lib/forms.ts'
import { toRequestSettings } from '../../../frontend/src/lib/settingsRequest.ts'
import {
  buildExportParams,
  buildGeneration,
  buildImageSource,
  buildPartAnalysisOptions,
  buildPromptSource,
  buildReferenceImageSegmentationSource,
  buildSegmentationOptions,
  buildSegmentationRequest,
  buildUseCandidateRequest,
  buildWorkflowRequest,
  resolveSeparate,
} from '../../../frontend/src/lib/workflowRequest.ts'

const FULL = normalizeFeatures(readFixture('features/full.json'))
const ctx = { features: FULL }
const SEP = ['eyes', 'eyebrows', 'eyelashes', 'hair', 'clothes', 'accessories', 'detachable']
const KNIGHT = 'a knight with a red cape and a sword'

const emptySampler = () => ({ steps: null, guidance_strength: null, guidance_rescale: null, rescale_t: null })
const settingsForm = (patch = {}) => ({
  resolution: 1024,
  seed: 0,
  randomize_seed: true,
  polygon_target: 500000,
  texture_size: 2048,
  remove_background: true,
  advanced: { sparse_structure: emptySampler(), shape: emptySampler(), texture: emptySampler() },
  ...patch,
})
const SETTINGS = toRequestSettings(settingsForm(), 'primary_only')

const parts = (patch = {}) => ({ ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [], enabled: true, ...patch })
const prompt = (patch = {}) => ({ ...DEFAULT_PROMPT_FORM, prompt: KNIGHT, ...patch })
const imageSource = () => buildImageSource(PLACEHOLDER_IDS.rs, PLACEHOLDER_IDS.img)

/** Features with some fields of the full fixture replaced (deep paths, e.g. `pipeline.prompt_to_3d.supports_negative_prompt`). */
function featuresWith(edits) {
  const raw = readFixture('features/full.json')
  for (const [path, value] of Object.entries(edits)) {
    const keys = path.split('.')
    let target = raw
    for (const key of keys.slice(0, -1)) target = target[key]
    target[keys[keys.length - 1]] = value
  }
  return normalizeFeatures(raw)
}

const G2A_PARTS = parts({
  strategy: 'semantic_parts',
  separate: ['eyes', 'hair', 'clothes'],
  subject: 'anime_character',
  extraTerms: ['katana'],
  previewSegmentation: true,
  perPartGlbs: true,
})

// ------------------------------------------------------------------------------------------------
// Golden bodies
// ------------------------------------------------------------------------------------------------

test('G2: image source + parts with defaults', () => {
  const body = buildWorkflowRequest({ source: imageSource(), partOptions: parts(), settings: SETTINGS }, ctx)
  assert.equal(JSON.stringify(body), readFixtureText('requests/G2.json'))
})

test('G2a: segmentation before 3D + method + groups + separate part files (no top-level export)', () => {
  const body = buildWorkflowRequest({ source: imageSource(), partOptions: G2A_PARTS, settings: SETTINGS }, ctx)
  assert.equal(JSON.stringify(body), readFixtureText('requests/G2a.json'))
})

test('G3: prompt one-shot without parts (num_images 1, no style)', () => {
  const body = buildWorkflowRequest(
    { source: buildPromptSource(prompt({ flow: 'one_shot' }), ctx), partOptions: { ...DEFAULT_PART_OPTIONS_FORM }, settings: SETTINGS },
    ctx,
  )
  assert.equal(JSON.stringify(body), readFixtureText('requests/G3.json'))
})

test('G4: prompt one-shot, style anime, parts on', () => {
  const body = buildWorkflowRequest(
    { source: buildPromptSource(prompt({ flow: 'one_shot', style: 'anime' }), ctx), partOptions: parts(), settings: SETTINGS },
    ctx,
  )
  assert.equal(JSON.stringify(body), readFixtureText('requests/G4.json'))
})

test('G5: review-first image generation with 2 candidates', () => {
  assert.equal(JSON.stringify(buildGeneration(prompt({ style: 'anime' }), 2, ctx)), readFixtureText('requests/G5.json'))
})

test('G6: use a candidate as the front primary image of a new set', () => {
  assert.equal(JSON.stringify(buildUseCandidateRequest()), readFixtureText('requests/G6.json'))
})

test('G8 / G8a: direct part analysis (same builder as workflows)', () => {
  assert.equal(JSON.stringify(buildPartAnalysisOptions(parts(), ctx)), readFixtureText('requests/G8.json'))
  assert.equal(JSON.stringify(buildPartAnalysisOptions(parts({ perPartGlbs: true }), ctx)), readFixtureText('requests/G8a.json'))
})

test('buildExportParams → per_part_glbs false / true (Node Export node params, G7)', () => {
  assert.equal(JSON.stringify(buildExportParams(parts())), '{"per_part_glbs":false}')
  assert.equal(JSON.stringify(buildExportParams(parts({ perPartGlbs: true }))), '{"per_part_glbs":true}')
})

// ------------------------------------------------------------------------------------------------
// Key order and omissions (§2.8)
// ------------------------------------------------------------------------------------------------

test('ImageGenerationRequest key order with every optional key present', () => {
  const features = featuresWith({ 'pipeline.prompt_to_3d.supports_negative_prompt': true })
  const g = buildGeneration(
    prompt({ negativePrompt: '  blurry  ', modelId: 'some_model', style: 'realistic', size: [896, 1152], seed: 7, randomizeSeed: false }),
    3,
    { features },
  )
  assert.deepEqual(Object.keys(g), [
    'prompt', 'negative_prompt', 'model_id', 'preset', 'style', 'part_hint', 'num_images', 'seed', 'randomize_seed', 'width', 'height',
  ])
  assert.equal(g.negative_prompt, 'blurry')
  assert.deepEqual([g.width, g.height], [896, 1152])
  assert.equal('steps' in g || 'guidance' in g, false)
  assertKnownKeys(g, 'ImageGenerationRequest')
})

test('ImageGenerationRequest omissions: style / model / size null, empty or unsupported negative prompt', () => {
  const g = buildGeneration(prompt({ prompt: `  ${KNIGHT}\n`, negativePrompt: 'blurry' }), 1, ctx) // full: negative prompts unsupported
  assert.deepEqual(Object.keys(g), ['prompt', 'preset', 'part_hint', 'num_images', 'seed', 'randomize_seed'])
  assert.equal(g.prompt, KNIGHT)
  const supported = featuresWith({ 'pipeline.prompt_to_3d.supports_negative_prompt': true })
  assert.equal('negative_prompt' in buildGeneration(prompt({ negativePrompt: '   ' }), 1, { features: supported }), false)
})

test('num_images is 1 inside workflows whatever the review candidate count is', () => {
  const source = buildPromptSource(prompt({ numCandidates: 4 }), ctx)
  assert.equal(source.generation.num_images, 1)
})

test('SegmentationOptions: all defaults → null; profile, subject, extra terms only when set', () => {
  assert.equal(buildSegmentationOptions(parts(), ctx), null)
  assert.deepEqual(Object.keys(buildSegmentationOptions(parts({ profile: 'anime', subject: 'object', extraTerms: ['katana'] }), ctx)), [
    'profile', 'subject', 'extra_terms',
  ])
  assert.deepEqual(buildSegmentationOptions(parts({ profile: 'realistic' }), ctx), { profile: 'realistic' })
})

test('SegmentationRequest (preview): source keys in order; options only when non-default', () => {
  const source = buildReferenceImageSegmentationSource(PLACEHOLDER_IDS.rs, PLACEHOLDER_IDS.img)
  assert.equal(JSON.stringify(buildSegmentationRequest(source, parts(), ctx)), '{"source":{"kind":"reference_image","reference_set_id":"rs_0123456789ab","image_id":"img_0123456789ab"}}')
  const withOptions = buildSegmentationRequest(source, parts({ subject: 'anime_character', extraTerms: ['katana'] }), ctx)
  assert.equal(JSON.stringify(withOptions.options), '{"subject":"anime_character","extra_terms":["katana"]}')
  assert.deepEqual(Object.keys(withOptions), ['source', 'options'])
  assertKnownKeys(withOptions, 'SegmentationRequest')
  assertKnownKeys(buildSegmentationRequest({ kind: 'job', job_id: PLACEHOLDER_IDS.job }, parts({ profile: 'baseline' }), ctx), 'SegmentationRequest')
})

test('extra terms: trimmed, deduplicated, at most 40 chars each and at most maxExtraTerms', () => {
  const long = 'x'.repeat(60)
  const terms = [' katana ', 'Katana', 'shield', '', '   ', long, ...Array.from({ length: 30 }, (_, i) => `term${i}`)]
  const options = buildSegmentationOptions(parts({ extraTerms: terms }), ctx)
  assert.equal(options.extra_terms[0], 'katana')
  assert.equal(options.extra_terms[1], 'shield')
  assert.equal(options.extra_terms[2], 'x'.repeat(40))
  assert.equal(options.extra_terms.length, FULL.segmentation.maxExtraTerms)
  const narrow = featuresWith({ 'pipeline.segmentation.max_extra_terms': 2 })
  assert.deepEqual(buildSegmentationOptions(parts({ extraTerms: terms }), { features: narrow }).extra_terms, ['katana', 'shield'])
})

test('PartAnalysisOptions key order and omissions', () => {
  const full = buildPartAnalysisOptions(parts({ strategy: 'components', subject: 'object', views: 12, perPartGlbs: true }), ctx)
  assert.deepEqual(Object.keys(full), ['strategy', 'segmentation', 'separate', 'views', 'export'])
  assert.deepEqual(Object.keys(buildPartAnalysisOptions(parts(), ctx)), ['strategy', 'separate'])
  for (const never of ['segmentation_id', 'min_part_faces', 'max_parts', 'split_shells']) assert.equal(never in full, false)
  assert.deepEqual(full.export, { per_part_glbs: true })
  assert.equal(buildPartAnalysisOptions(parts({ views: 99 }), ctx).views, FULL.parts.maxViews)
  assert.equal(buildPartAnalysisOptions(parts({ views: 0 }), ctx).views, 0)
})

test('separate: stored choice filtered to known groups, in /features order; null → default_selected', () => {
  assert.deepEqual(resolveSeparate(parts({ separate: ['hair', 'not_a_group', 'eyes'] }), FULL), ['eyes', 'hair'])
  assert.deepEqual(resolveSeparate(parts({ separate: null }), FULL), SEP)
  assert.deepEqual(resolveSeparate(parts({ separate: [] }), FULL), [])
  const someDefaults = readFixture('features/full.json')
  for (const group of someDefaults.pipeline.part_extraction.separable_groups) group.default_selected = group.id === 'hair' || group.id === 'eyes'
  assert.deepEqual(resolveSeparate(parts(), normalizeFeatures(someDefaults)), ['eyes', 'hair'])
  // `separate` is required, so it is sent even when empty.
  assert.deepEqual(buildPartAnalysisOptions(parts({ separate: [] }), ctx), { strategy: 'semantic_parts', separate: [] })
})

test('WorkflowRequest key order: source · segmentation · reconstruction · parts', () => {
  const body = buildWorkflowRequest({ source: imageSource(), partOptions: G2A_PARTS, settings: SETTINGS }, ctx)
  assert.deepEqual(Object.keys(body), ['source', 'segmentation', 'reconstruction', 'parts'])
  assert.deepEqual(Object.keys(body.source), ['kind', 'reference_set_id', 'primary_image_id'])
})

test('previewSegmentation with all-default options sends an empty segmentation object (only with parts)', () => {
  const body = buildWorkflowRequest({ source: imageSource(), partOptions: parts({ previewSegmentation: true }), settings: SETTINGS }, ctx)
  assert.deepEqual(body.segmentation, {})
  assert.equal('segmentation' in body.parts, false)
})

test('job source: no reconstruction', () => {
  const body = buildWorkflowRequest({ source: { kind: 'job', job_id: PLACEHOLDER_IDS.job }, partOptions: parts(), settings: SETTINGS }, ctx)
  assert.deepEqual(Object.keys(body), ['source', 'parts'])
  assertKnownKeys(body, 'WorkflowRequest')
})

test('image source with no primary image keeps primary_image_id: null', () => {
  assert.equal(JSON.stringify(buildImageSource('rs_x', null)), '{"kind":"image","reference_set_id":"rs_x","primary_image_id":null}')
})

// ------------------------------------------------------------------------------------------------
// Invariants over a matrix of inputs (UI4)
// ------------------------------------------------------------------------------------------------

const SOURCES = [
  ['image', () => imageSource()],
  ['image without primary', () => buildImageSource(PLACEHOLDER_IDS.rs, null)],
  ['prompt', () => buildPromptSource(prompt({ style: 'stylized', size: [1024, 1024], modelId: 'some_model' }), ctx)],
  ['job', () => ({ kind: 'job', job_id: PLACEHOLDER_IDS.job })],
]
const PART_FORMS = [
  ['parts off', { ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [] }],
  ['parts off but stale options set', { ...G2A_PARTS, enabled: false }],
  ['parts defaults', parts()],
  ['parts G2a', G2A_PARTS],
  ['parts with views + strategy, no preview', parts({ views: 4, strategy: 'components', profile: 'baseline' })],
  ['parts preview with default options', parts({ previewSegmentation: true })],
]
const SETTINGS_VARIANTS = [
  ['default settings', SETTINGS],
  [
    'advanced overrides',
    toRequestSettings(
      settingsForm({ advanced: { sparse_structure: emptySampler(), shape: { ...emptySampler(), steps: 20 }, texture: { ...emptySampler(), rescale_t: 2 } } }),
      'multi_view',
    ),
  ],
]

test('every built WorkflowRequest: keys ⊆ request_keys.json (recursively), no top-level export, no parts.segmentation_id, no segmentation without parts', () => {
  let checked = 0
  for (const [sourceName, makeSource] of SOURCES) {
    for (const [partsName, form] of PART_FORMS) {
      for (const [settingsName, settings] of SETTINGS_VARIANTS) {
        const label = `${sourceName} / ${partsName} / ${settingsName}`
        const body = buildWorkflowRequest({ source: makeSource(), partOptions: form, settings }, ctx)
        assert.deepEqual(findUnknownKeys(body, 'WorkflowRequest'), [], label)
        assert.equal('export' in body, false, label)
        assert.equal('name' in body || 'graph' in body || 'layout' in body, false, label)
        if (body.parts) assert.equal('segmentation_id' in body.parts, false, label)
        if ('segmentation' in body) assert.ok(body.parts, `${label}: segmentation without parts`)
        assert.equal('parts' in body, form.enabled, label)
        if (form.enabled) {
          const expected = form.perPartGlbs ? { per_part_glbs: true } : undefined
          assert.deepEqual(body.parts.export, expected, label)
        }
        checked += 1
      }
    }
  }
  assert.equal(checked, SOURCES.length * PART_FORMS.length * SETTINGS_VARIANTS.length)
})

test('every other built object passes assertKnownKeys under its DESIGN name', () => {
  const supported = featuresWith({ 'pipeline.prompt_to_3d.supports_negative_prompt': true })
  assertKnownKeys(buildGeneration(prompt({ negativePrompt: 'x', modelId: 'm', style: 'anime', size: [1152, 896] }), 4, { features: supported }), 'ImageGenerationRequest')
  assertKnownKeys(buildUseCandidateRequest(), 'UseCandidateRequest')
  for (const [, form] of PART_FORMS) {
    assertKnownKeys(buildPartAnalysisOptions(form, ctx), 'PartAnalysisOptions')
    const seg = buildSegmentationOptions(form, ctx)
    if (seg !== null) assertKnownKeys(seg, 'SegmentationOptions')
    assertKnownKeys(buildExportParams(form), 'node.export')
  }
  for (const name of ['G2', 'G2a', 'G3', 'G4']) assertKnownKeys(readFixture(`requests/${name}.json`), 'WorkflowRequest')
})

test('parts.export is {"per_part_glbs":true} exactly when "Separate part files" is on (workflows and direct analyses)', () => {
  for (const perPartGlbs of [false, true]) {
    const form = parts({ perPartGlbs })
    const direct = buildPartAnalysisOptions(form, ctx)
    const workflow = buildWorkflowRequest({ source: imageSource(), partOptions: form, settings: SETTINGS }, ctx).parts
    for (const options of [direct, workflow]) {
      if (perPartGlbs) assert.equal(JSON.stringify(options.export), '{"per_part_glbs":true}')
      else assert.equal('export' in options, false)
    }
  }
})

test('the assertKnownKeys walk itself rejects unknown and unnamed keys (guards the guard)', () => {
  const body = buildWorkflowRequest({ source: imageSource(), partOptions: G2A_PARTS, settings: SETTINGS }, ctx)
  assert.notDeepEqual(findUnknownKeys({ ...body, export: { per_part_glbs: true } }, 'WorkflowRequest'), [])
  assert.notDeepEqual(findUnknownKeys({ ...body, parts: { ...body.parts, bogus: 1 } }, 'WorkflowRequest'), [])
})
