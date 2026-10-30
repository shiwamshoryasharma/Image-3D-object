// Unit tests for lib/generatePlan.ts (UI_DESIGN.md §2.9 = DESIGN §6.9, UI11, §10.2; slice F2): the decision table
// row by row, the blocked rows in their first-match order, and the "never trap the user" rules for a persisted prompt
// source or parts switch on a legacy / prompt-less backend (golden fixtures G1–G5, G9).
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { PLACEHOLDER_IDS, readFixture, readFixtureText } from './_fixtures.mjs'
import { ERROR_FEATURES, LEGACY_FEATURES, LOADING_FEATURES, normalizeFeatures } from '../../../frontend/src/lib/featureFlags.ts'
import { DEFAULT_PART_OPTIONS_FORM, DEFAULT_PROMPT_FORM } from '../../../frontend/src/lib/forms.ts'
import {
  candidateCount,
  effectivePartsEnabled,
  effectiveSource,
  planGeneration,
  shouldRenderPartOptions,
} from '../../../frontend/src/lib/generatePlan.ts'

const features = (name) => normalizeFeatures(readFixture(`features/${name}.json`))
const FULL = features('full')
const NO_T2I = features('no-t2i')
const NO_SEG = features('no-seg')
const DISABLED = features('disabled')

/** Full features with workflows unavailable (prompt-to-3D itself does not depend on workflows, DESIGN §6.1). */
function fullWithoutWorkflows() {
  const raw = readFixture('features/full.json')
  raw.workflow = { ...raw.workflow, available: false, code: 'disabled', reason: 'Workflows are turned off (IMG3D_WORKFLOWS_ENABLED=false)' }
  return normalizeFeatures(raw)
}

const KNIGHT = 'a knight with a red cape and a sword'
const emptySampler = () => ({ steps: null, guidance_strength: null, guidance_rescale: null, rescale_t: null })
const FORM = {
  resolution: 1024,
  seed: 0,
  randomize_seed: true,
  polygon_target: 500000,
  texture_size: 2048,
  remove_background: true,
  advanced: { sparse_structure: emptySampler(), shape: emptySampler(), texture: emptySampler() },
}
const REFS = { id: PLACEHOLDER_IDS.rs, primaryImageId: PLACEHOLDER_IDS.img, imageCount: 1 }

function inputs(patch = {}) {
  return {
    features: FULL,
    source: 'image',
    prompt: { ...DEFAULT_PROMPT_FORM, prompt: KNIGHT },
    parts: { ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [] },
    references: REFS,
    form: FORM,
    referenceMode: 'primary_only',
    ...patch,
  }
}
const partsOn = (patch = {}) => ({ ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [], enabled: true, ...patch })
const promptForm = (patch = {}) => ({ ...DEFAULT_PROMPT_FORM, prompt: KNIGHT, ...patch })
const body = (plan) => JSON.stringify(plan.request)
const golden = (name) => readFixtureText(`requests/${name}.json`)

// ------------------------------------------------------------------------------------------------
// The decision table (§2.9)
// ------------------------------------------------------------------------------------------------

test('image · parts off → job (G1, the unchanged POST /jobs body)', () => {
  const plan = planGeneration(inputs())
  assert.equal(plan.kind, 'job')
  assert.equal(plan.needsEngine, true)
  assert.equal(body(plan), golden('G1'))
})

test('image · parts on → workflow (G2)', () => {
  const plan = planGeneration(inputs({ parts: partsOn() }))
  assert.equal(plan.kind, 'workflow')
  assert.equal(plan.needsEngine, true)
  assert.equal(body(plan), golden('G2'))
})

test('prompt · parts off · one_shot → workflow (G3)', () => {
  const plan = planGeneration(inputs({ source: 'prompt', prompt: promptForm({ flow: 'one_shot' }) }))
  assert.equal(plan.kind, 'workflow')
  assert.equal(body(plan), golden('G3'))
})

test('prompt · parts on · one_shot · style anime → workflow (G4)', () => {
  const plan = planGeneration(inputs({ source: 'prompt', prompt: promptForm({ flow: 'one_shot', style: 'anime' }), parts: partsOn() }))
  assert.equal(plan.kind, 'workflow')
  assert.equal(body(plan), golden('G4'))
})

test('prompt · review → image_generation (G5), with or without parts', () => {
  for (const parts of [{ ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [] }, partsOn()]) {
    const plan = planGeneration(inputs({ source: 'prompt', prompt: promptForm({ style: 'anime' }), parts }))
    assert.equal(plan.kind, 'image_generation')
    assert.equal(plan.needsEngine, false)
    assert.equal(body(plan), golden('G5'))
  }
})

test('review candidates are clamped to features.prompt.maxImages', () => {
  const raw = readFixture('features/full.json')
  raw.pipeline.prompt_to_3d.max_images = 2
  const two = normalizeFeatures(raw)
  assert.equal(candidateCount(promptForm({ numCandidates: 4 }), two), 2)
  assert.equal(candidateCount(promptForm({ numCandidates: 1 }), two), 1)
  const plan = planGeneration(inputs({ features: two, source: 'prompt', prompt: promptForm({ numCandidates: 4 }) }))
  assert.equal(plan.request.num_images, 2)
})

test('the image job uses the reference set primary (null stays null, G1b)', () => {
  const plan = planGeneration(inputs({ references: { ...REFS, primaryImageId: null } }))
  assert.equal(body(plan), golden('G1b'))
})

// ------------------------------------------------------------------------------------------------
// Effective source and parts (UI11)
// ------------------------------------------------------------------------------------------------

test('effectiveSource: image stays image; prompt → image on legacy and on a ready backend without prompt-to-3D', () => {
  assert.equal(effectiveSource('image', FULL), 'image')
  assert.equal(effectiveSource('prompt', FULL), 'prompt')
  assert.equal(effectiveSource('prompt', LEGACY_FEATURES), 'image')
  assert.equal(effectiveSource('prompt', NO_T2I), 'image')
  assert.equal(effectiveSource('prompt', DISABLED), 'image')
  // loading / error keep the prompt source (they block instead of silently switching to an image job)
  assert.equal(effectiveSource('prompt', LOADING_FEATURES), 'prompt')
  assert.equal(effectiveSource('prompt', ERROR_FEATURES), 'prompt')
})

test('effectivePartsEnabled: forced off only on a legacy backend', () => {
  assert.equal(effectivePartsEnabled(partsOn(), LEGACY_FEATURES), false)
  for (const f of [FULL, NO_SEG, DISABLED, LOADING_FEATURES, ERROR_FEATURES]) assert.equal(effectivePartsEnabled(partsOn(), f), true)
  assert.equal(effectivePartsEnabled({ ...DEFAULT_PART_OPTIONS_FORM }, FULL), false)
})

test('prompt source on a ready backend without prompt-to-3D → the image plan (G1), not a block', () => {
  const plan = planGeneration(inputs({ features: NO_T2I, source: 'prompt', prompt: promptForm({ prompt: '' }) }))
  assert.equal(plan.kind, 'job')
  assert.equal(body(plan), golden('G1'))
})

test('G9: legacy features + parts on → job plan equal to G1', () => {
  const plan = planGeneration(inputs({ features: LEGACY_FEATURES, parts: partsOn() }))
  assert.equal(plan.kind, 'job')
  assert.equal(body(plan), golden('G1'))
})

test('legacy404 with stored parts.enabled=true and source=prompt: image, parts off, G1, no part options rendered', () => {
  const stored = inputs({ features: LEGACY_FEATURES, source: 'prompt', parts: partsOn(), prompt: promptForm({ flow: 'one_shot' }) })
  assert.equal(effectiveSource(stored.source, LEGACY_FEATURES), 'image')
  assert.equal(effectivePartsEnabled(stored.parts, LEGACY_FEATURES), false)
  const plan = planGeneration(stored)
  assert.equal(plan.kind, 'job')
  assert.equal(body(plan), golden('G1'))
  assert.equal(shouldRenderPartOptions(LEGACY_FEATURES, stored.parts), false) // the featureBundle is never requested
})

test('the same stored forms with an error snapshot: part options stay reachable, plan blocked by features_error', () => {
  const stored = inputs({ features: ERROR_FEATURES, source: 'prompt', parts: partsOn() })
  assert.equal(shouldRenderPartOptions(ERROR_FEATURES, stored.parts), true) // the off switch stays reachable (rule 7a)
  const plan = planGeneration(stored)
  assert.equal(plan.kind, 'blocked')
  assert.equal(plan.code, 'features_error') // prompt source, first match
  const imagePlan = planGeneration({ ...stored, source: 'image' })
  assert.equal(imagePlan.kind, 'blocked')
  assert.equal(imagePlan.code, 'parts_unavailable')
})

test('shouldRenderPartOptions truth table', () => {
  const off = { ...DEFAULT_PART_OPTIONS_FORM }
  assert.equal(shouldRenderPartOptions(FULL, off), true)
  assert.equal(shouldRenderPartOptions(DISABLED, off), true)
  assert.equal(shouldRenderPartOptions(LOADING_FEATURES, off), false)
  assert.equal(shouldRenderPartOptions(LOADING_FEATURES, partsOn()), true)
  assert.equal(shouldRenderPartOptions(ERROR_FEATURES, off), false)
  assert.equal(shouldRenderPartOptions(LEGACY_FEATURES, off), false)
  assert.equal(shouldRenderPartOptions(LEGACY_FEATURES, partsOn()), false)
})

// ------------------------------------------------------------------------------------------------
// Blocked rows (first match)
// ------------------------------------------------------------------------------------------------

test('prompt + features loading → features_loading; error → features_error (before any prompt check)', () => {
  const loading = planGeneration(inputs({ features: LOADING_FEATURES, source: 'prompt', prompt: promptForm({ prompt: '' }) }))
  assert.deepEqual([loading.kind, loading.code, loading.reason], ['blocked', 'features_loading', 'Checking prompt-to-3D availability…'])
  const error = planGeneration(inputs({ features: ERROR_FEATURES, source: 'prompt', prompt: promptForm({ prompt: '' }) }))
  assert.deepEqual([error.kind, error.code, error.reason], ['blocked', 'features_error', 'Could not check prompt-to-3D availability. Retrying…'])
})

test('image source while features load: today\'s job plan is not blocked (parts off)', () => {
  assert.equal(planGeneration(inputs({ features: LOADING_FEATURES })).kind, 'job')
  assert.equal(planGeneration(inputs({ features: ERROR_FEATURES })).kind, 'job')
})

test('empty / whitespace prompt → empty_prompt; too long → prompt_too_long with the backend limit', () => {
  for (const text of ['', '   \n\t ']) {
    const plan = planGeneration(inputs({ source: 'prompt', prompt: promptForm({ prompt: text }) }))
    assert.deepEqual([plan.code, plan.reason], ['empty_prompt', 'Describe what to generate.'])
  }
  const long = planGeneration(inputs({ source: 'prompt', prompt: promptForm({ prompt: 'a'.repeat(2001) }) }))
  assert.deepEqual([long.code, long.reason], ['prompt_too_long', 'The prompt is too long (max 2000 characters).'])
  const exact = planGeneration(inputs({ source: 'prompt', prompt: promptForm({ prompt: ` ${'a'.repeat(2000)} ` }) }))
  assert.equal(exact.kind, 'image_generation') // trimmed length counts
})

test('prompt + one_shot + workflow unavailable → workflows_unavailable; review in the same snapshot → image_generation', () => {
  const f = fullWithoutWorkflows()
  const oneShot = planGeneration(inputs({ features: f, source: 'prompt', prompt: promptForm({ flow: 'one_shot' }) }))
  assert.equal(oneShot.kind, 'blocked')
  assert.equal(oneShot.code, 'workflows_unavailable')
  assert.equal(oneShot.reason, 'Straight to 3D needs workflows: Workflows are turned off (IMG3D_WORKFLOWS_ENABLED=false). Use Review image first.')
  const review = planGeneration(inputs({ features: f, source: 'prompt', prompt: promptForm({ flow: 'review' }) }))
  assert.equal(review.kind, 'image_generation')
})

test('image source without images → no_references', () => {
  for (const references of [null, { ...REFS, imageCount: 0 }]) {
    const plan = planGeneration(inputs({ references }))
    assert.deepEqual([plan.kind, plan.code, plan.reason], ['blocked', 'no_references', 'Add at least one reference image.'])
  }
  // first match: the missing images win over unavailable parts
  assert.equal(planGeneration(inputs({ references: null, features: DISABLED, parts: partsOn() })).code, 'no_references')
})

test('explicit components + no-seg stays a geometry-only workflow plan', () => {
  const plan = planGeneration(inputs({ features: NO_SEG, parts: partsOn({ strategy: 'components' }) }))
  assert.equal(plan.kind, 'workflow')
  assert.deepEqual(plan.request.parts, { strategy: 'components', separate: ['eyes', 'eyebrows', 'eyelashes', 'hair', 'clothes', 'accessories', 'detachable'] })
})

test('parts on + disabled → parts_unavailable with the backend reason', () => {
  const plan = planGeneration(inputs({ features: DISABLED, parts: partsOn() }))
  assert.equal(plan.kind, 'blocked')
  assert.equal(plan.code, 'parts_unavailable')
  assert.equal(plan.reason, 'Part-aware output is unavailable: Part-aware features are turned off (IMG3D_PART_AWARE_ENABLED=false).')
})

test('parts on + parts available but workflows unavailable → workflows_unavailable', () => {
  const plan = planGeneration(inputs({ features: fullWithoutWorkflows(), parts: partsOn() }))
  assert.equal(plan.code, 'workflows_unavailable')
  assert.equal(plan.reason, 'Part-aware output needs workflows: Workflows are turned off (IMG3D_WORKFLOWS_ENABLED=false).')
})

test('a capability without a reason still yields a readable sentence', () => {
  const raw = readFixture('features/full.json')
  raw.pipeline.part_extraction = { ...raw.pipeline.part_extraction, available: false, code: 'insufficient_vram', reason: null }
  const plan = planGeneration(inputs({ features: normalizeFeatures(raw), parts: partsOn() }))
  assert.equal(plan.reason, 'Part-aware output is unavailable: Not available (insufficient_vram).')
})

test('planGeneration never mutates its inputs', () => {
  const i = inputs({ source: 'prompt', parts: partsOn({ separate: ['hair'] }), prompt: promptForm({ flow: 'one_shot' }) })
  const before = JSON.stringify(i)
  planGeneration(i)
  assert.equal(JSON.stringify(i), before)
})
