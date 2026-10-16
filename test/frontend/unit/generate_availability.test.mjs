// Unit tests for lib/generateAvailability.ts (UI_DESIGN.md §2.10, UI10, §10.2; slice F2): the image-source reasons
// and notes are byte-identical to today's `hooks/useGenerateAvailability.ts` (re-implemented below from its pre-milestone
// source, L44-L74), and the new rules (Node pipeline block, prompt rules 5a–5e, part rules 7a/7b, run busy, run queue,
// cold-start note) apply in the §2.10 order.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { PLACEHOLDER_IDS, readFixture } from './_fixtures.mjs'
import { ERROR_FEATURES, LEGACY_FEATURES, LOADING_FEATURES, normalizeFeatures } from '../../../frontend/src/lib/featureFlags.ts'
import { DEFAULT_PART_OPTIONS_FORM, DEFAULT_PROMPT_FORM } from '../../../frontend/src/lib/forms.ts'
import { generateAvailability } from '../../../frontend/src/lib/generateAvailability.ts'

const FULL = normalizeFeatures(readFixture('features/full.json'))
const DISABLED = normalizeFeatures(readFixture('features/disabled.json'))

function featuresWith(edit) {
  const raw = readFixture('features/full.json')
  edit(raw)
  return normalizeFeatures(raw)
}
const NO_WORKFLOWS = featuresWith((raw) => {
  raw.workflow = { ...raw.workflow, available: false, code: 'disabled', reason: 'Workflows are turned off.' }
})
const RUN_QUEUE_FULL = featuresWith((raw) => {
  raw.queues.runs = { pending: 8, running_id: 'ig_0123456789ab', running_kind: 'image_generation', max_size: 8 }
})
const T2I_LOADED = featuresWith((raw) => {
  raw.pipeline.prompt_to_3d.default_model_state = 'loaded'
})

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

const engine = (state, extra = {}) => ({
  name: 'trellis2',
  state,
  message: null,
  load_progress: null,
  error: null,
  loaded_at: null,
  details: {},
  capabilities: {},
  ...extra,
})
const system = ({ engineInfo = engine('ready'), queue = { pending: 0, running_job_id: null, max_size: 4 } } = {}) => ({ engine: engineInfo, queue })

function planInputs(patch = {}) {
  return {
    features: FULL,
    source: 'image',
    prompt: { ...DEFAULT_PROMPT_FORM, prompt: KNIGHT },
    parts: { ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [] },
    references: { id: PLACEHOLDER_IDS.rs, primaryImageId: PLACEHOLDER_IDS.img, imageCount: 2 },
    form: FORM,
    referenceMode: 'primary_only',
    ...patch,
  }
}

function inputs(patch = {}, plan = {}) {
  return {
    reachable: true,
    system: system(),
    plan: planInputs(plan),
    uploading: false,
    activeJobStatus: null,
    activeWorkflowStatus: null,
    reviewGenerationStatus: null,
    submitting: false,
    pipelineBlock: null,
    nodeShellActive: false,
    ...patch,
  }
}

// ------------------------------------------------------------------------------------------------
// Today's hook, verbatim logic (useGenerateAvailability.ts before this milestone)
// ------------------------------------------------------------------------------------------------

function summarize(message, fallback) {
  const text = (message ?? '').replace(/\s*\(see error for how to fix\)\s*$/i, '').trim()
  return text || fallback
}

function todaysHook({ reachable, system: sys, imageCount, uploading, activeJobStatus, submitting }) {
  const busy = activeJobStatus !== null && !['completed', 'failed', 'cancelled'].includes(activeJobStatus)
  const eng = sys?.engine
  const queue = sys?.queue
  let reason = null
  let note = null
  let engineProblem = null
  if (eng?.state === 'unavailable') engineProblem = { summary: summarize(eng.message, 'The 3D engine is unavailable.'), details: eng.error }
  else if (eng?.state === 'error') engineProblem = { summary: summarize(eng.message, 'The model failed to load.'), details: eng.error }
  if (reachable === false) reason = 'The backend is unreachable.'
  else if (!sys) reason = 'Waiting for the backend…'
  else if (engineProblem) reason = engineProblem.summary
  else if (imageCount === 0) reason = 'Add at least one reference image.'
  else if (uploading) reason = 'Wait for uploads to finish.'
  else if (busy) reason = 'A generation is already in progress.'
  else if (queue && queue.pending >= queue.max_size) reason = 'The generation queue is full.'
  else if (submitting) reason = 'Submitting…'
  if (!reason && eng && eng.state !== 'ready') {
    const progress = eng.load_progress
    note =
      eng.state === 'loading'
        ? `Model is loading${progress ? ` (${progress.loaded}/${progress.total})` : ''} — the job will start when it is ready.`
        : 'The model is not loaded yet — it will load and the job will start automatically.'
  } else if (!reason && queue && (queue.pending > 0 || queue.running_job_id)) {
    note = `${queue.pending + (queue.running_job_id ? 1 : 0)} job(s) ahead in the queue.`
  }
  return { canGenerate: reason === null, reason, note, busy, engineProblem: reachable === false ? null : engineProblem }
}

const SCENARIOS = [
  ['unreachable', { reachable: false, system: null }, 'The backend is unreachable.', 'unreachable'],
  ['unreachable with an engine error (no engine problem reported)', { reachable: false, system: system({ engineInfo: engine('error', { message: 'boom', error: 'trace' }) }) }, 'The backend is unreachable.', 'unreachable'],
  ['waiting for the backend', { reachable: null, system: null }, 'Waiting for the backend…', 'waiting_backend'],
  [
    'engine unavailable',
    { system: system({ engineInfo: engine('unavailable', { message: 'TRELLIS.2 cannot start: CUDA missing (see error for how to fix)', error: 'Install CUDA 13.' }) }) },
    'TRELLIS.2 cannot start: CUDA missing',
    'engine_problem',
  ],
  ['engine unavailable without a message', { system: system({ engineInfo: engine('unavailable') }) }, 'The 3D engine is unavailable.', 'engine_problem'],
  ['engine error', { system: system({ engineInfo: engine('error', { message: 'Model load failed (see error for how to fix)', error: 'OOM' }) }) }, 'Model load failed', 'engine_problem'],
  ['no images', { imageCount: 0 }, 'Add at least one reference image.', 'no_references'],
  ['uploading', { uploading: true }, 'Wait for uploads to finish.', 'uploading'],
  ['busy', { activeJobStatus: 'running' }, 'A generation is already in progress.', 'busy'],
  ['busy (queued)', { activeJobStatus: 'queued' }, 'A generation is already in progress.', 'busy'],
  ['queue full', { system: system({ queue: { pending: 4, running_job_id: 'job_x', max_size: 4 } }) }, 'The generation queue is full.', 'queue_full'],
  ['submitting', { submitting: true }, 'Submitting…', 'submitting'],
  ['engine loading (note)', { system: system({ engineInfo: engine('loading', { load_progress: { loaded: 3, total: 7 } }) }) }, null, null],
  ['engine loading without progress (note)', { system: system({ engineInfo: engine('loading') }) }, null, null],
  ['engine not loaded (note)', { system: system({ engineInfo: engine('not_loaded') }) }, null, null],
  ['jobs ahead (note)', { system: system({ queue: { pending: 2, running_job_id: 'job_x', max_size: 4 } }) }, null, null],
  ['finished job is not busy', { activeJobStatus: 'completed' }, null, null],
  ['ready', {}, null, null],
]

for (const [name, patch, reason, code] of SCENARIOS) {
  for (const [featuresName, features] of [['legacy', LEGACY_FEATURES], ['full', FULL], ['loading', LOADING_FEATURES]]) {
    test(`image source, parts off — same as today's hook: ${name} (${featuresName} features)`, () => {
      const { imageCount = 2, ...rest } = patch
      const i = inputs(rest, { features, references: { id: PLACEHOLDER_IDS.rs, primaryImageId: null, imageCount } })
      const actual = generateAvailability(i)
      const expected = todaysHook({ ...i, imageCount })
      assert.deepEqual(
        { canGenerate: actual.canGenerate, reason: actual.reason, note: actual.note, busy: actual.busy, engineProblem: actual.engineProblem },
        expected,
      )
      assert.equal(actual.reason, reason)
      assert.equal(actual.code, code)
      assert.equal(actual.plan, imageCount === 0 ? 'blocked' : 'job')
    })
  }
}

test('notes keep today\'s exact wording', () => {
  const loading = generateAvailability(inputs({ system: system({ engineInfo: engine('loading', { load_progress: { loaded: 3, total: 7 } }) }) }))
  assert.equal(loading.note, 'Model is loading (3/7) — the job will start when it is ready.')
  const notLoaded = generateAvailability(inputs({ system: system({ engineInfo: engine('not_loaded') }) }))
  assert.equal(notLoaded.note, 'The model is not loaded yet — it will load and the job will start automatically.')
  const ahead = generateAvailability(inputs({ system: system({ queue: { pending: 2, running_job_id: 'job_x', max_size: 4 } }) }))
  assert.equal(ahead.note, '3 job(s) ahead in the queue.')
})

// ------------------------------------------------------------------------------------------------
// New rules
// ------------------------------------------------------------------------------------------------

const prompt = (patch = {}) => ({ ...DEFAULT_PROMPT_FORM, prompt: KNIGHT, ...patch })

test('rule 4: the Node pipeline block comes after the engine problem and before every input rule', () => {
  const blocked = generateAvailability(inputs({ pipelineBlock: 'Connect an image source.' }, { references: null }))
  assert.deepEqual([blocked.reason, blocked.code], ['Fix the workflow: Connect an image source.', 'pipeline_invalid'])
  const engineFirst = generateAvailability(inputs({ pipelineBlock: 'x', system: system({ engineInfo: engine('error', { message: 'bad' }) }) }))
  assert.equal(engineFirst.code, 'engine_problem')
  const waiting = generateAvailability(inputs({ pipelineBlock: 'x', system: null, reachable: null }))
  assert.equal(waiting.code, 'waiting_backend')
})

test('rules 5a–5e for the prompt source, in order', () => {
  const at = (features, promptPatch) => generateAvailability(inputs({}, { features, source: 'prompt', prompt: prompt(promptPatch) }))
  assert.deepEqual([at(LOADING_FEATURES, { prompt: '' }).code, at(LOADING_FEATURES, { prompt: '' }).reason], ['features_loading', 'Checking prompt-to-3D availability…'])
  assert.deepEqual([at(ERROR_FEATURES, { prompt: '' }).code, at(ERROR_FEATURES, { prompt: '' }).reason], ['features_error', 'Could not check prompt-to-3D availability. Retrying…'])
  assert.deepEqual([at(FULL, { prompt: '  ' }).code, at(FULL, { prompt: '  ' }).reason], ['empty_prompt', 'Describe what to generate.'])
  const long = at(FULL, { prompt: 'b'.repeat(2001) })
  assert.deepEqual([long.code, long.reason], ['prompt_too_long', 'The prompt is too long (max 2000 characters).'])
  const oneShot = at(NO_WORKFLOWS, { flow: 'one_shot', prompt: 'b'.repeat(2001) }) // 5d before 5e
  assert.equal(oneShot.code, 'prompt_too_long')
  const noWorkflows = at(NO_WORKFLOWS, { flow: 'one_shot' })
  assert.deepEqual([noWorkflows.code, noWorkflows.reason], ['workflows_unavailable', 'Straight to 3D needs workflows: Workflows are turned off. Use Review image first.'])
  assert.equal(at(NO_WORKFLOWS, { flow: 'review' }).canGenerate, true)
})

test('the prompt source ignores the reference set and its uploads', () => {
  const a = generateAvailability(inputs({ uploading: true }, { source: 'prompt', references: null }))
  assert.equal(a.canGenerate, true)
  assert.equal(a.plan, 'image_generation')
})

test('an unavailable prompt source falls back to the image rules (never trapped, UI11)', () => {
  const noT2i = normalizeFeatures(readFixture('features/no-t2i.json'))
  const a = generateAvailability(inputs({}, { features: noT2i, source: 'prompt', prompt: prompt({ prompt: '' }), references: null }))
  assert.deepEqual([a.code, a.reason], ['no_references', 'Add at least one reference image.'])
  const legacy = generateAvailability(inputs({}, { features: LEGACY_FEATURES, source: 'prompt', prompt: prompt({ prompt: '' }) }))
  assert.equal(legacy.canGenerate, true)
  assert.equal(legacy.plan, 'job')
})

test('rules 7a / 7b for part-aware output', () => {
  const parts = { ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [], enabled: true }
  const a = generateAvailability(inputs({}, { features: DISABLED, parts }))
  assert.deepEqual([a.code, a.reason], [
    'parts_unavailable',
    'Part-aware output is unavailable: Part-aware features are turned off (IMG3D_PART_AWARE_ENABLED=false). Turn it off to generate.',
  ])
  const b = generateAvailability(inputs({}, { features: NO_WORKFLOWS, parts }))
  assert.deepEqual([b.code, b.reason], ['workflows_unavailable', 'Part-aware output needs workflows: Workflows are turned off. Turn it off to generate.'])
  // uploads (6b) come before the part rules
  assert.equal(generateAvailability(inputs({ uploading: true }, { features: DISABLED, parts })).code, 'uploading')
  // legacy: parts are forced off, so nothing blocks
  assert.equal(generateAvailability(inputs({}, { features: LEGACY_FEATURES, parts })).canGenerate, true)
  const ok = generateAvailability(inputs({}, { parts }))
  assert.equal(ok.canGenerate, true)
  assert.equal(ok.plan, 'workflow')
})

test('while the Node shell is mounted rules 5–7 are skipped and the plan is left to the shell', () => {
  const a = generateAvailability(inputs({ nodeShellActive: true }, { references: null, source: 'prompt', prompt: prompt({ prompt: '' }) }))
  assert.equal(a.canGenerate, true)
  assert.equal(a.plan, null)
  const parts = { ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [], enabled: true }
  assert.equal(generateAvailability(inputs({ nodeShellActive: true }, { features: DISABLED, parts })).canGenerate, true)
  // rule 4 still carries the Node validation result
  assert.equal(generateAvailability(inputs({ nodeShellActive: true, pipelineBlock: 'Fix it' })).code, 'pipeline_invalid')
})

test('an active workflow or review generation counts as busy; terminal runs do not', () => {
  for (const status of ['queued', 'running']) {
    for (const key of ['activeWorkflowStatus', 'reviewGenerationStatus']) {
      const a = generateAvailability(inputs({ [key]: status }))
      assert.deepEqual([a.code, a.reason, a.busy], ['busy', 'A generation is already in progress.', true], `${key}=${status}`)
    }
  }
  for (const status of ['completed', 'failed', 'cancelled']) {
    assert.equal(generateAvailability(inputs({ activeWorkflowStatus: status, reviewGenerationStatus: status })).busy, false)
  }
})

test('run queue full blocks workflow and image-generation plans only; the job queue blocks 3D plans only', () => {
  const parts = { ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [], enabled: true }
  const wf = generateAvailability(inputs({}, { features: RUN_QUEUE_FULL, parts }))
  assert.deepEqual([wf.code, wf.reason], ['run_queue_full', 'The run queue is full.'])
  const review = generateAvailability(inputs({}, { features: RUN_QUEUE_FULL, source: 'prompt' }))
  assert.equal(review.code, 'run_queue_full')
  assert.equal(generateAvailability(inputs({}, { features: RUN_QUEUE_FULL })).canGenerate, true) // plain job
  const fullJobQueue = system({ queue: { pending: 4, running_job_id: 'job_x', max_size: 4 } })
  assert.equal(generateAvailability(inputs({ system: fullJobQueue }, { source: 'prompt' })).canGenerate, true) // review: no reconstruction
  assert.equal(generateAvailability(inputs({ system: fullJobQueue }, { source: 'prompt', prompt: prompt({ flow: 'one_shot' }) })).code, 'queue_full')
})

test('submitting (job or run store) is the last rule', () => {
  const a = generateAvailability(inputs({ submitting: true }, { source: 'prompt' }))
  assert.deepEqual([a.code, a.reason], ['submitting', 'Submitting…'])
})

test('cold-start note for prompt plans while the text-to-image model is only installed', () => {
  const review = generateAvailability(inputs({ system: system({ engineInfo: engine('not_loaded') }) }, { source: 'prompt' }))
  assert.equal(review.note, 'The text-to-image model loads first (about 6 min).') // ceil(337 / 60)
  assert.equal(generateAvailability(inputs({}, { features: T2I_LOADED, source: 'prompt' })).note, null)
  // a loaded T2I model + a one-shot plan → today's engine note (reconstruction follows)
  const oneShot = generateAvailability(inputs({ system: system({ engineInfo: engine('not_loaded') }) }, { features: T2I_LOADED, source: 'prompt', prompt: prompt({ flow: 'one_shot' }) }))
  assert.equal(oneShot.note, 'The model is not loaded yet — it will load and the job will start automatically.')
  // review plans never show the 3D engine or job queue notes
  const reviewLoaded = generateAvailability(inputs({ system: system({ engineInfo: engine('not_loaded'), queue: { pending: 1, running_job_id: null, max_size: 4 } }) }, { features: T2I_LOADED, source: 'prompt' }))
  assert.equal(reviewLoaded.note, null)
  // no note while blocked
  assert.equal(generateAvailability(inputs({ submitting: true }, { source: 'prompt' })).note, null)
})

test('plan kind is reported for every allowed state', () => {
  assert.equal(generateAvailability(inputs()).plan, 'job')
  assert.equal(generateAvailability(inputs({}, { source: 'prompt' })).plan, 'image_generation')
  assert.equal(generateAvailability(inputs({}, { source: 'prompt', prompt: prompt({ flow: 'one_shot' }) })).plan, 'workflow')
})
