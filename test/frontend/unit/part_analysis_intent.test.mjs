// Regression: the default individual-part action must not silently become
// geometry-only connected-components analysis when semantic models are blocked.
import assert from 'node:assert/strict'
import { register } from 'node:module'
import { beforeEach, test } from 'node:test'
import { readFixture } from './_fixtures.mjs'
import { normalizeFeatures } from '../../../frontend/src/lib/featureFlags.ts'
import { DEFAULT_PART_OPTIONS_FORM } from '../../../frontend/src/lib/forms.ts'
import { normalizePartResult } from '../../../frontend/src/lib/partsModel.ts'

const SRC = new URL('../../../frontend/src/', import.meta.url).href
const STUB_CODE = `
export const state = { features: null, form: null, job: null, calls: [], notices: [] };
const store = (getState) => ({ getState });
export const useFeaturesStore = store(() => ({ snapshot: state.features, refresh: async () => {} }));
export const useJobStore = store(() => ({
  viewedJobId: state.job?.id ?? null,
  jobs: state.job ? { [state.job.id]: state.job } : {},
}));
export const usePartOptionsStore = store(() => ({ form: state.form }));
export const usePartStore = store(() => ({
  requestAnalysis: async (jobId, options) => {
    state.calls.push({ jobId, options: structuredClone(options) }); return true;
  },
}));
export const useReferenceStore = store(() => ({ set: null }));
export const useRunStore = store(() => ({ startSegmentationPreview: async () => null }));
export const toast = Object.fromEntries(['warning', 'error', 'info', 'success'].map(
  kind => [kind, (...values) => state.notices.push({ kind, text: values.join(' ') })]
));
`
const STUB = 'data:text/javascript,' + encodeURIComponent(STUB_CODE)
const LOADER = `
const SRC = ${JSON.stringify(SRC)};
const STUB = ${JSON.stringify(STUB)};
export async function resolve(specifier, context, next) {
  if (context.parentURL?.startsWith(SRC) && /^\\.\\.?\\//.test(specifier)) {
    const target = new URL(specifier, context.parentURL).href;
    if (target.startsWith(SRC + 'store/')) return { url: STUB, shortCircuit: true };
    if (!/\\.[cm]?[jt]sx?$/.test(specifier)) {
      try { return await next(specifier + '.ts', context); } catch {}
    }
  }
  return next(specifier, context);
}
`
register('data:text/javascript,' + encodeURIComponent(LOADER))
const { state } = await import(STUB)
const { analyzePartsForViewedJob } = await import(new URL('actions/parts.ts', SRC).href)

beforeEach(() => {
  state.features = normalizeFeatures(readFixture('features/full.json'))
  state.form = { ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [] }
  state.job = { id: 'job_0123456789ab', status: 'completed' }
  state.calls.length = 0
  state.notices.length = 0
})

test('default Split into parts explicitly requests semantic_parts', async () => {
  assert.equal(state.form.strategy, null)
  assert.equal(await analyzePartsForViewedJob(), true)
  assert.equal(state.calls.length, 1)
  assert.equal(state.calls[0].options.strategy, 'semantic_parts')
})

test('unavailable semantic splitting is actionable instead of silently queueing components', async () => {
  state.features = normalizeFeatures(readFixture('features/no-seg.json'))
  assert.equal(state.features.parts.available, true, 'geometry-only fallback remains supported')
  assert.equal(state.features.parts.defaultStrategy, 'components')
  const semantic = state.features.parts.strategies.find(strategy => strategy.id === 'semantic_parts')
  assert.equal(semantic.available, false)
  assert.equal(await analyzePartsForViewedJob(), false)
  assert.equal(state.calls.length, 0)
  assert.ok(state.notices.some(notice => /semantic|segmentation|individual/i.test(notice.text)))
  const reason = (semantic.reason ?? '').replace(/[.\s]+$/, '')
  assert.ok(reason && state.notices.some(notice => notice.text.includes(reason)),
    'show the backend reason that explains how semantic splitting is blocked')
})

test('explicitly selected components remains available as a geometry-only fallback', async () => {
  state.features = normalizeFeatures(readFixture('features/no-seg.json'))
  state.form.strategy = 'components'
  assert.equal(await analyzePartsForViewedJob(), true)
  assert.equal(state.calls.length, 1)
  assert.equal(state.calls[0].options.strategy, 'components')
})

test('explicit semantic_parts selection never falls back to geometry-only components', async () => {
  state.features = normalizeFeatures(readFixture('features/no-seg.json'))
  state.form.strategy = 'semantic_parts'
  assert.equal(await analyzePartsForViewedJob(), false)
  assert.equal(state.calls.length, 0)
  assert.ok(state.notices.length > 0)
})

test('no finished model keeps the existing refusal without posting an analysis', async () => {
  state.job.status = 'running'
  assert.equal(await analyzePartsForViewedJob(), false)
  assert.equal(state.calls.length, 0)
})

test('geometry-only completed results retain components strategy for an explicit UI notice', () => {
  const run = readFixture('part_analysis_completed.json')
  run.options.strategy = 'components'
  run.result.unlabelled_face_ratio = 1
  const normalized = normalizePartResult(run, null, state.features)
  assert.equal(normalized.ok, true)
  assert.equal(normalized.set.strategy, 'components')
  assert.equal(normalized.set.quality.unlabelledFaceRatio, 1)
})

test('manifest strategy supplies result method when an older run omitted effective options', () => {
  const run = readFixture('part_analysis_completed.json')
  delete run.options.strategy
  const manifest = readFixture('manifest_parts.json')
  manifest.strategy.id = 'components'
  const normalized = normalizePartResult(run, manifest, state.features)
  assert.equal(normalized.ok, true)
  assert.equal(normalized.set.strategy, 'components')
})

test('poor semantic coverage is not mislabeled as components merely because all faces are unlabelled', () => {
  const run = readFixture('part_analysis_completed.json')
  run.options.strategy = 'semantic_parts'
  run.result.unlabelled_face_ratio = 1
  const normalized = normalizePartResult(run, null, state.features)
  assert.equal(normalized.ok, true)
  assert.equal(normalized.set.strategy, 'semantic_parts')
})

test('run effective strategy takes precedence over stale manifest strategy', () => {
  const run = readFixture('part_analysis_completed.json')
  run.options.strategy = 'components'
  const manifest = readFixture('manifest_parts.json')
  manifest.strategy.id = 'semantic_parts'
  const normalized = normalizePartResult(run, manifest, state.features)
  assert.equal(normalized.ok, true)
  assert.equal(normalized.set.strategy, 'components')
})



const { buildPartAnalysisOptions, buildWorkflowRequest } = await import(new URL('lib/workflowRequest.ts', SRC).href)
const { planGeneration } = await import(new URL('lib/generatePlan.ts', SRC).href)
const { generateAvailability } = await import(new URL('lib/generateAvailability.ts', SRC).href)

const enabledParts = (patch = {}) => ({ ...DEFAULT_PART_OPTIONS_FORM, extraTerms: [], enabled: true, ...patch })
function planInputs(features, patch = {}) {
  const sampler = () => ({ steps: null, guidance_strength: null, guidance_rescale: null, rescale_t: null })
  return {
    features, source: 'image', prompt: { prompt: '' },
    parts: enabledParts(),
    references: { id: 'rs_0123456789ab', primaryImageId: 'img_0123456789ab', imageCount: 1 },
    form: { ...readFixture('requests/G1.json').settings,
      advanced: { sparse_structure: sampler(), shape: sampler(), texture: sampler() } },
    referenceMode: 'primary_only', ...patch,
  }
}

test('shared builder requests semantic_parts even when backend advertises components fallback', () => {
  const features = normalizeFeatures(readFixture('features/no-seg.json'))
  const options = buildPartAnalysisOptions(enabledParts(), { features })
  assert.equal(features.parts.defaultStrategy, 'components')
  assert.equal(options.strategy, 'semantic_parts')
})

test('workflow builder preserves semantic intent instead of delegating to components fallback', () => {
  const features = normalizeFeatures(readFixture('features/no-seg.json'))
  const request = buildWorkflowRequest({
    source: { kind: 'job', job_id: 'job_0123456789ab' },
    partOptions: enabledParts(),
    settings: readFixture('requests/G1.json').settings,
  }, { features })
  assert.equal(request.parts.strategy, 'semantic_parts')
})

test('generation plan blocks unavailable semantic parts with its model reason', () => {
  const features = normalizeFeatures(readFixture('features/no-seg.json'))
  const plan = planGeneration(planInputs(features))
  assert.equal(plan.kind, 'blocked')
  assert.equal(plan.code, 'parts_unavailable')
  assert.match(plan.reason, /semantic|segmentation|individual/i)
  const semantic = features.parts.strategies.find(strategy => strategy.id === 'semantic_parts')
  assert.ok(plan.reason.includes(semantic.reason.replace(/[.\s]+$/, '')))
})

test('generate availability also disables unavailable semantic output before generation', () => {
  const features = normalizeFeatures(readFixture('features/no-seg.json'))
  const result = generateAvailability({
    reachable: true,
    system: { engine: { state: 'ready', message: null, error: null },
      queue: { pending: 0, running_job_id: null, max_size: 4 } },
    plan: planInputs(features), uploading: false, activeJobStatus: null,
    activeWorkflowStatus: null, reviewGenerationStatus: null, submitting: false,
    pipelineBlock: null, nodeShellActive: false,
  })
  assert.equal(result.canGenerate, false)
  assert.equal(result.code, 'parts_unavailable')
  assert.match(result.reason, /semantic|segmentation|individual/i)
})

test('explicit geometry fallback still has an available workflow generation plan', () => {
  const features = normalizeFeatures(readFixture('features/no-seg.json'))
  const plan = planGeneration(planInputs(features, { parts: enabledParts({ strategy: 'components' }) }))
  assert.equal(plan.kind, 'workflow')
  assert.equal(plan.request.parts.strategy, 'components')
})
