/**
 * Contract smoke test for the Auto Mesh Tuner part of the frontend mock backend (test/frontend/mock_backend.mjs).
 * Starts the mock per scenario on a free port and checks the DESIGN.md §6 shapes the frontend codes against:
 * runtime detection/selection, the exact needs_download prompt, a confirmed download streaming
 * resolving→downloading→verifying→extracting→validating→completed over `blender.updated`, and an Auto Tune run
 * streaming the 8 steps over `tuning.updated` with a report, previews and a distinct tuned GLB.
 *
 *   node test/frontend/mesh_tuner_mock_smoke.mjs      (Node >= 22: global fetch + WebSocket)
 *
 * Writes a JSON summary to test/outputs/mesh_tuner/frontend/mock_smoke.json and exits non-zero on failure.
 */
import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:net'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'

const HERE = dirname(fileURLToPath(import.meta.url))
const MOCK = join(HERE, 'mock_backend.mjs')
const OUT_DIR = join(HERE, '..', 'outputs', 'mesh_tuner', 'frontend')
const STEP_IDS = ['load', 'analyze', 'geometry_repair', 'face_refinement', 'topology_tuning', 'rig_optimization', 'validation', 'export']
const OLD_ONLY_MESSAGE =
  'Blender 4.2.17 is installed but Auto Mesh Tuner requires Blender 5.1 or newer. Download a supported portable Blender?'
const NONE_MESSAGE =
  'No Blender installation was found. Auto Mesh Tuner requires Blender 5.1 or newer. Download a supported portable Blender?'

const results = []
let failures = 0

function check(name, condition, detail = '') {
  results.push({ name, ok: !!condition, detail })
  if (!condition) failures += 1
  console.log(`${condition ? 'PASS' : 'FAIL'} ${name}${detail ? ` — ${detail}` : ''}`)
}

function freePort() {
  return new Promise((resolve) => {
    const server = createServer()
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      server.close(() => resolve(port))
    })
  })
}

async function startMock(extraArgs) {
  const port = await freePort()
  const child = spawn(process.execPath, [MOCK, '--port', String(port), '--engine-ready', ...extraArgs], { stdio: ['ignore', 'pipe', 'pipe'] })
  await new Promise((resolve, reject) => {
    const timer = setTimeout(() => reject(new Error('mock did not start')), 10_000)
    child.stdout.on('data', (chunk) => {
      if (String(chunk).includes('listening')) {
        clearTimeout(timer)
        resolve()
      }
    })
    child.on('exit', (code) => reject(new Error(`mock exited early (${code})`)))
  })
  const base = `http://127.0.0.1:${port}/api/v1`
  return { base, port, stop: () => child.kill() }
}

async function api(base, method, path, body) {
  const response = await fetch(`${base}${path}`, {
    method,
    headers: body === undefined ? {} : { 'Content-Type': 'application/json' },
    body: body === undefined ? undefined : JSON.stringify(body),
  })
  const type = response.headers.get('content-type') ?? ''
  const data = type.includes('json') ? await response.json() : Buffer.from(await response.arrayBuffer())
  return { status: response.status, data, headers: response.headers }
}

/** Collect WS frames of one type until `done(data)` is true. */
function collect(port, type, done, timeoutMs = 40_000) {
  return new Promise((resolve, reject) => {
    const frames = []
    const socket = new WebSocket(`ws://127.0.0.1:${port}/api/v1/ws`)
    const timer = setTimeout(() => {
      socket.close()
      reject(new Error(`timed out waiting for ${type} (${frames.length} frames)`))
    }, timeoutMs)
    socket.onmessage = (event) => {
      const message = JSON.parse(String(event.data))
      if (message.type !== type) return
      frames.push(message.data)
      if (done(message.data)) {
        clearTimeout(timer)
        socket.close()
        resolve(frames)
      }
    }
    socket.onerror = () => reject(new Error('websocket error'))
  })
}

async function scenarioReady() {
  const mock = await startMock(['--blender-scenario', 'ready'])
  try {
    const { data: runtime } = await api(mock.base, 'GET', '/blender/runtime')
    check('ready: state ready', runtime.state === 'ready', runtime.state)
    check('ready: 5.2.2 LTS selected', runtime.selected?.version === '5.2.2' && runtime.selected.is_lts && runtime.selected.kind === 'system')
    const old = runtime.installations.find((install) => install.version === '4.2.17')
    check('ready: 4.2.17 listed as incompatible', !!old && !old.compatible && /older than 5\.1/.test(old.incompatible_reason ?? ''), old?.incompatible_reason)
    check('ready: installations carry selected flag', runtime.installations.filter((install) => install.selected).length === 1)
    const bad = await api(mock.base, 'PUT', '/blender/runtime/selection', { mode: 'system', executable: old.executable })
    check('ready: pinning 4.2.17 → 422 blender_incompatible', bad.status === 422 && bad.data.error.code === 'blender_incompatible')
    const missing = await api(mock.base, 'PUT', '/blender/runtime/selection', { mode: 'system', executable: 'C:\\nope\\blender.exe' })
    check('ready: unknown exe → 404 blender_not_found', missing.status === 404 && missing.data.error.code === 'blender_not_found')
    const embedded = await api(mock.base, 'PUT', '/blender/runtime/selection', { mode: 'embedded' })
    check('ready: embedded mode without embedded → fallback warning', embedded.data.state === 'ready' && embedded.data.warnings.some((w) => w.startsWith('Embedded Blender is selected but not installed')), embedded.data.warnings[0])
    const detect = await api(mock.base, 'POST', '/blender/runtime/detect')
    check('ready: detect returns runtime', detect.status === 200 && detect.data.state === 'ready' && detect.data.scan_duration_ms > 0)
  } finally {
    mock.stop()
  }
}

async function scenarioOldOnlyDownload() {
  const mock = await startMock(['--blender-scenario', 'old-only', '--download-seconds', '3'])
  try {
    const { data: runtime } = await api(mock.base, 'GET', '/blender/runtime')
    check('old-only: needs_download', runtime.state === 'needs_download' && runtime.selected === null, runtime.state)
    check('old-only: exact prompt', runtime.message === OLD_ONLY_MESSAGE, runtime.message)
    const release = await api(mock.base, 'POST', '/blender/runtime/release')
    check('old-only: release resolved', release.data.version === '5.2.2' && release.data.sha256?.length === 64 && new URL(release.data.url).host === 'download.blender.org')
    const unconfirmed = await api(mock.base, 'POST', '/blender/runtime/download', {})
    check('old-only: download without confirm → 422', unconfirmed.status === 422)
    const framesPromise = collect(mock.port, 'blender.updated', (data) => ['completed', 'failed'].includes(data.download?.state))
    await new Promise((resolve) => setTimeout(resolve, 150))
    const started = await api(mock.base, 'POST', '/blender/runtime/download', { confirm: true, version: '5.2.2' })
    check('old-only: confirmed download → 202', started.status === 202, String(started.status))
    const again = await api(mock.base, 'POST', '/blender/runtime/download', { confirm: true })
    check('old-only: second download → 409', again.status === 409 && again.data.error.code === 'blender_download_in_progress')
    const frames = await framesPromise
    const states = [...new Set(frames.map((frame) => frame.download?.state))]
    check('old-only: download phases streamed', ['downloading', 'verifying', 'extracting', 'validating', 'completed'].every((s) => states.includes(s)), states.join('→'))
    const withSpeed = frames.find((frame) => frame.download?.state === 'downloading' && frame.download.speed_bps > 0 && frame.download.eta_s !== null)
    check('old-only: bytes/speed/ETA reported', !!withSpeed)
    const last = frames.at(-1)
    check('old-only: embedded 5.2.2 selected after install', last.state === 'ready' && last.selected?.kind === 'embedded' && last.download.verified === true, last.selection_reason)
  } finally {
    mock.stop()
  }
}

async function scenarioNone() {
  const mock = await startMock(['--blender-scenario', 'none', '--download-seconds', '20'])
  try {
    const { data: runtime } = await api(mock.base, 'GET', '/blender/runtime')
    check('none: exact prompt', runtime.message === NONE_MESSAGE, runtime.message)
    await api(mock.base, 'POST', '/blender/runtime/download', { confirm: true })
    await new Promise((resolve) => setTimeout(resolve, 1500))
    const cancelled = await api(mock.base, 'DELETE', '/blender/runtime/download')
    check('none: cancel → cancelled + needs_download', cancelled.status === 202 && cancelled.data.download.state === 'cancelled' && cancelled.data.state === 'needs_download')
    await api(mock.base, 'POST', '/blender/runtime/download', { confirm: true })
    const { data: resumed } = await api(mock.base, 'GET', '/blender/runtime')
    check('none: restart resumes partial', resumed.download.resumed === true && resumed.state === 'downloading')
  } finally {
    mock.stop()
  }
}

async function scenarioTuning() {
  const mock = await startMock(['--blender-scenario', 'ready', '--seed-jobs', '1', '--tuning-seconds', '4'])
  try {
    const { data: list } = await api(mock.base, 'GET', '/jobs')
    const job = list.items[0]
    check('tuning: seeded completed job', job?.status === 'completed')
    const framesPromise = collect(mock.port, 'tuning.updated', (data) => ['completed', 'failed', 'cancelled'].includes(data.status))
    await new Promise((resolve) => setTimeout(resolve, 150))
    const created = await api(mock.base, 'POST', `/jobs/${job.id}/tunings`, { preset: 'strong', quads: true })
    check('tuning: POST → 202 queued', created.status === 202 && created.data.status === 'queued' && created.data.steps.length === 8)
    check('tuning: effective options echoed', created.data.options.preset === 'strong' && created.data.options.smoothing === 0.8 && created.data.options.quads === true)
    const frames = await framesPromise
    const run = frames.at(-1)
    check('tuning: completed', run.status === 'completed', run.status)
    check('tuning: 8 step ids in order', JSON.stringify(run.steps.map((step) => step.id)) === JSON.stringify(STEP_IDS))
    const runningSeen = new Set(frames.flatMap((frame) => frame.steps.filter((step) => step.status === 'running').map((step) => step.id)))
    check('tuning: every step streamed as running', STEP_IDS.every((id) => runningSeen.has(id)), [...runningSeen].join(','))
    check('tuning: progress monotonic', frames.every((frame, i) => i === 0 || frame.progress.percent >= frames[i - 1].progress.percent))
    check('tuning: metrics/validation/warnings', run.metrics.length > 5 && run.validation?.checks.length > 0 && run.warnings.length > 0)
    const urls = run.result
    check('tuning: unique tuned model url', urls.model_url === `/api/v1/tunings/${run.id}/model.glb`)
    const tuned = await api(mock.base, 'GET', `/tunings/${run.id}/model.glb`)
    const original = await api(mock.base, 'GET', `/jobs/${job.id}/model.glb`)
    check('tuning: tuned GLB differs from original', tuned.status === 200 && tuned.data.subarray(0, 4).toString() === 'glTF' && !tuned.data.equals(original.data))
    const exported = await api(mock.base, 'GET', `/tunings/${run.id}/export/glb`)
    check('tuning: export attachment name', /img3d_job_\w+_tuned_tn_\w+\.glb/.test(exported.headers.get('content-disposition') ?? ''))
    const preview = await api(mock.base, 'GET', `/tunings/${run.id}/previews/${urls.previews[0].name}`)
    check('tuning: preview is PNG', preview.status === 200 && preview.data.subarray(1, 4).toString() === 'PNG', `${urls.previews.length} previews`)
    const report = await api(mock.base, 'GET', `/tunings/${run.id}/report`)
    check('tuning: report.json schema 1', report.data.schema === 1 && report.data.steps.length === 8)
    const listed = await api(mock.base, 'GET', `/jobs/${job.id}/tunings`)
    check('tuning: list newest first', listed.data.items[0].id === run.id)
    const deleted = await api(mock.base, 'DELETE', `/tunings/${run.id}`)
    check('tuning: delete finished run → 204', deleted.status === 204)
  } finally {
    mock.stop()
  }
}

async function scenarioTuningGuards() {
  const mock = await startMock(['--blender-scenario', 'old-only', '--seed-jobs', '1'])
  try {
    const { data: list } = await api(mock.base, 'GET', '/jobs')
    const blocked = await api(mock.base, 'POST', `/jobs/${list.items[0].id}/tunings`, {})
    check('guards: 503 blender_unavailable with prompt', blocked.status === 503 && blocked.data.error.code === 'blender_unavailable' && blocked.data.error.message === OLD_ONLY_MESSAGE)
  } finally {
    mock.stop()
  }
}

const started = Date.now()
for (const scenario of [scenarioReady, scenarioOldOnlyDownload, scenarioNone, scenarioTuning, scenarioTuningGuards]) {
  try {
    await scenario()
  } catch (error) {
    check(`${scenario.name} crashed`, false, String(error))
  }
}
const summary = { passed: results.length - failures, failed: failures, seconds: (Date.now() - started) / 1000, results }
mkdirSync(OUT_DIR, { recursive: true })
writeFileSync(join(OUT_DIR, 'mock_smoke.json'), JSON.stringify(summary, null, 2))
console.log(`\n${summary.passed} passed, ${summary.failed} failed in ${summary.seconds.toFixed(1)}s`)
process.exit(failures ? 1 : 0)
