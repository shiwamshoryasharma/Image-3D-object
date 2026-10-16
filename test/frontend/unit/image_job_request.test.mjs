// Unit tests for lib/imageJobRequest.ts and lib/settingsRequest.ts (UI_DESIGN.md §0.3, §1.3, §2.8, §10.2; slice F2):
// the image-to-3D `POST /jobs` body stays byte-identical to today's (golden fixtures G1, G1a, G1b), the moved
// `toRequestSettings` behaves exactly as before, and `store/settingsStore.ts` re-exports every moved symbol.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { readFileSync } from 'node:fs'
import { test } from 'node:test'

import { PLACEHOLDER_IDS, assertKnownKeys, readFixtureText } from './_fixtures.mjs'
import { buildImageJobRequest } from '../../../frontend/src/lib/imageJobRequest.ts'
import { SAMPLER_FIELDS, hasAdvancedOverrides, toRequestSettings } from '../../../frontend/src/lib/settingsRequest.ts'

const SAMPLER_KEYS = ['sparse_structure', 'shape', 'texture']
const emptySampler = () => ({ steps: null, guidance_strength: null, guidance_rescale: null, rescale_t: null })
const emptyAdvanced = () => ({ sparse_structure: emptySampler(), shape: emptySampler(), texture: emptySampler() })

/** Today's default settings form (`settingsStore` defaults for the 1024 / 2048 / 500 k engine defaults). */
function settingsForm(patch = {}) {
  return {
    resolution: 1024,
    seed: 0,
    randomize_seed: true,
    polygon_target: 500000,
    texture_size: 2048,
    remove_background: true,
    advanced: emptyAdvanced(),
    ...patch,
  }
}

/** Re-implementation of `jobStore.ts:L208-212` + the pre-move `settingsStore.toRequestSettings` (commit 8654b90). */
function legacyRequest(referenceSetId, primaryImageId, form, referenceMode) {
  const settings = {
    resolution: form.resolution,
    seed: form.seed,
    randomize_seed: form.randomize_seed,
    polygon_target: form.polygon_target,
    texture_size: form.texture_size,
    remove_background: form.remove_background,
    reference_mode: referenceMode,
  }
  const overrides = SAMPLER_KEYS.some((key) => SAMPLER_FIELDS.some((field) => form.advanced[key][field] !== null))
  if (overrides) {
    const advanced = {}
    for (const key of SAMPLER_KEYS) {
      const sampler = {}
      for (const field of SAMPLER_FIELDS) {
        const value = form.advanced[key][field]
        if (value !== null) sampler[field] = value
      }
      if (Object.keys(sampler).length > 0) advanced[key] = sampler
    }
    settings.advanced = advanced
  }
  return { reference_set_id: referenceSetId, primary_image_id: primaryImageId, settings }
}

const build = (form, primaryImageId = PLACEHOLDER_IDS.img, referenceMode = 'primary_only') =>
  buildImageJobRequest({ referenceSetId: PLACEHOLDER_IDS.rs, primaryImageId, form, referenceMode })

test('G1: default form → the exact golden bytes (key order included)', () => {
  assert.equal(JSON.stringify(build(settingsForm())), readFixtureText('requests/G1.json'))
})

test('G1a: one advanced override → `advanced` is the last settings key, only the set field is sent', () => {
  const advanced = emptyAdvanced()
  advanced.shape.steps = 20
  assert.equal(JSON.stringify(build(settingsForm({ advanced }))), readFixtureText('requests/G1a.json'))
})

test('G1b: no primary image → `primary_image_id: null` is still sent', () => {
  assert.equal(JSON.stringify(build(settingsForm(), null)), readFixtureText('requests/G1b.json'))
})

const SIX_FORMS = [
  ['defaults', settingsForm(), 'primary_only'],
  ['512 / fixed seed / no background removal', settingsForm({ resolution: 512, seed: 1234, randomize_seed: false, remove_background: false }), 'primary_only'],
  ['1536 / 4096 / 2 M faces / multi-view', settingsForm({ resolution: 1536, texture_size: 4096, polygon_target: 2_000_000 }), 'multi_view'],
  [
    'overrides in every stage',
    settingsForm({
      advanced: {
        sparse_structure: { steps: 12, guidance_strength: 7.5, guidance_rescale: null, rescale_t: null },
        shape: { steps: null, guidance_strength: null, guidance_rescale: 0.7, rescale_t: 5 },
        texture: { steps: 8, guidance_strength: 1, guidance_rescale: 0, rescale_t: 3 },
      },
    }),
    'primary_only',
  ],
  [
    'zero-valued overrides are explicit values, not "unset"',
    settingsForm({ advanced: { ...emptyAdvanced(), texture: { steps: null, guidance_strength: 0, guidance_rescale: 0, rescale_t: null } } }),
    'multi_view',
  ],
  ['max seed', settingsForm({ seed: 2_147_483_647, randomize_seed: false, texture_size: 512 }), 'primary_only'],
]

for (const [name, form, mode] of SIX_FORMS) {
  test(`same body as today's jobStore.submit() (${name})`, () => {
    const built = build(form, PLACEHOLDER_IDS.img, mode)
    assert.equal(JSON.stringify(built), JSON.stringify(legacyRequest(PLACEHOLDER_IDS.rs, PLACEHOLDER_IDS.img, form, mode)))
    assertKnownKeys(built, 'CreateJobRequest')
  })
}

test('toRequestSettings: no advanced key without overrides; hasAdvancedOverrides agrees', () => {
  const plain = settingsForm()
  assert.equal(hasAdvancedOverrides(plain.advanced), false)
  assert.equal('advanced' in toRequestSettings(plain, 'primary_only'), false)
  const advanced = emptyAdvanced()
  advanced.sparse_structure.rescale_t = 2
  assert.equal(hasAdvancedOverrides(advanced), true)
  assert.deepEqual(toRequestSettings(settingsForm({ advanced }), 'primary_only').advanced, { sparse_structure: { rescale_t: 2 } })
})

test('toRequestSettings does not mutate the form', () => {
  const form = settingsForm({ advanced: { ...emptyAdvanced(), shape: { steps: 20, guidance_strength: null, guidance_rescale: null, rescale_t: null } } })
  const before = JSON.stringify(form)
  toRequestSettings(form, 'multi_view')
  assert.equal(JSON.stringify(form), before)
})

test('SAMPLER_FIELDS keep their order', () => {
  assert.deepEqual([...SAMPLER_FIELDS], ['steps', 'guidance_strength', 'guidance_rescale', 'rescale_t'])
})

/** Names listed in an `export {…} from '<spec>'` / `export type {…} from '<spec>'` statement. */
function exportedNames(source, keyword, spec) {
  const escaped = spec.replace(/[.*+?^${}()|[\]\\/]/g, '\\$&')
  const pattern = new RegExp(`export\\s+${keyword}\\{([^}]*)\\}\\s*from\\s*['"]${escaped}['"]`, 'g')
  const names = []
  for (const match of source.matchAll(pattern)) {
    for (const part of match[1].split(',')) {
      const name = part.trim().replace(/^type\s+/, '')
      if (name) names.push(name)
    }
  }
  return names.sort()
}

test('store/settingsStore.ts re-exports the moved symbols (static check; the store is never imported under Node)', () => {
  const source = readFileSync(new URL('../../../frontend/src/store/settingsStore.ts', import.meta.url), 'utf8')
  assert.deepEqual(exportedNames(source, '', '../lib/settingsRequest.ts'), ['SAMPLER_FIELDS', 'hasAdvancedOverrides', 'toRequestSettings'])
  assert.deepEqual(exportedNames(source, 'type\\s*', '../lib/settingsRequest.ts'), ['SamplerField', 'SamplerForm', 'SettingsForm'])
  // Moved, not copied: the store no longer defines them itself.
  assert.doesNotMatch(source, /export\s+function\s+toRequestSettings\b/)
  assert.doesNotMatch(source, /function\s+hasAdvancedOverrides\b/)
  assert.doesNotMatch(source, /export\s+interface\s+SettingsForm\b/)
  assert.doesNotMatch(source, /export\s+const\s+SAMPLER_FIELDS\b/)
})
