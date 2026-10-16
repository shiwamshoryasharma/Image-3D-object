// Unit tests for lib/runSteps.ts, lib/errorText.ts and the warning texts of lib/featureMeta.ts (UI_DESIGN.md §2.4,
// §2.7, §2.11, §10.2; slice F2): the run stale guard, workflow step views (0..100) vs run step views (0..1), the
// error → text table for every run error code and the API error rows, and the warning text fallback.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { readFixture } from './_fixtures.mjs'
import { describeError, errorText, isStepFailedDetails, locText, RUN_ERROR_TEXT, validationIssues } from '../../../frontend/src/lib/errorText.ts'
import { STEP_LABELS, WARNING_TEXT, stepLabel, warningText } from '../../../frontend/src/lib/featureMeta.ts'
import {
  currentStep,
  isStaleRun,
  isTerminalRunStatus,
  runPercent,
  runStepViews,
  warningViews,
  workflowStepViews,
} from '../../../frontend/src/lib/runSteps.ts'

const rec = (status, updatedAt) => ({ status, progress: { updated_at: updatedAt } })
const T1 = '2026-10-16T10:00:00Z'
const T2 = '2026-10-16T10:00:05Z'

// ------------------------------------------------------------------------------------------------
// Stale guard (§2.4)
// ------------------------------------------------------------------------------------------------

test('isStaleRun: the status rank never decreases', () => {
  assert.equal(isStaleRun(rec('running', T1), rec('queued', T2)), true)
  for (const terminal of ['completed', 'failed', 'cancelled']) {
    assert.equal(isStaleRun(rec(terminal, T1), rec('running', T2)), true)
    assert.equal(isStaleRun(rec(terminal, T1), rec('queued', T2)), true)
    assert.equal(isStaleRun(rec('running', T2), rec(terminal, T1)), false) // a terminal update always lands
    assert.equal(isStaleRun(rec('queued', T2), rec(terminal, T1)), false)
  }
  assert.equal(isStaleRun(rec('queued', T2), rec('running', T1)), false)
})

test('isStaleRun: a terminal record is final (another terminal status is stale; the same one is accepted)', () => {
  assert.equal(isStaleRun(rec('completed', T1), rec('failed', T2)), true)
  assert.equal(isStaleRun(rec('cancelled', T1), rec('completed', T2)), true)
  assert.equal(isStaleRun(rec('completed', T2), rec('completed', T1)), false)
})

test('isStaleRun: equal non-terminal status → an older progress.updated_at is dropped', () => {
  assert.equal(isStaleRun(rec('running', T2), rec('running', T1)), true)
  assert.equal(isStaleRun(rec('running', T1), rec('running', T2)), false)
  assert.equal(isStaleRun(rec('running', T1), rec('running', T1)), false)
  assert.equal(isStaleRun(rec('running', null), rec('running', T1)), false) // unknown timestamps are accepted
  assert.equal(isStaleRun(rec('running', T1), rec('running', null)), false)
})

test('isStaleRun works on the real payload shapes (workflow, part analysis, image generation)', () => {
  const running = readFixture('workflow_running.json')
  const completed = readFixture('workflow_completed.json')
  assert.equal(isStaleRun(completed, running), true)
  assert.equal(isStaleRun(running, completed), false)
  const pa = readFixture('part_analysis_completed.json')
  assert.equal(isStaleRun(pa, { ...pa, status: 'running' }), true)
  const ig = readFixture('image_generation_completed.json')
  assert.equal(isStaleRun(ig, ig), false)
  assert.deepEqual(['queued', 'running', 'completed', 'failed', 'cancelled'].map(isTerminalRunStatus), [false, false, true, true, true])
})

// ------------------------------------------------------------------------------------------------
// Step views
// ------------------------------------------------------------------------------------------------

test('workflow step views: shared labels, 0..100 progress, done + warnings → warning', () => {
  const views = workflowStepViews(readFixture('workflow_running.json').steps)
  assert.deepEqual(views.map((v) => v.label), ['Text to image', 'Import image', 'Segmentation', 'Image to 3D', 'Part extraction', 'Export'])
  assert.deepEqual(views.map((v) => v.status), ['warning', 'done', 'done', 'running', 'pending', 'pending'])
  assert.deepEqual(views.map((v) => v.percent), [100, 100, 100, 38.5, null, null])
  assert.deepEqual(views.map((v) => v.warningCount), [1, 0, 0, 0, 0, 0])
  assert.deepEqual(views.map((v) => v.nodeId), ['n_prompt', null, 'n_segmentation', 'n_image_to_3d', 'n_part_extraction', 'n_export'])
  assert.equal(views[0].artifacts[0].role, 'candidate')
  assert.equal(currentStep(views).id, 'reconstruction')
})

test('workflow step views: done without warning_count → done; skipped keeps its message; failed step is current', () => {
  const completed = workflowStepViews(readFixture('workflow_completed.json').steps)
  assert.equal(completed.find((v) => v.id === 'reconstruction').status, 'done')
  const skipped = completed.find((v) => v.id === 'segmentation')
  assert.deepEqual([skipped.status, skipped.message], ['skipped', 'No segmentation requested'])
  assert.equal(currentStep(completed), null)
  const failed = workflowStepViews(readFixture('workflow_failed.json').steps)
  const extraction = currentStep(failed)
  assert.deepEqual([extraction.id, extraction.status, extraction.percent], ['part_extraction', 'failed', 37])
  // progress values out of range are clamped; a missing warning_count counts as 0
  const odd = workflowStepViews([{ id: 'export', node: 'export', node_id: null, status: 'running', progress: 140, resource: null, artifacts: [] }])
  assert.deepEqual([odd[0].percent, odd[0].warningCount, odd[0].message], [100, 0, null])
})

test('run step views: payload labels, 0..1 progress → percent, warnings counted per step', () => {
  const ig = readFixture('image_generation_completed.json')
  const views = runStepViews(ig.steps, ig.warnings)
  assert.deepEqual(views.map((v) => v.label), ig.steps.map((s) => s.label))
  assert.deepEqual(views.map((v) => v.percent), [100, 100, 100, 100])
  assert.deepEqual(views.map((v) => v.warningCount), [0, 0, 1, 0])
  assert.equal(views.find((v) => v.id === 'check').status, 'warning')
  const partial = runStepViews([
    { id: 'label_lift', label: 'Lifting labels to 3D', status: 'running', message: 'View 3/8', progress: 0.375, duration_s: null, details: {} },
    { id: 'partition', label: '', status: 'pending', message: null, progress: null, duration_s: null, details: {} },
  ])
  assert.deepEqual(partial.map((v) => v.percent), [37.5, null])
  assert.equal(partial[1].label, 'Partition') // empty payload label → humanised id
  assert.equal(currentStep(partial).message, 'View 3/8')
})

test('runPercent clamps progress.percent; stepLabel humanises unknown step ids', () => {
  assert.equal(runPercent({ percent: 41 }), 41)
  assert.equal(runPercent({ percent: 104 }), 100)
  assert.equal(runPercent({ percent: -2 }), 0)
  assert.equal(runPercent(null), null)
  assert.equal(stepLabel('reconstruction'), STEP_LABELS.reconstruction)
  assert.equal(stepLabel('future_step_id'), 'Future step id')
  assert.equal(stepLabel(''), 'Step')
})

// ------------------------------------------------------------------------------------------------
// Run / workflow error texts (§2.11)
// ------------------------------------------------------------------------------------------------

test('step_failed (workflow_failed.json) → "${stepLabel(step)} failed: ${child_error.message}"', () => {
  const wf = readFixture('workflow_failed.json')
  assert.equal(isStepFailedDetails(wf.error.details), true)
  assert.equal(errorText(wf.error), `${stepLabel('part_extraction')} failed: ${wf.error.details.child_error.message}`)
  assert.equal(errorText(wf.error), 'Part extraction failed: The reassembly check failed: positions differ on 3 vertices of part:hair.')
})

test('step_failed with malformed details → the "${label}: ${message}" fallback', () => {
  const base = { code: 'step_failed', message: 'Separating parts failed' }
  assert.equal(errorText({ ...base, details: null }), 'Generation: Separating parts failed')
  assert.equal(errorText({ ...base, details: { step: 'part_extraction' } }), 'Generation: Separating parts failed')
  assert.equal(errorText({ ...base, details: { step: 'x', child_kind: 'robot', child_id: null, child_error: { code: 'c', message: 'm' } } }), 'Generation: Separating parts failed')
  assert.equal(errorText({ ...base, details: null }, { label: 'Prompt to 3D' }), 'Prompt to 3D: Separating parts failed')
  assert.equal(isStepFailedDetails({ step: 'import_image', child_kind: 'job', child_id: null, child_error: { code: 'queue_full', message: 'Queue full', details: null } }), true)
})

test('fixed run error texts', () => {
  const fixed = {
    out_of_memory: 'Ran out of GPU memory. Close other GPU apps and retry.',
    insufficient_vram: 'Not enough free GPU memory for this step.',
    interrupted: 'Interrupted by a server restart.',
    run_timeout: 'The step took too long and was stopped.',
    queue_full: 'A queue was full when the next step started.',
  }
  assert.deepEqual({ ...RUN_ERROR_TEXT }, fixed)
  for (const [code, text] of Object.entries(fixed)) assert.equal(errorText({ code, message: 'server text', details: null }), text, code)
})

test('every other DESIGN §6.8 run error code reads "${label}: ${message}"', () => {
  const codes = ['segmentation_failed', 'camera_fit_failed', 'lifting_failed', 'partition_failed', 'export_failed', 'publish_conflict', 'job_deleted', 'unsupported_base_mesh', 'source_missing', 'generation_failed', 'run_error']
  for (const code of codes) {
    assert.equal(errorText({ code, message: 'It broke.', details: null }, { subject: 'part_analysis' }), 'Part analysis: It broke.', code)
    assert.equal(errorText({ code, message: 'It broke.', details: null }, { subject: 'image_generation' }), 'Image generation: It broke.', code)
    assert.equal(errorText({ code, message: 'It broke.', details: null }), 'Generation: It broke.', code)
  }
  assert.equal(errorText({ code: 'some_future_code', message: '', details: null }, { subject: 'segmentation' }), 'Segmentation: some_future_code')
  const report = describeError({ code: 'partition_failed', message: 'x' })
  assert.deepEqual([report.refreshFeatures, report.field, report.issues, report.expected], [false, null, null, false])
})

// ------------------------------------------------------------------------------------------------
// API error rows (§2.11)
// ------------------------------------------------------------------------------------------------

const api = (status, code, message, details = null) => ({ status, code, message, details })
const validation = (...items) => api(422, 'validation_error', 'Request validation failed', items)

test('422 prompt errors → inline under the prompt field; control characters have their own text', () => {
  const control = describeError(validation({ loc: ['body', 'prompt'], type: 'invalid_characters', msg: 'prompt contains 1 control character(s)' }))
  assert.deepEqual([control.text, control.field], ['Remove control characters from the prompt.', 'prompt'])
  const nested = describeError(validation({ loc: ['body', 'source', 'generation', 'prompt'], type: 'string_too_long', msg: 'String should have at most 2000 characters' }))
  assert.deepEqual([nested.text, nested.field], ['String should have at most 2000 characters.', 'prompt'])
  assert.equal(describeError(validation({ loc: ['body', 'prompt'], type: 'missing', msg: '' })).text, 'The prompt was rejected.')
})

test('422 unsupported_by_model / unsupported_by_engine → names the field and ctx.allowed', () => {
  const size = describeError(validation({ loc: ['body', 'width'], type: 'unsupported_by_model', msg: 'no', ctx: { allowed: [[1024, 1024], [896, 1152]] } }))
  assert.equal(size.text, 'Width is not supported by the model (allowed: 1024×1024, 896×1152).')
  const engine = describeError(validation({ loc: ['body', 'reconstruction', 'settings', 'texture_size'], type: 'unsupported_by_engine', msg: 'no', ctx: { allowed: [1024, 2048] } }))
  assert.equal(engine.text, 'Texture size is not supported by the 3D engine (allowed: 1024, 2048).')
  const noAllowed = describeError(validation({ loc: ['body', 'polygon_target'], type: 'unsupported_by_engine', msg: 'no', ctx: { min: 1, max: 2 } }))
  assert.equal(noAllowed.text, 'Polygon target is not supported by the 3D engine.')
})

test('422 extra_forbidden → version-skew text, features refresh, body logged', () => {
  const r = describeError(validation({ loc: ['body', 'export'], type: 'extra_forbidden', msg: 'Extra inputs are not permitted' }))
  assert.equal(r.text, 'The backend rejected an unknown field (export).')
  assert.deepEqual([r.refreshFeatures, r.logBody], [true, true])
  assert.equal(locText(['body', 'parts', 'export', 'zip']), 'parts.export.zip')
  assert.equal(locText(['body']), 'body')
})

test('422 conflicting_fields → builder-bug text, body logged, no refresh', () => {
  const r = describeError(validation({ loc: ['body', 'segmentation'], type: 'conflicting_fields', msg: 'segmentation requires parts' }))
  assert.equal(r.text, 'The backend rejected the request (conflicting fields: segmentation).')
  assert.deepEqual([r.logBody, r.refreshFeatures], [true, false])
})

test('422 other validation errors → the first message with its location', () => {
  assert.equal(errorText(validation({ loc: ['body', 'num_images'], type: 'less_than_equal', msg: 'Input should be less than or equal to 4' })), 'Num images: Input should be less than or equal to 4.')
  assert.equal(errorText(validation({ loc: ['body', 'graph', 'nodes', 0], type: 'missing', msg: 'Field required' })), 'Nodes: Field required.')
  assert.equal(errorText(validation({ loc: ['body'], type: 'json_invalid', msg: 'Invalid JSON' })), 'Invalid JSON.')
  assert.equal(errorText(api(422, 'validation_error', 'Request validation failed', 'garbage')), 'Request validation failed.')
  assert.deepEqual(validationIssues([null, { loc: ['body', 1, {}], type: 3 }]), [{ loc: ['body', 1], type: '', msg: '', ctx: null }])
})

test('422 invalid_workflow → the server issues for the Problems panel; text = first issue', () => {
  const issues = [
    { severity: 'error', code: 'missing_required_input', message: 'Image-to-3D needs an image input.', node_id: 'n_image_to_3d', port: 'images', edge_index: null, details: null },
    { severity: 'warning', code: 'not_a_code', message: 'dropped', node_id: null, port: null, edge_index: null, details: null },
  ]
  const r = describeError(api(422, 'invalid_workflow', 'The workflow is not valid', { issues }))
  assert.equal(r.text, 'Image-to-3D needs an image input.')
  assert.deepEqual(r.issues.map((i) => i.code), ['missing_required_input'])
  assert.equal(describeError(api(422, 'invalid_workflow', 'The workflow is not valid', null)).text, 'The workflow is not valid.')
})

test('503 feature_disabled / capability_unavailable / model_unavailable → server message (+ reasons) and a features refresh', () => {
  const cap = describeError(
    api(503, 'capability_unavailable', 'Text-to-image is not available', {
      capability: 'text_to_image',
      reasons: [{ model_id: 'z', state: 'not_installed', reason_code: 'not_installed', reason: 'weights are not installed' }],
    }),
  )
  assert.equal(cap.text, 'Text-to-image is not available. Weights are not installed.')
  assert.equal(cap.refreshFeatures, true)
  for (const code of ['feature_disabled', 'model_unavailable']) {
    const r = describeError(api(503, code, 'Part analysis is turned off.', { feature: 'part_analysis', flag: 'IMG3D_PART_ANALYSIS_ENABLED' }))
    assert.deepEqual([r.text, r.refreshFeatures], ['Part analysis is turned off.', true])
  }
  assert.equal(errorText(api(503, 'engine_unavailable', 'Shutting down')), 'The server is shutting down.')
})

test('429 queue_full → the job text for jobs, the run text for runs', () => {
  const full = api(429, 'queue_full', 'Queue is full')
  assert.equal(errorText(full, { subject: 'job' }), 'The generation queue is full.')
  for (const subject of ['workflow', 'image_generation', 'segmentation', 'part_analysis', undefined]) {
    assert.equal(errorText(full, subject ? { subject } : undefined), 'The run queue is full.')
  }
})

test('409 / 404 on a candidate → "no longer available" and a run refresh; latest "none" is not an error', () => {
  for (const status of [404, 409]) {
    const r = describeError(api(status, status === 404 ? 'not_found' : 'conflict', 'x'), { subject: 'candidate' })
    assert.deepEqual([r.text, r.refreshRun], ['That image is no longer available.', true])
  }
  const none = describeError(api(404, 'not_found', 'No completed analysis', { reason: 'none' }))
  assert.equal(none.expected, true)
  // other errors fall back to the server message
  assert.equal(errorText(api(404, 'not_found', 'Workflow not found')), 'Workflow not found.')
  assert.equal(errorText(api(0, 'backend_unreachable', 'Cannot reach the Img3D backend. Make sure it is running.')), 'Cannot reach the Img3D backend. Make sure it is running.')
  assert.equal(errorText(api(500, 'internal_error', '')), 'Something went wrong.')
})

// ------------------------------------------------------------------------------------------------
// Warning texts (§2.11, DESIGN §6.8)
// ------------------------------------------------------------------------------------------------

test('one WARNING_TEXT entry per DESIGN §6.8 warning code', () => {
  const codes = ['t2i_touches_border', 't2i_multiple_subjects', 't2i_coverage_low', 't2i_coverage_high', 't2i_aspect_mismatch', 'profile_degraded', 'frame_unverified', 'head_pass_low_res', 'segmentation_rerun_for_alignment', 'camera_fit_low_iou', 'too_small_for_geometry', 'max_parts_exceeded', 'expected_part_missing', 'charts_cross_boundary', 'low_confidence', 'floater_candidates', 'gpu_defer_timeout']
  assert.deepEqual(Object.keys(WARNING_TEXT).sort(), [...codes].sort())
  assert.equal(warningText('t2i_touches_border', 'server'), 'Touches the image border')
  assert.equal(warningText('gpu_defer_timeout'), 'Waited the maximum time behind image-to-3D jobs, then continued')
})

test('warning text fallback: unknown codes show the server message, else the code', () => {
  assert.equal(warningText('brand_new_warning', 'Something new happened.'), 'Something new happened.')
  assert.equal(warningText('brand_new_warning', '   '), 'brand_new_warning')
  assert.equal(warningText('brand_new_warning'), 'brand_new_warning')
  const views = warningViews([
    ...readFixture('part_analysis_completed.json').warnings,
    { code: 'max_parts_exceeded', message: 'repeated' },
    { code: 'x_new', message: 'First.' },
    { code: 'x_new', message: 'Second.' },
  ])
  assert.deepEqual(views.map((v) => v.text), [
    'Too many parts; the smallest were kept as regions',
    'Small loose pieces were kept on the body',
    'Texture charts cross part borders',
    'First.',
    'Second.',
  ])
})
