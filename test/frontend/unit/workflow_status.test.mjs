// Unit tests for the run-status projection (UI_DESIGN.md §4.3.9, slice F3): every row of the legacy-job table (one
// `fixtures/jobs/*.json` per stage), the workflow step mapping (implicit steps, artifacts → thumbnails, 0..100
// progress, the job's stage label while reconstructing, `done` + `warning_count` → `warning`, failed / cancelled
// runs), the session `runIndex` restriction and the base states without a run.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { test } from 'node:test'

import { FALLBACK_LEGACY_JOB_NODE_MAP } from '../../../frontend/src/lib/featureFlags.ts'
import {
  LEGACY_NODE_ORDER,
  addNode,
  baseStatus,
  legacyJobStatuses,
  projectRunState,
  registryFromCatalog,
  templateFromConfig,
  withNodeStates,
} from '../../../frontend/src/workflow/engine/index.ts'
import { readFixture } from './_fixtures.mjs'

const CATALOG = readFixture('catalog.json')
const FULL = readFixture('features/full.json')
const R = withNodeStates(registryFromCatalog(CATALOG), FULL.nodes)
const LEGACY_MAP = FULL.workflow.legacy_job_node_map
const JOB_NODE_IDS = ['n_input_image', 'n_image_to_3d', 'n_export']
const resolve = (url) => `http://test${url}?token=t`

const job = (name) => readFixture(`jobs/${name}.json`)
const states = (statuses) => statuses.map((s) => s.state)

/** Expected [input_image, image_to_3d, export] per stage fixture (UI_DESIGN §4.3.9 table). */
const LEGACY_TABLE = {
  queued: [['done', 'queued', 'queued'], [null, 'Queued, position 2', null]],
  waiting_for_model: [['done', 'queued', 'queued'], [null, 'Waiting for the model', null]],
  preprocessing: [['running', 'queued', 'queued'], ['Preparing image', null, null]],
  conditioning: [['done', 'running', 'queued'], [null, 'Encoding image', null]],
  sparse_structure: [['done', 'running', 'queued'], [null, 'Building structure', null]],
  shape: [['done', 'running', 'queued'], [null, 'Generating geometry', null]],
  texture: [['done', 'running', 'queued'], [null, 'Generating texture', null]],
  decoding: [['done', 'running', 'queued'], [null, 'Decoding mesh', null]],
  exporting: [['done', 'done', 'running'], [null, null, 'Exporting GLB']],
  finalizing: [['done', 'done', 'running'], [null, null, 'Finalizing']],
  completed: [['done', 'done', 'done'], [null, null, null]],
  failed: [['done', 'failed', 'skipped'], [null, 'CUDA out of memory while generating geometry.', null]],
  cancelled: [['done', 'cancelled', 'skipped'], [null, null, null]],
}

for (const [name, [expectedStates, expectedMessages]] of Object.entries(LEGACY_TABLE)) {
  test(`legacy job table: ${name}`, () => {
    const fixture = job(name)
    const statuses = legacyJobStatuses(fixture, LEGACY_MAP)
    assert.deepEqual(states(statuses), expectedStates)
    assert.deepEqual(statuses.map((s) => s.message), expectedMessages)
    // Only a running Image-to-3D node shows a percentage (the job's).
    statuses.forEach((s, i) => {
      const expected = s.state === 'running' && LEGACY_NODE_ORDER[i] === 'image_to_3d' ? fixture.progress.percent : null
      assert.equal(s.percent, expected, `${name} percent of ${LEGACY_NODE_ORDER[i]}`)
      assert.equal(s.thumbnailUrl, null)
    })
  })
}

test('the fallback LEGACY_JOB_NODE_MAP gives the same table', () => {
  for (const name of Object.keys(LEGACY_TABLE)) {
    assert.deepEqual(states(legacyJobStatuses(job(name), FALLBACK_LEGACY_JOB_NODE_MAP)), LEGACY_TABLE[name][0], name)
  }
})

test('legacy run on the trivial template: compiled nodes lit, the rest keep their base state', () => {
  const doc = templateFromConfig({ source: 'image', segmentation: false, partExtraction: false }, R)
  const view = projectRunState(doc, R, { kind: 'job', job: job('shape'), nodeIds: JOB_NODE_IDS, legacyMap: LEGACY_MAP })
  assert.deepEqual(
    Object.fromEntries(Object.entries(view).map(([id, s]) => [id, s.state])),
    { n_input_image: 'done', n_prompt: 'idle', n_segmentation: 'bypassed', n_image_to_3d: 'running', n_part_extraction: 'bypassed', n_export: 'queued' },
  )
  assert.equal(view.n_image_to_3d.percent, 33.8)
  assert.equal(view.n_image_to_3d.message, 'Generating geometry')
})

test('runIndex restricts to the compiled ids: a node outside them never inherits the run state', () => {
  const doc = templateFromConfig({ source: 'image', segmentation: false, partExtraction: false }, R)
  // The run compiled without an Export node (implicit export); the Export node added later stays idle.
  const view = projectRunState(doc, R, { kind: 'job', job: job('exporting'), nodeIds: ['n_input_image', 'n_image_to_3d'], legacyMap: LEGACY_MAP })
  assert.equal(view.n_image_to_3d.state, 'done')
  assert.equal(view.n_export.state, 'idle')
  // Runs from other sessions (no runIndex entry) map by type.
  const other = projectRunState(doc, R, { kind: 'job', job: job('exporting'), nodeIds: null, legacyMap: LEGACY_MAP })
  assert.equal(other.n_export.state, 'running')
  assert.equal(other.n_prompt.state, 'idle', 'a source type the job does not use')
})

test('workflow steps: done + warning_count → warning, thumbnails, 0..100 progress, job stage label while reconstructing', () => {
  const doc = templateFromConfig({ source: 'prompt', segmentation: true, partExtraction: true }, R)
  const nodeIds = ['n_prompt', 'n_segmentation', 'n_image_to_3d', 'n_part_extraction', 'n_export']
  const workflow = readFixture('workflow_running.json')
  const view = projectRunState(doc, R, { kind: 'workflow', workflow, job: null, nodeIds }, resolve)
  assert.deepEqual(view.n_prompt, {
    state: 'warning',
    percent: null,
    message: null,
    thumbnailUrl: 'http://test/api/v1/image-generations/ig_0123456789ab/candidates/0/thumbnail?token=t',
  })
  assert.equal(view.n_input_image.state, 'idle', 'implicit import_image step: Input Image is parked, not compiled')
  assert.equal(view.n_segmentation.state, 'done')
  assert.equal(view.n_segmentation.thumbnailUrl, 'http://test/api/v1/segmentations/sg_0123456789ab/overlay?token=t')
  assert.deepEqual(view.n_image_to_3d, { state: 'running', percent: 38.5, message: null, thumbnailUrl: null })
  assert.equal(view.n_part_extraction.state, 'queued')
  assert.equal(view.n_export.state, 'queued')

  const withJob = projectRunState(doc, R, { kind: 'workflow', workflow, job: job('decoding'), nodeIds }, resolve)
  assert.deepEqual(withJob.n_image_to_3d, { state: 'running', percent: 75, message: 'Decoding mesh', thumbnailUrl: null })
})

test('workflow steps: implicit steps (node_id null) map by type inside the compiled ids', () => {
  const doc = templateFromConfig({ source: 'prompt', segmentation: false, partExtraction: true }, R)
  const workflow = readFixture('workflow_completed.json')
  const view = projectRunState(doc, R, { kind: 'workflow', workflow, job: null, nodeIds: ['n_prompt', 'n_image_to_3d', 'n_part_extraction', 'n_export'] }, resolve)
  assert.equal(view.n_prompt.state, 'warning', 'text_to_image has warning_count 1')
  assert.equal(view.n_image_to_3d.state, 'done')
  assert.equal(view.n_image_to_3d.thumbnailUrl, 'http://test/api/v1/jobs/job_0123456789ab/preview?token=t')
  assert.equal(view.n_part_extraction.state, 'done')
  assert.equal(view.n_export.state, 'done')
  assert.equal(view.n_segmentation.state, 'bypassed')
  assert.equal(view.n_input_image.state, 'idle')
})

test('a done step without warning_count is done; with warning_count 0 it is done too', () => {
  const doc = templateFromConfig({ source: 'prompt', segmentation: false, partExtraction: false }, R)
  const workflow = readFixture('workflow_completed.json')
  const steps = workflow.steps.map((s) => {
    if (s.id !== 'text_to_image') return s
    const { warning_count: _ignored, ...rest } = s
    return rest
  })
  const view = projectRunState(doc, R, { kind: 'workflow', workflow: { ...workflow, steps }, job: null, nodeIds: null })
  assert.equal(view.n_prompt.state, 'done')
  assert.equal(view.n_image_to_3d.state, 'done')
})

test('failed workflow: the failed step shows the child error; pending steps of a terminal workflow are skipped', () => {
  const doc = templateFromConfig({ source: 'image', segmentation: false, partExtraction: true }, R)
  const workflow = readFixture('workflow_failed.json')
  const view = projectRunState(doc, R, { kind: 'workflow', workflow, job: null, nodeIds: ['n_input_image', 'n_image_to_3d', 'n_part_extraction', 'n_export'] })
  assert.equal(view.n_input_image.state, 'done')
  assert.equal(view.n_image_to_3d.state, 'done')
  assert.deepEqual(view.n_part_extraction, {
    state: 'failed',
    percent: null,
    message: 'The reassembly check failed: positions differ on 3 vertices of part:hair.',
    thumbnailUrl: null,
  })
  assert.equal(view.n_export.state, 'skipped')

  const cancelled = { ...readFixture('workflow_running.json'), status: 'cancelled' }
  const promptDoc = templateFromConfig({ source: 'prompt', segmentation: true, partExtraction: true }, R)
  const cancelledView = projectRunState(promptDoc, R, { kind: 'workflow', workflow: cancelled, job: null, nodeIds: null })
  assert.equal(cancelledView.n_part_extraction.state, 'skipped')
  assert.equal(cancelledView.n_export.state, 'skipped')
})

test('base states without a run: locked, bypassed, unavailable, idle', () => {
  const noT2i = withNodeStates(registryFromCatalog(CATALOG), readFixture('features/no-t2i.json').nodes)
  let doc = templateFromConfig({ source: 'image', segmentation: false, partExtraction: true }, noT2i)
  doc = addNode(doc, noT2i.get('part_refine'), { x: 1280, y: 260 })
  doc = { ...doc, graph: { ...doc.graph, nodes: [...doc.graph.nodes, { id: 'n_mystery', type: 'ui.mystery', version: 1, mode: 'active' }] } }
  const view = projectRunState(doc, noT2i, null)
  assert.deepEqual(
    Object.fromEntries(Object.entries(view).map(([id, s]) => [id, s.state])),
    {
      n_input_image: 'idle',
      n_prompt: 'unavailable',
      n_segmentation: 'bypassed',
      n_image_to_3d: 'idle',
      n_part_extraction: 'idle',
      n_export: 'idle',
      n_part_refine: 'locked',
      n_mystery: 'unavailable',
    },
  )
  assert.match(view.n_prompt.message, /Z-Image-Turbo/)
  assert.equal(view.n_part_refine.message, 'Paused until approved')
  assert.deepEqual(baseStatus({ id: 'n_x', type: 'export', version: 1, mode: 'active' }, R.get('export')), {
    state: 'idle',
    percent: null,
    message: null,
    thumbnailUrl: null,
  })
})

test('a run never lights bypassed or locked nodes', () => {
  const doc = templateFromConfig({ source: 'image', segmentation: false, partExtraction: false }, R)
  const workflow = readFixture('workflow_failed.json')
  const view = projectRunState(doc, R, { kind: 'workflow', workflow, job: null, nodeIds: null })
  assert.equal(view.n_part_extraction.state, 'bypassed')
  assert.equal(view.n_segmentation.state, 'bypassed')
})
