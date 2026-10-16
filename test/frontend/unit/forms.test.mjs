// Unit tests for lib/forms.ts (UI_DESIGN.md §3.2, §3.3, §10.2; slice F2): defaults of the shared input forms and the
// sanitizers the persisted stores run in `merge` (unknown keys dropped, corrupt values → defaults, clamps, truncation).
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import {
  DEFAULT_PART_OPTIONS_FORM,
  DEFAULT_PROMPT_FORM,
  EXTRA_TERM_MAX_CHARS,
  PROMPT_STORE_MAX_CHARS,
  normalizeExtraTerms,
  promptFormFromRequest,
  sanitizeInputSource,
  sanitizeInputState,
  sanitizePartOptionsForm,
  sanitizePartOptionsState,
  sanitizePromptForm,
} from '../../../frontend/src/lib/forms.ts'

test('defaults (§3.3)', () => {
  assert.deepEqual({ ...DEFAULT_PROMPT_FORM }, {
    prompt: '',
    preset: 'character',
    style: null,
    partHint: true,
    flow: 'review',
    numCandidates: 2,
    seed: 0,
    randomizeSeed: true,
    modelId: null,
    size: null,
    negativePrompt: '',
  })
  assert.deepEqual({ ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [...DEFAULT_PART_OPTIONS_FORM.extraTerms] }, {
    enabled: false,
    strategy: null,
    separate: null,
    subject: 'auto',
    profile: 'auto',
    extraTerms: [],
    views: null,
    previewSegmentation: false,
    perPartGlbs: false,
  })
  assert.equal(Object.isFrozen(DEFAULT_PROMPT_FORM) && Object.isFrozen(DEFAULT_PART_OPTIONS_FORM), true)
})

test('sanitizePromptForm: non-objects → a fresh copy of the defaults', () => {
  for (const raw of [null, undefined, 'x', 3, [], true]) {
    const form = sanitizePromptForm(raw)
    assert.deepEqual(form, { ...DEFAULT_PROMPT_FORM })
    assert.notEqual(form, DEFAULT_PROMPT_FORM)
    assert.equal(Object.isFrozen(form), false)
  }
})

test('sanitizePromptForm keeps valid values and drops unknown keys', () => {
  const valid = {
    prompt: 'a red fox',
    preset: 'object',
    style: 'anime',
    partHint: false,
    flow: 'one_shot',
    numCandidates: 3,
    seed: 42,
    randomizeSeed: false,
    modelId: 'some_model',
    size: [896, 1152],
    negativePrompt: 'blurry',
  }
  assert.deepEqual(sanitizePromptForm({ ...valid, extra: 1, __proto__x: 2 }), valid)
})

test('sanitizePromptForm: corrupt values fall back to the defaults', () => {
  const form = sanitizePromptForm({
    prompt: 7,
    preset: 'cartoon',
    style: 'noir',
    partHint: 'yes',
    flow: 'later',
    numCandidates: 'many',
    seed: Number.NaN,
    randomizeSeed: 1,
    modelId: '   ',
    size: [1024, -1],
    negativePrompt: { text: 'x' },
  })
  assert.deepEqual(form, { ...DEFAULT_PROMPT_FORM })
  assert.equal(sanitizePromptForm({ style: null }).style, null)
  assert.equal(sanitizePromptForm({ size: [1024.5, 1024] }).size, null)
  assert.equal(sanitizePromptForm({ size: [1024, 1024, 3] }).size, null)
  assert.equal(sanitizePromptForm({ modelId: 'x'.repeat(200) }).modelId, null)
  assert.equal(sanitizePromptForm({ modelId: '  model_a  ' }).modelId, 'model_a')
})

test('numCandidates is clamped to 1..4 and rounded; seeds to 0..2^31-1', () => {
  assert.equal(sanitizePromptForm({ numCandidates: 0 }).numCandidates, 1)
  assert.equal(sanitizePromptForm({ numCandidates: 9 }).numCandidates, 4)
  assert.equal(sanitizePromptForm({ numCandidates: 2.6 }).numCandidates, 3)
  assert.equal(sanitizePromptForm({ seed: -5 }).seed, 0)
  assert.equal(sanitizePromptForm({ seed: 1e12 }).seed, 2_147_483_647)
})

test('prompts longer than 8000 characters are truncated (the real limit comes from /features)', () => {
  assert.equal(PROMPT_STORE_MAX_CHARS, 8000)
  const form = sanitizePromptForm({ prompt: 'p'.repeat(9000), negativePrompt: 'n'.repeat(8001) })
  assert.equal(form.prompt.length, 8000)
  assert.equal(form.negativePrompt.length, 8000)
  assert.equal(sanitizePromptForm({ prompt: '  spaced  ' }).prompt, '  spaced  ') // stored as typed
})

test('sanitizePartOptionsForm keeps valid values and drops unknown keys', () => {
  const valid = {
    enabled: true,
    strategy: 'semantic_parts',
    separate: ['eyes', 'hair'],
    subject: 'anime_character',
    profile: 'anime',
    extraTerms: ['katana'],
    views: 12,
    previewSegmentation: true,
    perPartGlbs: true,
  }
  assert.deepEqual(sanitizePartOptionsForm({ ...valid, regions: ['face'], export: {} }), valid)
})

test('sanitizePartOptionsForm: corrupt values fall back to the defaults', () => {
  const form = sanitizePartOptionsForm({
    enabled: 'true',
    strategy: 5,
    separate: 'eyes',
    subject: 'robot',
    profile: 'cartoon',
    extraTerms: 'katana',
    views: 'many',
    previewSegmentation: null,
    perPartGlbs: 0,
  })
  assert.deepEqual(form, { ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [] })
  assert.deepEqual(sanitizePartOptionsForm(null), { ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [] })
  assert.notEqual(sanitizePartOptionsForm(null).extraTerms, DEFAULT_PART_OPTIONS_FORM.extraTerms)
})

test('separate: null stays null (= backend defaults); lists are de-duplicated and cleaned', () => {
  assert.equal(sanitizePartOptionsForm({ separate: null }).separate, null)
  assert.deepEqual(sanitizePartOptionsForm({ separate: [] }).separate, [])
  assert.deepEqual(sanitizePartOptionsForm({ separate: ['hair', 'hair', 3, '', ' eyes '] }).separate, ['hair', 'eyes'])
})

test('views: null kept, numbers clamped to 0..24', () => {
  assert.equal(sanitizePartOptionsForm({ views: null }).views, null)
  assert.equal(sanitizePartOptionsForm({}).views, null)
  assert.equal(sanitizePartOptionsForm({ views: -3 }).views, 0)
  assert.equal(sanitizePartOptionsForm({ views: 100 }).views, 24)
  assert.equal(sanitizePartOptionsForm({ views: 7.4 }).views, 7)
})

test('normalizeExtraTerms: trimmed, case-insensitively unique, ≤ 40 chars, ≤ max', () => {
  assert.equal(EXTRA_TERM_MAX_CHARS, 40)
  assert.deepEqual(normalizeExtraTerms([' Katana', 'katana', 'cat ears', 7, '', 'y'.repeat(45)], 10), ['Katana', 'cat ears', 'y'.repeat(40)])
  assert.deepEqual(normalizeExtraTerms(['a', 'b', 'c'], 2), ['a', 'b'])
  assert.deepEqual(normalizeExtraTerms('a', 5), [])
  assert.equal(sanitizePartOptionsForm({ extraTerms: Array.from({ length: 100 }, (_, i) => `t${i}`) }).extraTerms.length, 64)
})

test('input source and the persisted envelopes', () => {
  assert.equal(sanitizeInputSource('prompt'), 'prompt')
  assert.equal(sanitizeInputSource('PROMPT'), 'image')
  assert.equal(sanitizeInputSource(undefined), 'image')
  assert.deepEqual(sanitizeInputState({ source: 'prompt', prompt: { prompt: 'x' }, other: 1 }), {
    source: 'prompt',
    prompt: { ...DEFAULT_PROMPT_FORM, prompt: 'x' },
  })
  assert.deepEqual(sanitizeInputState('garbage'), { source: 'image', prompt: { ...DEFAULT_PROMPT_FORM } })
  assert.deepEqual(sanitizePartOptionsState({ form: { enabled: true } }), { form: { ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [], enabled: true } })
  assert.deepEqual(sanitizePartOptionsState(null), { form: { ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [] } })
})

test('promptFormFromRequest: an echoed request → a valid prompt form ("Reuse prompt")', () => {
  const g = {
    prompt: 'a knight',
    negative_prompt: null,
    model_id: 'model_b',
    preset: 'raw',
    style: 'stylized',
    part_hint: false,
    num_images: 3,
    seed: 9,
    randomize_seed: false,
    width: 896,
    height: 1152,
  }
  assert.deepEqual(promptFormFromRequest(g), {
    prompt: 'a knight',
    preset: 'raw',
    style: 'stylized',
    partHint: false,
    flow: 'review',
    numCandidates: 3,
    seed: 9,
    randomizeSeed: false,
    modelId: 'model_b',
    size: [896, 1152],
    negativePrompt: '',
  })
  const oneShot = promptFormFromRequest({ ...g, num_images: 1, width: null, height: null, style: null }, 'one_shot')
  assert.deepEqual([oneShot.flow, oneShot.numCandidates, oneShot.size, oneShot.style], ['one_shot', 2, null, null])
  const unknown = promptFormFromRequest({ prompt: 'x', preset: 'cartoon', part_hint: true, num_images: 9, seed: -1, randomize_seed: true })
  assert.deepEqual([unknown.preset, unknown.numCandidates, unknown.seed], ['character', 4, 0])
})
