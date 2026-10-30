/**
 * Contract smoke test of the part-aware frontend mock backend (UI_DESIGN.md §10.5): checks the DESIGN.md §6 shapes the
 * four UI modes code against, for all five `--features` scenarios (switched at runtime via POST /api/__test/scenario).
 *
 *   node test/frontend/ui_modes_mock_smoke.mjs                                  starts its own mock on a free port
 *   node test/frontend/ui_modes_mock_smoke.mjs --base http://127.0.0.1:8765     uses a running mock (§10.1); its request
 *                                                                               log is read but never cleared
 *
 * Per scenario: `/features` shape (or 404); the golden bodies G1–G8 of fixtures/requests are accepted (202/201) or
 * refused with the scenario's status; each golden body plus one unknown key → 422 `extra_forbidden`; G2a with `export`
 * at the top level → 422 `extra_forbidden`; G2a without `parts` → 422 `conflicting_fields`. Then: strict-mode probes
 * inside nested objects (`layout` stays opaque); a G3 workflow streams workflow.updated → job.updated → completed; a G2
 * workflow ends with part_analysis.updated completed, its manifest and assembled GLB load (node names `part:<id>`,
 * `_FEATURE_ID_0` with byteStride 4 on `part:body`); `latest` → 404 `details.reason: "none"` for a fresh job; the
 * `/workflows/validate` cases of fixtures/graphs; the parts scenarios; model warm-up; queue_full; the tuner flag; the
 * request log (raw `body`, `created_id`) and UI3 (zero `/blender` or `/tunings` requests in the whole log).
 *
 * Writes test/outputs/frontend/ui_modes_smoke.json; exit code 1 on any failure. Node >= 22 (global fetch/WebSocket).
 */
import { spawn } from 'node:child_process'
import { mkdirSync, writeFileSync } from 'node:fs'
import { createServer } from 'node:net'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { gray16Png, parseGlb, readAccessor } from './mock/glb_parts.mjs'
import { listFixtures, readFixture, readFixtureText, withIds } from './unit/_fixtures.mjs'

const HERE = dirname(fileURLToPath(import.meta.url))
const MOCK = join(HERE, 'mock_backend.mjs')
const OUT_DIR = join(HERE, '..', 'outputs', 'frontend')
const SCENARIOS = ['full', 'no-t2i', 'no-seg', 'disabled', 'legacy404']
const GOLDEN = {
  G1: ['POST', '/jobs'], G1a: ['POST', '/jobs'], G1b: ['POST', '/jobs'], G2: ['POST', '/workflows'], G2a: ['POST', '/workflows'],
  G3: ['POST', '/workflows'], G4: ['POST', '/workflows'], G5: ['POST', '/image-generations'], G6: ['POST', '/image-generations/{ig}/candidates/0/use'],
  G7: ['POST', '/workflows'], G8: ['POST', '/jobs/{job}/part-analyses'], G8a: ['POST', '/jobs/{job}/part-analyses'],
}
const FEATURES_KEYS = Object.keys(readFixture('features/full.json'))

const args = process.argv.slice(2)
const results = []
let failures = 0

function check(name, condition, detail = '') {
  results.push({ name, ok: !!condition, detail: String(detail) })
  if (!condition) failures += 1
  console.log(`${condition ? 'PASS' : 'FAIL'} ${name}${detail !== '' ? ` — ${detail}` : ''}`)
}

const sleep = (ms) => new Promise((resolve) => setTimeout(resolve, ms))

function freePort() {
  return new Promise((resolve) => {
    const server = createServer()
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address()
      server.close(() => resolve(port))
    })
  })
}

async function startMock() {
  const port = await freePort()
  const child = spawn(process.execPath, [MOCK, '--port', String(port), '--engine-ready', '--seed-jobs', '2', '--features', 'full', '--strict-requests', 'on'], { stdio: ['ignore', 'pipe', 'pipe'] })
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
  return { root: `http://127.0.0.1:${port}`, stop: () => child.kill() }
}

// ------------------------------------------------------------------------------------------------
// HTTP + WebSocket helpers
// ------------------------------------------------------------------------------------------------
let ROOT = ''
const API = () => `${ROOT}/api/v1`

async function request(method, path, body, { raw = false, headers = {} } = {}) {
  const init = { method, headers: { ...headers } }
  if (body !== undefined) {
    init.body = raw ? body : JSON.stringify(body)
    if (!(body instanceof FormData)) init.headers['Content-Type'] = 'application/json'
  }
  // `path`: absolute URL, a payload URL (`/api/v1/…`, as the backend returns them) or a route below /api/v1.
  const url = path.startsWith('http') ? path : path.startsWith('/api/') ? `${ROOT}${path}` : `${API()}${path}`
  const response = await fetch(url, init)
  const type = response.headers.get('content-type') ?? ''
  const data = response.status === 204 || response.status === 304 ? null : type.includes('json') ? await response.json() : Buffer.from(await response.arrayBuffer())
  return { status: response.status, data, headers: response.headers }
}

const testRoute = (method, name, body) => request(method, `${ROOT}/api/__test/${name}`, body)
const setScenario = (patch) => testRoute('POST', 'scenario', patch)

/** One socket for the whole run; `waitFrame(predicate)` resolves with the first matching frame (past or future). */
const frames = []
const waiters = new Set()
let socket = null
function openSocket() {
  return new Promise((resolve, reject) => {
    socket = new WebSocket(`${ROOT.replace('http', 'ws')}/api/v1/ws`)
    socket.onopen = () => resolve()
    socket.onerror = () => reject(new Error('websocket error'))
    socket.onmessage = (event) => {
      const message = JSON.parse(String(event.data))
      message.seq = frames.length
      frames.push(message)
      for (const waiter of waiters) if (waiter.predicate(message)) { waiters.delete(waiter); waiter.resolve(message) }
    }
  })
}
function waitFrame(predicate, timeoutMs = 60_000, fromSeq = 0) {
  const past = frames.find((frame) => frame.seq >= fromSeq && predicate(frame))
  if (past) return Promise.resolve(past)
  return new Promise((resolve, reject) => {
    const waiter = { predicate: (frame) => frame.seq >= fromSeq && predicate(frame), resolve }
    waiters.add(waiter)
    setTimeout(() => { if (waiters.delete(waiter)) reject(new Error('timed out waiting for a WebSocket frame')) }, timeoutMs)
  })
}

async function poll(path, done, timeoutMs = 60_000) {
  const until = Date.now() + timeoutMs
  for (;;) {
    const response = await request('GET', path)
    if (done(response.data)) return response.data
    if (Date.now() > until) throw new Error(`timed out polling ${path}`)
    await sleep(250)
  }
}

const errorCode = (response) => response.data?.error?.code
const detailTypes = (response) => (response.data?.error?.details ?? []).map((item) => item.type)

/** Cancel whatever a golden request created, so the serial job queue stays short. */
async function cleanup(id) {
  if (!id) return
  const route = id.startsWith('job_') ? `/jobs/${id}/cancel` : id.startsWith('wf_') ? `/workflows/${id}/cancel` : id.startsWith('ig_') ? `/image-generations/${id}/cancel` : id.startsWith('pa_') ? `/part-analyses/${id}/cancel` : null
  if (route) await request('POST', route)
}

// ------------------------------------------------------------------------------------------------
// Checks
// ------------------------------------------------------------------------------------------------
async function uploadSample() {
  const set = await request('POST', '/reference-sets')
  const form = new FormData()
  form.append('file', new Blob([gray16Png(96, 96, (x, y) => ((x - 48) ** 2 + (y - 48) ** 2 < 1200 ? 52000 : 9000))], { type: 'image/png' }), 'smoke.png')
  form.append('slot', 'front')
  form.append('make_primary', 'true')
  const upload = await request('POST', `/reference-sets/${set.data.id}/images`, form, { raw: true })
  check('setup: reference set + image upload', set.status === 201 && upload.status === 201 && upload.data.image.source === null, `${set.status}/${upload.status}`)
  return { rs: set.data.id, img: upload.data.image.id }
}

function expectedStatus(id, scenario) {
  if (id.startsWith('G1')) return 202
  if (scenario === 'legacy404') return 404
  if (scenario === 'disabled') return 503
  if (scenario === 'no-t2i' && ['G3', 'G4', 'G5'].includes(id)) return 503
  if (scenario === 'no-seg' && ['G2', 'G2a', 'G4', 'G7', 'G8', 'G8a'].includes(id)) return 503
  return id === 'G6' ? 201 : 202
}

const expectedCode = (scenario) => ({ disabled: 'feature_disabled', 'no-t2i': 'capability_unavailable', 'no-seg': 'capability_unavailable', legacy404: 'not_found' })[scenario]

async function checkFeatures(scenario) {
  const response = await request('GET', '/features')
  if (scenario === 'legacy404') return check(`${scenario}: GET /features → 404 (old backend)`, response.status === 404, response.status)
  const fixture = readFixture(`features/${scenario}.json`)
  const same = Object.keys(fixture.pipeline).every((key) => response.data.pipeline[key].available === fixture.pipeline[key].available && response.data.pipeline[key].code === fixture.pipeline[key].code)
  check(`${scenario}: GET /features = FeaturesOut of fixtures/features/${scenario}.json`, response.status === 200 && response.data.schema === 'img3d.features/1' && JSON.stringify(Object.keys(response.data)) === JSON.stringify(FEATURES_KEYS) && same && response.data.workflow.available === fixture.workflow.available, `${response.status} ${Object.keys(response.data ?? {}).length} keys`)
  const catalog = await request('GET', '/workflows/catalog')
  check(`${scenario}: catalog etag = features.workflow.catalog_etag`, catalog.status === 200 && catalog.headers.get('etag') === catalog.data.etag && response.data.workflow.catalog_etag === catalog.data.etag)
}

async function goldenBodies(scenario, ids, state) {
  for (const [id, [method, template]] of Object.entries(GOLDEN)) {
    const path = template.replace('{ig}', state.igId ?? 'ig_000000000000').replace('{job}', state.jobId)
    const text = withIds(readFixtureText(`requests/${id}.json`), { rs: ids.rs, img: ids.img })
    const expected = expectedStatus(id, scenario)
    const response = await request(method, path, text, { raw: true })
    const okCode = expected >= 400 ? errorCode(response) === expectedCode(scenario) : true
    check(`${scenario}: ${id} ${method} ${template} → ${expected}${expected >= 400 ? ` ${expectedCode(scenario)}` : ''}`, response.status === expected && okCode, `${response.status} ${errorCode(response) ?? ''}`)
    const created = response.status < 300 ? response.data.id ?? response.data.image?.id : null
    if (scenario === 'full' && id === 'G1') state.g1 = { text, jobId: created }
    if (created && ['full', 'no-seg'].includes(scenario) && ['G2', 'G3'].includes(id)) state[`${scenario}:${id}`] = created
    else await cleanup(created)
    const unknown = await request(method, path, text.replace(/^\{/, '{"x_unknown_key":1,'), { raw: true })
    const strictExpected = scenario === 'legacy404' && !id.startsWith('G1') ? 404 : 422
    check(`${scenario}: ${id} + unknown key → ${strictExpected}${strictExpected === 422 ? ' extra_forbidden' : ''}`, unknown.status === strictExpected && (strictExpected === 404 || detailTypes(unknown).includes('extra_forbidden')), `${unknown.status} ${detailTypes(unknown).join(',')}`)
  }
  const g2a = JSON.parse(withIds(readFixtureText('requests/G2a.json'), ids))
  const topExport = { ...g2a, export: g2a.parts.export }
  delete topExport.parts.export
  const movedExport = await request('POST', '/workflows', topExport)
  const noParts = { ...JSON.parse(withIds(readFixtureText('requests/G2a.json'), ids)) }
  delete noParts.parts
  const withoutParts = await request('POST', '/workflows', noParts)
  if (scenario === 'legacy404') {
    check(`${scenario}: G2a variants → 404`, movedExport.status === 404 && withoutParts.status === 404)
    return
  }
  check(`${scenario}: G2a with a top-level export → 422 extra_forbidden`, movedExport.status === 422 && movedExport.data.error.details.some((item) => item.type === 'extra_forbidden' && item.loc.join('.') === 'body.export'), detailTypes(movedExport).join(','))
  check(`${scenario}: G2a without parts → 422 conflicting_fields`, withoutParts.status === 422 && detailTypes(withoutParts).includes('conflicting_fields'), detailTypes(withoutParts).join(','))
}

async function strictProbes(ids) {
  const g7 = JSON.parse(withIds(readFixtureText('requests/G7.json'), ids))
  const g2 = JSON.parse(withIds(readFixtureText('requests/G2.json'), ids))
  const g1a = JSON.parse(withIds(readFixtureText('requests/G1a.json'), ids))
  const cases = [
    ['POST /segmentations source', '/segmentations', { source: { kind: 'reference_image', reference_set_id: ids.rs, image_id: ids.img, x_unknown: 1 } }, 'body.source.x_unknown'],
    ['reconstruction', '/workflows', { ...g2, reconstruction: { ...g2.reconstruction, x_unknown: 1 } }, 'body.reconstruction.x_unknown'],
    ['settings.advanced.shape', '/jobs', { ...g1a, settings: { ...g1a.settings, advanced: { shape: { steps: 20, x_unknown: 1 } } } }, 'body.settings.advanced.shape.x_unknown'],
    ['graph.nodes[0]', '/workflows', { ...g7, graph: { ...g7.graph, nodes: [{ ...g7.graph.nodes[0], x_unknown: 1 }, ...g7.graph.nodes.slice(1)] } }, 'body.graph.nodes.0.x_unknown'],
    ['graph.edges[0].from', '/workflows', { ...g7, graph: { ...g7.graph, edges: [{ ...g7.graph.edges[0], from: { ...g7.graph.edges[0].from, x_unknown: 1 } }, ...g7.graph.edges.slice(1)] } }, 'body.graph.edges.0.from.x_unknown'],
  ]
  for (const [label, path, body, loc] of cases) {
    const response = await request('POST', path, body)
    check(`strict: unknown key inside ${label} → 422 extra_forbidden`, response.status === 422 && response.data.error.details.some((item) => item.type === 'extra_forbidden' && item.loc.join('.') === loc), `${response.status} ${JSON.stringify(response.data?.error?.details?.[0]?.loc ?? null)}`)
  }
  const layout = await request('POST', '/workflows', { ...g7, layout: { ...g7.layout, anything: { goes: [1, { deep: true }] } } })
  check('strict: extra keys inside layout pass (opaque)', layout.status === 202, layout.status)
  await cleanup(layout.data?.id)
  const segId = await request('POST', '/workflows', { ...g2, parts: { ...g2.parts, segmentation_id: 'sg_0123456789ab' } })
  check('strict: non-null parts.segmentation_id → 422 conflicting_fields', segId.status === 422 && segId.data.error.details.some((item) => item.type === 'conflicting_fields' && item.loc.join('.') === 'body.parts.segmentation_id'), detailTypes(segId).join(','))
  const both = await request('POST', '/workflows', { ...g7, source: g2.source })
  check('strict: graph + simple-form field → 422 conflicting_fields', both.status === 422 && detailTypes(both).includes('conflicting_fields'))
  const many = await request('POST', '/workflows', { ...JSON.parse(withIds(readFixtureText('requests/G3.json'), ids)), source: { kind: 'prompt', generation: { prompt: 'a knight', num_images: 2 } } })
  check('strict: num_images ≠ 1 inside a workflow → 422', many.status === 422, many.status)
  const envelope = await request('POST', '/jobs', { reference_set_id: ids.rs, primary_image_id: ids.img, settings: {}, export: {} })
  check('strict: 422 envelope {error: {code, message, details[{loc, type, msg}], request_id}}', envelope.status === 422 && envelope.data.error.code === 'validation_error' && envelope.data.error.message === 'Request validation failed' && typeof envelope.data.error.request_id === 'string' && ['loc', 'type', 'msg'].every((key) => key in envelope.data.error.details[0]))
}

async function followG2(scenario, wfId) {
  const done = await waitFrame((frame) => frame.type === 'part_analysis.updated' && frame.data.workflow_id === wfId && frame.data.status === 'completed', 90_000)
  const pa = done.data
  check(`${scenario}: G2 workflow → part_analysis.updated completed`, pa.status === 'completed' && pa.result?.parts?.length > 0, `${pa.id} ${pa.options.strategy}`)
  const wf = await waitFrame((frame) => frame.type === 'workflow.updated' && frame.data.id === wfId && frame.data.status === 'completed', 15_000)
  check(`${scenario}: G2 workflow completed with outputs`, wf.data.outputs.part_analysis_id === pa.id && wf.data.outputs.parts_model_url === pa.result.assembled_url && wf.data.steps.every((step) => ['done', 'skipped'].includes(step.status)), wf.data.steps.map((step) => `${step.id}:${step.status}`).join(' '))
  check(`${scenario}: strategy ${scenario === 'no-seg' ? 'components' : 'semantic_parts'}`, pa.options.strategy === (scenario === 'no-seg' ? 'components' : 'semantic_parts'), pa.options.strategy)
  const manifest = await request('GET', pa.result.manifest_url)
  check(`${scenario}: manifest 200 application/json`, manifest.status === 200 && manifest.headers.get('content-type').startsWith('application/json') && manifest.data.analysis_id === pa.id, manifest.status)
  const glb = await request('GET', pa.result.assembled_url)
  check(`${scenario}: assembled GLB 200 model/gltf-binary`, glb.status === 200 && glb.headers.get('content-type') === 'model/gltf-binary', glb.status)
  let parsed = null
  try {
    parsed = parseGlb(glb.data)
  } catch (error) {
    check(`${scenario}: GLB parses`, false, error.message)
    return pa
  }
  const partNodes = parsed.json.nodes.filter((node) => node.name !== 'img3d_asset')
  check(`${scenario}: GLB parses; node names = part:<id> = manifest meshes`, partNodes.every((node) => node.name === `part:${node.extras?.img3d?.id}`) && JSON.stringify(partNodes.map((node) => node.name)) === JSON.stringify(manifest.data.meshes.map((mesh) => mesh.node_name)), partNodes.map((node) => node.name).join(' '))
  const body = partNodes.find((node) => node.name === 'part:body')
  const attribute = body && parsed.json.meshes[body.mesh].primitives[0].attributes._FEATURE_ID_0
  const view = attribute !== undefined ? parsed.json.bufferViews[parsed.json.accessors[attribute].bufferView] : null
  const values = attribute !== undefined ? new Set(readAccessor(parsed, attribute)) : new Set()
  check(`${scenario}: _FEATURE_ID_0 on part:body (byteStride 4, accessor.byteOffset 0)`, view?.byteStride === 4 && parsed.json.accessors[attribute].byteOffset === 0 && values.has(1), [...values].join(','))
  return pa
}

async function followG3(scenario, wfId) {
  const completed = await waitFrame((frame) => frame.type === 'workflow.updated' && frame.data.id === wfId && frame.data.status === 'completed', 90_000)
  const jobId = completed.data.outputs.job_id
  const firstWf = await waitFrame((frame) => frame.type === 'workflow.updated' && frame.data.id === wfId, 1000)
  const firstJob = await waitFrame((frame) => frame.type === 'job.updated' && frame.data.id === jobId, 1000).catch(() => null)
  check(`${scenario}: G3 streams workflow.updated → job.updated → completed`, !!firstJob && firstWf.seq < firstJob.seq && firstJob.seq < completed.seq, `${firstWf.seq} < ${firstJob?.seq} < ${completed.seq}`)
  const steps = completed.data.steps
  const ig = steps.find((step) => step.id === 'text_to_image')
  const imported = steps.find((step) => step.id === 'import_image')
  check(`${scenario}: G3 steps text_to_image → import_image (auto_first) → reconstruction`, ig?.status === 'done' && ig.artifacts[0]?.role === 'candidate' && imported?.resource?.kind === 'reference_image' && steps.find((step) => step.id === 'reconstruction').status === 'done', steps.map((step) => `${step.id}:${step.status}`).join(' '))
  const image = await request('GET', `/reference-sets/${imported.resource.reference_set_id}`)
  const source = image.data.images.find((item) => item.id === imported.resource.id)?.source
  check(`${scenario}: imported candidate carries ReferenceImage.source (t2i)`, source?.kind === 't2i' && source.image_generation_id === ig.resource.id && source.candidate_index === 0, JSON.stringify(source))
}

async function validateCases(ids) {
  for (const name of listFixtures('graphs')) {
    const fixture = readFixture(`graphs/${name}.json`)
    if (!fixture.server) continue
    const graph = JSON.parse(withIds(JSON.stringify(fixture.server.graph), ids))
    const response = await request('POST', '/workflows/validate', { graph })
    const key = (item) => `${item.severity}:${item.code}:${item.node_id}:${item.port}`
    const got = (response.data?.issues ?? []).map(key).sort()
    const want = fixture.server.issues.map(key).sort()
    check(`validate: ${name} → valid=${fixture.server.valid} mode=${fixture.server.mode}`, response.status === 200 && response.data.valid === fixture.server.valid && response.data.mode === fixture.server.mode && JSON.stringify(got) === JSON.stringify(want) && !('job_request' in response.data), `${got.join(' | ') || '[]'}`)
  }
}

async function partsScenarios(jobId) {
  const outcomes = { ok: ['completed', 'completed'], partial: ['completed', 'partial'], fail: ['failed', null], 'no-attribute': ['completed', 'completed'] }
  for (const [parts, [status, resultStatus]] of Object.entries(outcomes)) {
    await setScenario({ parts })
    const created = await request('POST', `/jobs/${jobId}/part-analyses`, {})
    const run = await poll(`/part-analyses/${created.data.id}`, (data) => !['queued', 'running'].includes(data.status), 30_000)
    let detail = `${run.status} ${run.result?.status ?? run.error?.code}`
    let ok = run.status === status && (resultStatus ? run.result.status === resultStatus : run.error.code === 'partition_failed')
    if (parts === 'partial') ok &&= run.warnings.some((warning) => warning.code === 'camera_fit_low_iou')
    if (parts === 'fail') ok &&= run.steps.find((step) => step.id === 'partition').status === 'failed'
    if (parts === 'no-attribute' || parts === 'ok') {
      const parsed = parseGlb((await request('GET', run.result.assembled_url)).data)
      const has = parsed.json.meshes.some((mesh) => '_FEATURE_ID_0' in mesh.primitives[0].attributes)
      ok &&= parts === 'ok' ? has : !has
      detail += ` _FEATURE_ID_0=${has}`
    }
    check(`parts ${parts}: POST /jobs/{id}/part-analyses → ${status}${resultStatus ? ` (${resultStatus})` : ' partition_failed'}`, ok, detail)
  }
  await setScenario({ parts: 'slow' })
  const slow = await request('POST', `/jobs/${jobId}/part-analyses`, {})
  await sleep(2500)
  const running = await request('GET', `/part-analyses/${slow.data.id}`)
  const cancel = await request('POST', `/part-analyses/${slow.data.id}/cancel`)
  const cancelled = await poll(`/part-analyses/${slow.data.id}`, (data) => data.status === 'cancelled', 5000)
  check('parts slow: still running after 2.5 s, then cancelled', running.data.status === 'running' && cancel.status === 202 && cancelled.status === 'cancelled', `${running.data.status} → ${cancelled.status}`)
  await setScenario({ parts: 'ok' })
  const files = await request('GET', `/jobs/${jobId}/part-analyses/latest`)
  const pa = files.data
  const part = pa.result.parts.find((item) => item.kind === 'physical')
  const region = pa.result.parts.find((item) => item.kind === 'region')
  const [partGlb, regionGlb, thumb, mask, zip, buffer, unknown] = await Promise.all([
    request('GET', part.model_url), request('GET', `${pa.result.manifest_url.replace('/manifest', '')}/parts/${region.id}/model.glb`), request('GET', part.thumbnail_url),
    request('GET', part.mask_url), request('GET', pa.result.zip_url), request('GET', pa.result.face_labels_url), request('GET', `${pa.result.manifest_url.replace('/manifest', '')}/parts/nope/model.glb`),
  ])
  check('parts files: per-part GLB, 409 no_mesh for a region, webp thumbnail, png mask, zip, buffer headers, 404 unknown part',
    partGlb.status === 200 && regionGlb.status === 409 && regionGlb.data.error.details.reason === 'no_mesh' && thumb.headers.get('content-type') === 'image/webp' && thumb.data.subarray(8, 12).toString() === 'WEBP'
      && mask.headers.get('content-type') === 'image/png' && zip.headers.get('content-type') === 'application/zip' && zip.data.readUInt32LE(0) === 0x04034b50
      && buffer.headers.get('x-img3d-dtype') === 'uint16' && unknown.status === 404,
    [partGlb.status, regionGlb.status, thumb.headers.get('content-type'), mask.status, zip.status, buffer.headers.get('x-img3d-count'), unknown.status].join(' '))
}

async function modelsAndFlags(ids) {
  await setScenario({ t2i_cold_seconds: 1 })
  const from = frames.length
  const load = await request('POST', '/models/z_image_turbo/load')
  const again = await request('POST', '/models/z_image_turbo/load')
  const loaded = await waitFrame((frame) => frame.type === 'features.updated' && frame.data.pipeline.prompt_to_3d.default_model_state === 'loaded', 6000, from).catch(() => null)
  const loading = frames.slice(from).some((frame) => frame.type === 'features.updated' && frame.data.pipeline.prompt_to_3d.default_model_state === 'loading')
  check('models: POST /models/{id}/load → 202 loading → loaded (features.updated on each change); second load → 409', load.status === 202 && load.data.state === 'loading' && again.status === 409 && loading && !!loaded, `${load.status} ${load.data?.state} ${again.status} loading=${loading} loaded=${!!loaded}`)
  const unload = await request('POST', '/models/z_image_turbo/unload')
  const missing = await request('GET', '/models/nope')
  const badKind = await request('GET', '/models?kind=bogus')
  const hidden = await request('GET', '/models?include_hidden=true')
  check('models: unload → installed; 404 unknown; 422 bad kind; include_hidden lists hidden entries', unload.data.state === 'installed' && missing.status === 404 && badKind.status === 422 && hidden.data.items.some((model) => model.hidden_by_default), `${unload.data.state} ${missing.status} ${badKind.status}`)
  await setScenario({ t2i_cold_seconds: 0 })

  await setScenario({ queue_full: true })
  const g1 = await request('POST', '/jobs', withIds(readFixtureText('requests/G1.json'), ids), { raw: true })
  const g2 = await request('POST', '/workflows', withIds(readFixtureText('requests/G2.json'), ids), { raw: true })
  const g5 = await request('POST', '/image-generations', readFixtureText('requests/G5.json'), { raw: true })
  check('queue_full: create routes → 429 queue_full', [g1, g2, g5].every((response) => response.status === 429 && errorCode(response) === 'queue_full'), [g1.status, g2.status, g5.status].join(' '))
  await setScenario({ queue_full: false })

  await setScenario({ tuner_enabled: true })
  const features = await request('GET', '/features')
  check('tuner flag: /features mesh_tuning.enabled = flags.mesh_tuning = true', features.data.mesh_tuning.enabled === true && features.data.flags.mesh_tuning === true)
  await setScenario({ tuner_enabled: false })
}

async function requestLogChecks(ids, state, ownMock) {
  const log = (await testRoute('GET', 'requests')).data
  const set = log.findLast((entry) => entry.method === 'POST' && entry.path === '/api/v1/reference-sets')
  const image = log.findLast((entry) => entry.method === 'POST' && /\/reference-sets\/[^/]+\/images$/.test(entry.path))
  const job = log.find((entry) => entry.created_id === state.g1.jobId)
  check('request log: created_id for reference set and image upload', set?.created_id === ids.rs && image?.created_id === ids.img && image.body === null && /^[0-9a-f]{64}$/.test(image.body_sha256 ?? ''), `${set?.created_id} ${image?.created_id}`)
  check('request log: raw POST /jobs body === golden G1 (ids substituted) and created_id = job_…', job?.body === state.g1.text && job.status === 202 && /^job_/.test(job.created_id) && ['ts', 'method', 'path', 'query', 'status', 'body', 'body_sha256', 'created_id'].every((key) => key in job))
  if (ownMock) {
    const cleared = await testRoute('DELETE', 'requests')
    const empty = (await testRoute('GET', 'requests')).data
    check('request log: DELETE clears it', cleared.status === 204 && empty.length === 0, empty.length)
  }
}

// ------------------------------------------------------------------------------------------------
// Run
// ------------------------------------------------------------------------------------------------
const started = Date.now()
const baseIndex = args.indexOf('--base')
const mock = baseIndex >= 0 ? null : await startMock()
ROOT = (baseIndex >= 0 ? args[baseIndex + 1] : mock.root).replace(/\/+$/, '')
let original = null
try {
  original = (await testRoute('GET', 'scenario')).data
  const fullLog = (await testRoute('GET', 'requests')).data
  const tunerPaths = fullLog.filter((entry) => /\/blender|\/tunings/.test(entry.path))
  check('UI3: request log has zero /blender or /tunings paths', tunerPaths.length === 0, `${tunerPaths.length} of ${fullLog.length} entries`)
  await setScenario({ features: 'full', parts: 'ok', t2i_cold_seconds: 0, tuner_enabled: false, queue_full: false })
  await openSocket()
  const ids = await uploadSample()
  const jobs = (await request('GET', '/jobs?limit=100')).data.items
  const state = { jobId: jobs.find((job) => job.status === 'completed')?.id ?? null }
  if (!state.jobId) {
    const created = await request('POST', '/jobs', withIds(readFixtureText('requests/G1.json'), ids), { raw: true })
    state.jobId = (await poll(`/jobs/${created.data.id}`, (data) => data.status === 'completed', 60_000)).id
  }
  for (const scenario of SCENARIOS) {
    await setScenario({ features: scenario })
    await checkFeatures(scenario)
    if (scenario === 'full') {
      const g5 = await request('POST', '/image-generations', readFixtureText('requests/G5.json'), { raw: true })
      const ig = await poll(`/image-generations/${g5.data.id}`, (data) => data.status === 'completed', 20_000)
      state.igId = ig.id
      check('full: G5 image generation completes with 2 candidates and t2i_* readiness', ig.result.candidates.length === 2 && ig.result.candidates[1].readiness.issues[0] === 't2i_touches_border' && ig.warnings[0]?.code === 't2i_touches_border', ig.steps.map((step) => `${step.id}:${step.status}`).join(' '))
      const thumb = await request('GET', ig.result.candidates[0].thumbnail_url)
      const png = await request('GET', ig.result.candidates[0].url)
      check('full: candidate png + webp thumbnail', png.headers.get('content-type') === 'image/png' && thumb.headers.get('content-type') === 'image/webp', `${png.status} ${thumb.status}`)
    }
    await goldenBodies(scenario, ids, state)
    if (scenario === 'full') {
      await strictProbes(ids)
      const fresh = state.g1.jobId
      const latest = await request('GET', `/jobs/${fresh}/part-analyses/latest`)
      check('full: latest → 404 details.reason "none" for a fresh job', latest.status === 404 && latest.data.error.details?.reason === 'none', `${latest.status} ${JSON.stringify(latest.data?.error?.details)}`)
      await validateCases(ids)
    }
    if (state[`${scenario}:G2`]) {
      const pa = await followG2(scenario, state[`${scenario}:G2`])
      if (scenario === 'full') state.partsJob = pa.job_id
    }
    if (state[`${scenario}:G3`]) await followG3(scenario, state[`${scenario}:G3`])
  }
  await setScenario({ features: 'full' })
  await partsScenarios(state.partsJob ?? state.jobId)
  await modelsAndFlags(ids)
  await requestLogChecks(ids, state, mock !== null)
} catch (error) {
  check('smoke crashed', false, error.stack ?? String(error))
} finally {
  if (original) await setScenario({ features: original.features, parts: original.parts, t2i_cold_seconds: original.t2i_cold_seconds, tuner_enabled: original.tuner_enabled, queue_full: original.queue_full }).catch(() => {})
  socket?.close()
  mock?.stop()
}
const summary = { base: ROOT, passed: results.length - failures, failed: failures, seconds: (Date.now() - started) / 1000, results }
mkdirSync(OUT_DIR, { recursive: true })
writeFileSync(join(OUT_DIR, 'ui_modes_smoke.json'), JSON.stringify(summary, null, 2))
console.log(`\n${summary.passed} passed, ${summary.failed} failed in ${summary.seconds.toFixed(1)}s`)
process.exit(failures ? 1 : 0)
