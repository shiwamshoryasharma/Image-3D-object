// Unit tests for the part-aware API layer (UI_DESIGN.md §2.1–§2.4, slice F1): every DESIGN §6 route function sends
// the right method/path/params/body, the special status mappings (404 → legacy, 404 reason "none" → null,
// 304 → not_modified) and the five new WebSocket event types.
//
// The api/*.ts modules are not pure (extensionless imports, axios, `import.meta.env`), so this file registers a tiny
// in-process loader hook first: it resolves extensionless relative specifiers under frontend/src to `.ts` and
// replaces `import.meta.env` with `({})`. HTTP goes through a fake axios adapter; nothing touches the network.
//
// Run: node --experimental-strip-types --test "test/frontend/unit/**/*.test.mjs"
import assert from 'node:assert/strict'
import { register } from 'node:module'
import { test } from 'node:test'

const SRC = new URL('../../../frontend/src/', import.meta.url).href
const AXIOS = new URL('../../../frontend/node_modules/axios/index.js', import.meta.url).href

const LOADER = `
const SRC = ${JSON.stringify(SRC)};
export async function resolve(specifier, context, next) {
  if (context.parentURL?.startsWith(SRC) && /^\\.\\.?\\//.test(specifier) && !/\\.[cm]?[jt]sx?$/.test(specifier)) {
    try { return await next(specifier + '.ts', context) } catch { /* fall through */ }
  }
  return next(specifier, context)
}
export async function load(url, context, next) {
  const result = await next(url, context)
  if (!url.startsWith(SRC) || !url.endsWith('.ts')) return result
  const text = typeof result.source === 'string' ? result.source : new TextDecoder().decode(result.source)
  return { ...result, source: text.replaceAll('import.meta.env', '({})') }
}
`
register(`data:text/javascript,${encodeURIComponent(LOADER)}`)

const { default: axios } = await import(AXIOS)
const client = await import(new URL('api/client.ts', SRC).href)
const features = await import(new URL('api/features.ts', SRC).href)
const imageGenerations = await import(new URL('api/imageGenerations.ts', SRC).href)
const segmentations = await import(new URL('api/segmentations.ts', SRC).href)
const partAnalyses = await import(new URL('api/partAnalyses.ts', SRC).href)
const workflows = await import(new URL('api/workflows.ts', SRC).href)
const files = await import(new URL('api/files.ts', SRC).href)
const ws = await import(new URL('api/ws.ts', SRC).href)
const partAwareTypes = await import(new URL('api/partAwareTypes.ts', SRC).href)

// ------------------------------------------------------------------------------------------------
// Fake axios adapter
// ------------------------------------------------------------------------------------------------

const calls = []
let replyWith = () => ({ status: 200, data: {} })

/** Mimics axios' `settle`: statuses rejected by `validateStatus` become an AxiosError with the response. */
client.apiClient.defaults.adapter = async (config) => {
  calls.push(config)
  const reply = replyWith(config)
  reply.progress?.forEach((event) => config.onDownloadProgress?.(event))
  const response = {
    data: reply.data,
    status: reply.status,
    statusText: reply.statusText ?? '',
    headers: reply.headers ?? {},
    config,
    request: {},
  }
  if (!config.validateStatus || config.validateStatus(response.status)) return response
  const code = response.status >= 500 ? 'ERR_BAD_RESPONSE' : 'ERR_BAD_REQUEST'
  throw new axios.AxiosError(`Request failed with status code ${response.status}`, code, config, {}, response)
}

function reply(status, data, headers) {
  calls.length = 0
  replyWith = () => ({ status, data, headers })
}

function lastCall() {
  assert.equal(calls.length, 1, 'exactly one request')
  return calls[0]
}

function envelope(code, message, details = null) {
  return { error: { code, message, details, request_id: 'req_1' } }
}

// ------------------------------------------------------------------------------------------------
// Route table: every DESIGN §6 endpoint the UI uses (§2.2) plus the typed-but-unused ones
// ------------------------------------------------------------------------------------------------

const G5 = { prompt: 'a knight with a red cape and a sword', preset: 'character', style: 'anime', part_hint: true,
  num_images: 2, seed: 0, randomize_seed: true }
const G6 = { slot: 'front', make_primary: true }
const G8a = { separate: ['eyes', 'eyebrows', 'eyelashes', 'hair', 'clothes', 'accessories', 'detachable'],
  export: { per_part_glbs: true } }
const SIMPLE_WORKFLOW = { source: { kind: 'job', job_id: 'job_0123456789ab' }, parts: { separate: ['hair'] } }
const SEGMENTATION = { source: { kind: 'reference_image', reference_set_id: 'rs_1', image_id: 'img_1' } }

const ROUTES = [
  // [label, invoke, method, url, extra expectations]
  ['listModels()', () => features.listModels(), 'get', '/models', { params: {}, items: true }],
  ['listModels(kind, include_hidden)', () => features.listModels({ kind: 'segmentation', include_hidden: true }),
    'get', '/models', { params: { kind: 'segmentation', include_hidden: true }, items: true }],
  ['getModel', () => features.getModel('a/b'), 'get', '/models/a%2Fb'],
  ['loadModel', () => features.loadModel('z_model'), 'post', '/models/z_model/load', { status: 202 }],
  ['unloadModel', () => features.unloadModel('z_model'), 'post', '/models/z_model/unload', { status: 202 }],

  ['createImageGeneration', () => imageGenerations.createImageGeneration(G5), 'post', '/image-generations',
    { body: G5, status: 202 }],
  ['listImageGenerations', () => imageGenerations.listImageGenerations({ limit: 5, status: 'running' }), 'get',
    '/image-generations', { params: { limit: 5, status: 'running' }, items: true }],
  ['getImageGeneration', () => imageGenerations.getImageGeneration('ig_1'), 'get', '/image-generations/ig_1'],
  ['cancelImageGeneration', () => imageGenerations.cancelImageGeneration('ig_1'), 'post',
    '/image-generations/ig_1/cancel', { status: 202 }],
  ['deleteImageGeneration', () => imageGenerations.deleteImageGeneration('ig_1'), 'delete',
    '/image-generations/ig_1', { status: 204, voidResult: true }],
  ['postCandidateUse', () => imageGenerations.postCandidateUse('ig_1', 0, G6), 'post',
    '/image-generations/ig_1/candidates/0/use', { body: G6, status: 201 }],

  ['createSegmentation', () => segmentations.createSegmentation(SEGMENTATION), 'post', '/segmentations',
    { body: SEGMENTATION, status: 202 }],
  ['listSegmentations', () => segmentations.listSegmentations({ reference_set_id: 'rs_1', job_id: 'job_1' }), 'get',
    '/segmentations', { params: { reference_set_id: 'rs_1', job_id: 'job_1' }, items: true }],
  ['getSegmentation', () => segmentations.getSegmentation('sg_1'), 'get', '/segmentations/sg_1'],
  ['cancelSegmentation', () => segmentations.cancelSegmentation('sg_1'), 'post', '/segmentations/sg_1/cancel',
    { status: 202 }],
  ['deleteSegmentation', () => segmentations.deleteSegmentation('sg_1'), 'delete', '/segmentations/sg_1',
    { status: 204, voidResult: true }],

  ['createPartAnalysis(options)', () => partAnalyses.createPartAnalysis('job_1', G8a), 'post',
    '/jobs/job_1/part-analyses', { body: G8a, status: 202 }],
  ['listPartAnalyses', () => partAnalyses.listPartAnalyses('job_1'), 'get', '/jobs/job_1/part-analyses',
    { items: true }],
  ['getLatestPartAnalysis', () => partAnalyses.getLatestPartAnalysis('job_1'), 'get',
    '/jobs/job_1/part-analyses/latest'],
  ['getPartAnalysis', () => partAnalyses.getPartAnalysis('pa_1'), 'get', '/part-analyses/pa_1'],
  ['cancelPartAnalysis', () => partAnalyses.cancelPartAnalysis('pa_1'), 'post', '/part-analyses/pa_1/cancel',
    { status: 202 }],
  ['deletePartAnalysis', () => partAnalyses.deletePartAnalysis('pa_1'), 'delete', '/part-analyses/pa_1',
    { status: 204, voidResult: true }],
  ['getPartManifest', () => partAnalyses.getPartManifest('/api/v1/part-analyses/pa_1/manifest'), 'get',
    '/api/v1/part-analyses/pa_1/manifest', { absolute: true }],

  ['validateWorkflow', () => workflows.validateWorkflow(SIMPLE_WORKFLOW), 'post', '/workflows/validate',
    { body: SIMPLE_WORKFLOW }],
  ['createWorkflow', () => workflows.createWorkflow(SIMPLE_WORKFLOW), 'post', '/workflows',
    { body: SIMPLE_WORKFLOW, status: 202 }],
  ['listWorkflows', () => workflows.listWorkflows({ job_id: 'job_1', image_generation_id: 'ig_1', limit: 100 }),
    'get', '/workflows', { params: { job_id: 'job_1', image_generation_id: 'ig_1', limit: 100 }, items: true }],
  ['getWorkflow', () => workflows.getWorkflow('wf_1'), 'get', '/workflows/wf_1'],
  ['getWorkflowGraph', () => workflows.getWorkflowGraph('wf_1'), 'get', '/workflows/wf_1/graph'],
  ['cancelWorkflow', () => workflows.cancelWorkflow('wf_1'), 'post', '/workflows/wf_1/cancel', { status: 202 }],
  ['deleteWorkflow', () => workflows.deleteWorkflow('wf_1'), 'delete', '/workflows/wf_1',
    { status: 204, voidResult: true }],
]

test('every route function sends the DESIGN §6 method, path, params and body', async (t) => {
  for (const [label, invoke, method, url, expect = {}] of ROUTES) {
    await t.test(label, async () => {
      const payload = expect.items ? { items: [{ id: 'x_1' }] } : expect.voidResult ? '' : { id: 'x_1' }
      reply(expect.status ?? 200, payload)
      const result = await invoke()
      const call = lastCall()
      assert.equal(call.method, method)
      assert.equal(call.url, url)
      assert.equal(call.baseURL, expect.absolute ? '' : '/api/v1')
      if ('params' in expect) assert.deepEqual(call.params, expect.params)
      if ('body' in expect) {
        // The body is passed through untouched: same bytes, same key order.
        assert.equal(call.data, JSON.stringify(expect.body))
      } else {
        assert.equal(call.data, undefined, 'no request body')
      }
      if (expect.items) assert.deepEqual(result, [{ id: 'x_1' }])
      else if (expect.voidResult) assert.equal(result, undefined)
      else assert.deepEqual(result, { id: 'x_1' })
    })
  }
})

test('createPartAnalysis without options sends no body (server defaults)', async () => {
  reply(202, { id: 'pa_1' })
  await partAnalyses.createPartAnalysis('job_1')
  const call = lastCall()
  // `data: undefined` → the browser (XHR) adapter sends no body and drops the default Content-Type.
  assert.equal(call.url, '/jobs/job_1/part-analyses')
  assert.equal(call.data, undefined)
})

test('validateWorkflow forwards the abort signal', async () => {
  const controller = new AbortController()
  reply(200, { valid: true, issues: [], mode: 'job', request: null })
  await workflows.validateWorkflow({ graph: null }, controller.signal)
  assert.equal(lastCall().signal, controller.signal)
})

test('Authorization header is sent on J routes when a token is stored', async () => {
  client.setAuthToken('secret token')
  try {
    reply(200, { id: 'wf_1' })
    await workflows.getWorkflow('wf_1')
    assert.equal(lastCall().headers.get('Authorization'), 'Bearer secret token')
  } finally {
    client.setAuthToken(null)
  }
})

// ------------------------------------------------------------------------------------------------
// Status mappings (§2.2)
// ------------------------------------------------------------------------------------------------

test('getFeatures: 200 → ok, 404 → legacy, 401 and other errors throw', async (t) => {
  await t.test('200', async () => {
    const body = { schema: 'img3d.features/1', api: { version: '1.1.0' } }
    reply(200, body)
    assert.deepEqual(await features.getFeatures(), { kind: 'ok', data: body })
    assert.equal(lastCall().url, '/features')
  })
  await t.test('404 with the error envelope', async () => {
    reply(404, envelope('not_found', 'Not found'))
    assert.deepEqual(await features.getFeatures(), { kind: 'legacy' })
  })
  await t.test('404 without an envelope (plain FastAPI detail)', async () => {
    reply(404, { detail: 'Not Found' })
    assert.deepEqual(await features.getFeatures(), { kind: 'legacy' })
  })
  await t.test('401 throws and runs the session-expired hook', async () => {
    const seen = []
    client.configureClientHooks({ onUnauthorized: (error) => seen.push(error.status) })
    try {
      reply(401, envelope('unauthorized', 'Authentication required'))
      await assert.rejects(features.getFeatures(), (error) => error instanceof client.ApiError && error.status === 401)
      assert.deepEqual(seen, [401])
    } finally {
      client.configureClientHooks({ onUnauthorized: undefined })
    }
  })
  await t.test('500 with an envelope throws', async () => {
    reply(500, envelope('internal_error', 'Boom'))
    await assert.rejects(features.getFeatures(), (error) => error.code === 'internal_error' && error.status === 500)
  })
})

test('getLatestPartAnalysis: 404 reason "none" → null; other 404s throw', async (t) => {
  await t.test('200', async () => {
    reply(200, { id: 'pa_1', status: 'completed' })
    assert.deepEqual(await partAnalyses.getLatestPartAnalysis('job_1'), { id: 'pa_1', status: 'completed' })
  })
  await t.test('404 details.reason none', async () => {
    reply(404, envelope('not_found', 'No completed part analysis', { reason: 'none' }))
    assert.equal(await partAnalyses.getLatestPartAnalysis('job_1'), null)
  })
  await t.test('404 unknown job (no reason)', async () => {
    reply(404, envelope('not_found', 'Job not found'))
    await assert.rejects(partAnalyses.getLatestPartAnalysis('job_x'), (error) => error.status === 404)
  })
  await t.test('404 with another reason', async () => {
    reply(404, envelope('not_found', 'Job not found', { reason: 'job' }))
    await assert.rejects(partAnalyses.getLatestPartAnalysis('job_x'), (error) => error.status === 404)
  })
  await t.test('409 throws', async () => {
    reply(409, envelope('conflict', 'Busy', { reason: 'none' }))
    await assert.rejects(partAnalyses.getLatestPartAnalysis('job_1'), (error) => error.status === 409)
  })
})

test('getWorkflowCatalog: If-None-Match, 304 → not_modified, ETag from header or body', async (t) => {
  const catalog = { schema: 'img3d.workflow_catalog/1', etag: '"sha256-body"', node_types: [] }
  await t.test('first load sends no If-None-Match', async () => {
    reply(200, catalog, { etag: '"sha256-header"' })
    assert.deepEqual(await workflows.getWorkflowCatalog(null), { kind: 'ok', data: catalog, etag: '"sha256-header"' })
    const call = lastCall()
    assert.equal(call.url, '/workflows/catalog')
    assert.equal(call.headers.get('If-None-Match'), undefined)
  })
  await t.test('304 with an empty body', async () => {
    reply(304, '', { etag: '"sha256-header"' })
    assert.deepEqual(await workflows.getWorkflowCatalog('"sha256-header"'), { kind: 'not_modified' })
    const call = lastCall()
    assert.equal(call.headers.get('If-None-Match'), '"sha256-header"')
    assert.equal(call.validateStatus(304), true)
    assert.equal(call.validateStatus(200), true)
    assert.equal(call.validateStatus(404), false)
  })
  await t.test('changed catalog without a readable ETag header falls back to the body etag', async () => {
    reply(200, catalog)
    assert.deepEqual(await workflows.getWorkflowCatalog('"sha256-old"'), { kind: 'ok', data: catalog,
      etag: '"sha256-body"' })
  })
  await t.test('errors still throw ApiError', async () => {
    reply(401, envelope('unauthorized', 'Authentication required'))
    await assert.rejects(workflows.getWorkflowCatalog(null), (error) => error instanceof client.ApiError)
  })
})

// ------------------------------------------------------------------------------------------------
// Files (§2.3)
// ------------------------------------------------------------------------------------------------

test('fileUrl resolves payload URLs and adds ?token= when a token is stored', () => {
  assert.equal(files.fileUrl('/api/v1/part-analyses/pa_1/model.glb'), '/api/v1/part-analyses/pa_1/model.glb')
  client.setAuthToken('t k')
  try {
    assert.equal(files.fileUrl('/api/v1/part-analyses/pa_1/model.glb'), '/api/v1/part-analyses/pa_1/model.glb?token=t%20k')
    assert.equal(files.fileUrl(files.withQuery('/api/v1/part-analyses/pa_1/export/zip', { include_base: 'true' })),
      '/api/v1/part-analyses/pa_1/export/zip?include_base=true&token=t%20k')
  } finally {
    client.setAuthToken(null)
  }
})

test('withQuery appends encoded parameters', () => {
  assert.equal(files.withQuery('/x/zip', { include_base: 'true' }), '/x/zip?include_base=true')
  assert.equal(files.withQuery('/x/zip?a=1', { include_base: 'false' }), '/x/zip?a=1&include_base=false')
  assert.equal(files.withQuery('/x/zip', {}), '/x/zip')
  assert.equal(files.withQuery('/x', { 'a b': 'c&d' }), '/x?a%20b=c%26d')
})

test('downloadFile: header auth on the absolute payload URL, filename, progress', async (t) => {
  await t.test('Content-Disposition filename', async () => {
    client.setAuthToken('tok')
    try {
      reply(200, new Blob(['glb']), { 'content-disposition': 'attachment; filename="img3d_job_1_parts_pa_1.glb"' })
      const { blob, filename } = await files.downloadFile('/api/v1/part-analyses/pa_1/export/glb',
        { fallbackName: 'fallback.glb' })
      assert.equal(filename, 'img3d_job_1_parts_pa_1.glb')
      assert.equal(await blob.text(), 'glb')
      const call = lastCall()
      assert.equal(call.baseURL, '')
      assert.equal(call.url, '/api/v1/part-analyses/pa_1/export/glb')
      assert.equal(call.responseType, 'blob')
      assert.equal(call.timeout, 0)
      assert.equal(call.headers.get('Authorization'), 'Bearer tok')
    } finally {
      client.setAuthToken(null)
    }
  })
  await t.test('RFC 5987 filename*', async () => {
    reply(200, new Blob(['zip']), { 'content-disposition': "attachment; filename*=UTF-8''img3d%20bundle.zip" })
    const { filename } = await files.downloadFile('/api/v1/x/export/zip', { fallbackName: 'fallback.zip' })
    assert.equal(filename, 'img3d bundle.zip')
  })
  await t.test('fallback name and malformed encodings', async () => {
    reply(200, new Blob(['a']))
    assert.equal((await files.downloadFile('/api/v1/x', { fallbackName: 'fallback.bin' })).filename, 'fallback.bin')
    reply(200, new Blob(['a']), { 'content-disposition': "attachment; filename*=UTF-8''bad%E0%A4%A.glb" })
    assert.equal((await files.downloadFile('/api/v1/x', { fallbackName: 'f.glb' })).filename, 'bad%E0%A4%A.glb')
  })
  await t.test('progress uses the total, then sizeHint, else null', async () => {
    const seen = []
    calls.length = 0
    replyWith = () => ({ status: 200, data: new Blob(['a']),
      progress: [{ loaded: 50, total: 100 }, { loaded: 50, total: undefined }] })
    await files.downloadFile('/api/v1/x', { fallbackName: 'f', sizeHint: 200, onProgress: (p) => seen.push(p) })
    await files.downloadFile('/api/v1/x', { fallbackName: 'f', onProgress: (p) => seen.push(p) })
    assert.deepEqual(seen, [50, 25, 50, null])
  })
  await t.test('a JSON error body inside a Blob becomes an ApiError', async () => {
    reply(409, new Blob([JSON.stringify(envelope('conflict', 'Part has no mesh', { reason: 'no_mesh' }))]))
    await assert.rejects(files.downloadFile('/api/v1/x', { fallbackName: 'f' }),
      (error) => error.code === 'conflict' && error.details.reason === 'no_mesh')
  })
})

test('typed non-v1 file routes (labels, source, buffers, log)', async (t) => {
  await t.test('segmentation labels and source', async () => {
    reply(200, new Blob(['png']))
    assert.equal(await (await segmentations.getSegmentationLabels('sg_1')).text(), 'png')
    assert.equal(lastCall().url, '/segmentations/sg_1/labels')
    assert.equal(calls[0].responseType, 'blob')
    reply(200, new Blob(['png']))
    await segmentations.getSegmentationSource('sg_1')
    assert.equal(lastCall().url, '/segmentations/sg_1/source')
  })
  await t.test('part buffer with dtype/count headers', async () => {
    reply(200, new Blob([new Uint8Array([1, 0, 2, 0])]), { 'x-img3d-dtype': 'uint16', 'x-img3d-count': '2' })
    const buffer = await partAnalyses.getPartAnalysisBuffer('pa_1', 'face_labels')
    assert.equal(lastCall().url, '/part-analyses/pa_1/buffers/face_labels')
    assert.equal(buffer.dtype, 'uint16')
    assert.equal(buffer.count, 2)
    assert.deepEqual([...new Uint16Array(buffer.data)], [1, 2])
    reply(200, new Blob([]), {})
    assert.deepEqual(await partAnalyses.getPartAnalysisBuffer('pa_1', 'face_part'),
      { data: new ArrayBuffer(0), dtype: null, count: null })
  })
  await t.test('part analysis log', async () => {
    reply(200, new Blob(['line 1\nline 2']))
    assert.equal(await partAnalyses.getPartAnalysisLog('pa_1'), 'line 1\nline 2')
    assert.equal(lastCall().url, '/part-analyses/pa_1/log')
  })
})

// ------------------------------------------------------------------------------------------------
// Wire-type module (§2.1) and WebSocket events (§2.4)
// ------------------------------------------------------------------------------------------------

test('partAwareTypes exports only the GRAPH_ISSUE_CODES value (DESIGN §4.7.2 issue codes)', () => {
  assert.deepEqual(Object.keys(partAwareTypes), ['GRAPH_ISSUE_CODES'])
  assert.deepEqual(partAwareTypes.GRAPH_ISSUE_CODES, ['graph_too_large', 'unknown_node_type', 'unknown_port',
    'type_mismatch', 'port_already_connected', 'missing_required_input', 'no_source', 'multiple_sources', 'cycle',
    'too_many_instances', 'node_locked', 'unsupported_topology', 'capability_unavailable', 'feature_disabled',
    'invalid_param', 'not_found', 'no_sink'])
})

class FakeSocket {
  static CONNECTING = 0
  static OPEN = 1
  static CLOSING = 2
  static CLOSED = 3
  static instances = []

  constructor(url) {
    this.url = url
    this.readyState = FakeSocket.CONNECTING
    this.sent = []
    FakeSocket.instances.push(this)
  }

  send(message) {
    this.sent.push(message)
  }

  close() {
    this.readyState = FakeSocket.CLOSED
  }
}

test('RealtimeClient dispatches the five part-aware event types; unknown types are ignored', () => {
  const noop = () => {}
  const saved = { window: globalThis.window, document: globalThis.document, WebSocket: globalThis.WebSocket }
  Object.defineProperty(globalThis, 'WebSocket', { value: FakeSocket, configurable: true, writable: true })
  globalThis.window = { location: { origin: 'http://127.0.0.1:5173' }, addEventListener: noop, removeEventListener: noop }
  globalThis.document = { visibilityState: 'visible', addEventListener: noop, removeEventListener: noop }
  const realtime = new ws.RealtimeClient({ getToken: () => 'tok' })
  try {
    const types = ['features.updated', 'image_generation.updated', 'segmentation.updated', 'part_analysis.updated',
      'workflow.updated', 'job.updated']
    const seen = []
    for (const type of types) realtime.on(type, (data) => seen.push([type, data]))
    realtime.start()
    const socket = FakeSocket.instances.at(-1)
    // No ?job_id= filter, so the job-scoped part_analysis.updated is delivered (§2.4).
    assert.equal(socket.url, 'ws://127.0.0.1:5173/api/v1/ws?token=tok')
    socket.readyState = FakeSocket.OPEN
    socket.onopen()
    for (const type of types) {
      socket.onmessage({ data: JSON.stringify({ type, ts: '2026-10-16T00:00:00Z', data: { id: `${type}:1` } }) })
    }
    socket.onmessage({ data: JSON.stringify({ type: 'future.updated', ts: '2026-10-16T00:00:00Z', data: {} }) })
    socket.onmessage({ data: 'not json' })
    socket.onmessage({ data: JSON.stringify({ type: 'ping', ts: '2026-10-16T00:00:00Z', data: {} }) })
    assert.deepEqual(seen, types.map((type) => [type, { id: `${type}:1` }]))
    assert.deepEqual(socket.sent, [JSON.stringify({ type: 'pong' })])
  } finally {
    realtime.stop()
    Object.defineProperty(globalThis, 'WebSocket', { value: saved.WebSocket, configurable: true, writable: true })
    globalThis.window = saved.window
    globalThis.document = saved.document
  }
})
