/**
 * TEST DOUBLE — in-memory mock of the Img3D backend contract (api-contract.md §7/§8) used ONLY to exercise the
 * frontend UI without a GPU. Not part of the application. No dependencies (Node >= 20).
 *
 *   node test/frontend/mock_backend.mjs [--port 8765] [--auth]
 *        [--blender-scenario ready|old-only|none|downloading] [--no-download] [--download-seconds 10]
 *        [--download-fail none|checksum|network] [--tuning-scenario ok|warnings|fail] [--tuning-seconds 9]
 *        [--seed-jobs 0] [--engine-ready]
 *        [--features full|no-t2i|no-seg|disabled|legacy404] [--strict-requests on|off]
 *        [--parts-scenario ok|partial|fail|slow|no-attribute] [--t2i-cold-seconds 0] [--tuner-enabled]
 *
 * Then run the frontend against it:
 *   IMG3D_BACKEND_URL=http://127.0.0.1:8765 npx vite preview --port 4174   (from frontend/)
 *
 * Part-aware milestone (UI_DESIGN.md §10.4; routes and shapes of DESIGN.md §6 in test/frontend/mock/part_aware_mock.mjs):
 *   --features          full (default) | no-t2i | no-seg | disabled | legacy404: serves fixtures/features/<name>.json;
 *                       legacy404 = an old backend (404 for /features, /models*, /image-generations*, /segmentations*,
 *                       /jobs/{id}/part-analyses*, /part-analyses*, /workflows*); disabled = mutating new routes 503
 *                       feature_disabled; no-t2i / no-seg = 503 capability_unavailable for prompt / segmentation.
 *   --strict-requests   on (default): every JSON body with a DESIGN key set (incl. POST /jobs) is checked recursively
 *                       against fixtures/request_keys.json → 422 validation_error / extra_forbidden.
 *   --parts-scenario    pa_ runs: ok | partial (result.status partial + warning) | fail (partition_failed) | slow (30 s)
 *                       | no-attribute (model_parts.glb without _FEATURE_ID_0).
 *   --t2i-cold-seconds  the first ig_ run spends N s in load_model; POST /models/{id}/load → loading for N s → loaded.
 *   --tuner-enabled     /features reports mesh_tuning.enabled = flags.mesh_tuning = true (the UI build flag still hides it).
 *
 * Test-only routes (never part of the contract):
 *   GET    /api/__test/sample/<name>   serves files from test/images (for scripted uploads)
 *   GET    /api/__test/requests        request log [{ts, method, path, query, status, body, body_sha256, created_id}];
 *                                      `body` is the raw request text exactly as received (null for no body or a binary
 *                                      multipart upload), `created_id` the id the request created (rs_/img_/job_/wf_/…)
 *   DELETE /api/__test/requests        clears the log
 *   GET    /api/__test/scenario        the current scenario
 *   POST   /api/__test/scenario        {features?, parts?, t2i_cold_seconds?, tuner_enabled?, queue_full?}: switch at
 *                                      runtime (broadcasts features.updated); queue_full makes every create route
 *                                      (POST /jobs, /workflows, /image-generations, /segmentations,
 *                                      /jobs/{id}/part-analyses) answer 429 queue_full until reset
 *
 * Auto Mesh Tuner simulation (DESIGN.md §6 contract, test-only data):
 *   --blender-scenario ready        5.2.2 LTS (system, selected) + 4.2.17 LTS (incompatible)        [default]
 *                      old-only     only 4.2.17 → needs_download with the exact "installed but …" prompt
 *                      none         no Blender → needs_download with the "No Blender installation …" prompt
 *                      downloading  old-only with a confirmed portable download already in progress
 *   A confirmed download streams resolving → downloading (bytes/speed/ETA) → verifying → extracting →
 *   validating → completed, then installs an embedded 5.2.2 and re-selects. Cancel keeps the partial (resume).
 *   Auto Tune runs stream the 8 protocol steps, then a report (metrics/validation/warnings/previews) and a
 *   distinct tuned GLB at /api/v1/tunings/<id>/model.glb.
 *   --seed-jobs N creates N completed jobs at startup (so the tuner can be exercised without generating).
 */
import { createHash, randomBytes } from 'node:crypto'
import { readFileSync, readdirSync, statSync } from 'node:fs'
import { createServer } from 'node:http'
import { dirname, join } from 'node:path'
import { fileURLToPath } from 'node:url'
import { deflateSync } from 'node:zlib'
import { createPartAwareMock, FEATURE_SCENARIOS, PARTS_SCENARIOS } from './mock/part_aware_mock.mjs'

const args = process.argv.slice(2)
const argValue = (name, fallback) => (args.includes(name) ? args[args.indexOf(name) + 1] ?? fallback : fallback)
const PORT = Number(args[args.indexOf('--port') + 1]) || 8765
const AUTH = args.includes('--auth')
const BLENDER_SCENARIO = argValue('--blender-scenario', 'ready')
const ALLOW_DOWNLOAD = !args.includes('--no-download')
const DOWNLOAD_SECONDS = Number(argValue('--download-seconds', '10')) || 10
const DOWNLOAD_FAIL = argValue('--download-fail', 'none')
const TUNING_SCENARIO = argValue('--tuning-scenario', 'warnings')
const TUNING_SECONDS = Number(argValue('--tuning-seconds', '9')) || 9
const SEED_JOBS = Number(argValue('--seed-jobs', '0')) || 0
const ENGINE_READY = args.includes('--engine-ready')
const HERE = dirname(fileURLToPath(import.meta.url))
const IMAGES_DIR = join(HERE, '..', 'images')
const P = '/api/v1'
if (!['ready', 'old-only', 'none', 'downloading'].includes(BLENDER_SCENARIO)) throw new Error(`unknown --blender-scenario ${BLENDER_SCENARIO}`)
if (!['ok', 'warnings', 'fail'].includes(TUNING_SCENARIO)) throw new Error(`unknown --tuning-scenario ${TUNING_SCENARIO}`)

/** Part-aware scenario (UI_DESIGN §10.4); mutated at runtime by POST /api/__test/scenario. */
const scenario = {
  features: argValue('--features', 'full'),
  parts: argValue('--parts-scenario', 'ok'),
  t2i_cold_seconds: Number(argValue('--t2i-cold-seconds', '0')) || 0,
  tuner_enabled: args.includes('--tuner-enabled'),
  queue_full: false,
  strict: argValue('--strict-requests', 'on') !== 'off',
}
if (!FEATURE_SCENARIOS.includes(scenario.features)) throw new Error(`unknown --features ${scenario.features}`)
if (!PARTS_SCENARIOS.includes(scenario.parts)) throw new Error(`unknown --parts-scenario ${scenario.parts}`)
if (!['on', 'off'].includes(argValue('--strict-requests', 'on'))) throw new Error('--strict-requests must be on or off')

const now = () => new Date().toISOString()
const hex = (n = 12) => randomBytes(Math.ceil(n / 2)).toString('hex').slice(0, n)

// ------------------------------------------------------------------------------------------------
// Engine simulation
// ------------------------------------------------------------------------------------------------
const engine = ENGINE_READY ? { state: 'ready', loaded: 8, total: 8, loaded_at: now() } : { state: 'loading', loaded: 0, total: 8, loaded_at: null }
const COMPONENTS = ['ss_flow', 'ss_decoder', 'shape_flow_512', 'shape_flow_1024', 'tex_flow_512', 'tex_flow_1024', 'shape_decoder', 'tex_decoder']
const loadTimer = setInterval(() => {
  if (engine.state === 'ready') return clearInterval(loadTimer)
  engine.loaded += 1
  if (engine.loaded >= engine.total) {
    engine.state = 'ready'
    engine.loaded_at = now()
    clearInterval(loadTimer)
  }
  broadcast('system.updated', systemStatus())
}, 700)

function systemStatus() {
  const running = [...jobs.values()].find((j) => j.status === 'running')
  return {
    app: { name: 'Img3D', version: '1.0.0', environment: 'development' },
    engine: {
      name: 'trellis2',
      state: engine.state,
      message: engine.state === 'loading' ? `Loading ${COMPONENTS[engine.loaded] ?? ''} (${engine.loaded + 1}/${engine.total})` : 'Model ready',
      load_progress: engine.state === 'loading' ? { loaded: engine.loaded, total: engine.total, current: COMPONENTS[engine.loaded] ?? null } : null,
      error: null,
      loaded_at: engine.loaded_at,
      details: { attention_backend: 'sdpa', model_dir: 'mock', low_vram: true },
      capabilities: {
        resolutions: [512, 1024, 1536],
        texture_sizes: [512, 1024, 2048, 4096],
        polygon_target: { min: 5000, max: 2000000, default: 500000 },
        max_reference_images: 1,
        reference_modes: ['primary_only'],
        supports_cancel: true,
        supports_texture: true,
      },
    },
    device: {
      cuda_available: true, name: 'Mock GPU (test double)', compute_capability: '12.0',
      vram_total_mb: 32607, vram_used_mb: running ? 21000 : 9000, vram_free_mb: running ? 11607 : 23607,
      torch_version: 'mock', cuda_version: 'mock',
    },
    queue: { pending: [...jobs.values()].filter((j) => j.status === 'queued').length, running_job_id: running?.id ?? null, max_size: 16 },
    limits: {
      max_upload_mb: 25, allowed_mime_types: ['image/png', 'image/jpeg', 'image/webp'], max_detail_images: 8,
      min_image_side: 64, slots: ['front', 'back', 'left', 'right', 'face_front', 'face_side', 'detail'],
    },
    defaults: { resolution: 1024, seed: 0, randomize_seed: true, polygon_target: 500000, texture_size: 2048, remove_background: true, reference_mode: 'primary_only' },
    auth_required: AUTH,
  }
}

// ------------------------------------------------------------------------------------------------
// Reference sets
// ------------------------------------------------------------------------------------------------
const SLOT_ORDER = ['front', 'back', 'left', 'right', 'face_front', 'face_side', 'detail']
const PRIORITY = ['front', 'face_front', 'left', 'right', 'back', 'face_side', 'detail']
const sets = new Map()
const files = new Map() // image id -> { bytes, mime }

function setView(set) {
  const images = [...set.images].sort((a, b) => SLOT_ORDER.indexOf(a.slot) - SLOT_ORDER.indexOf(b.slot) || a.created_at.localeCompare(b.created_at))
  return { ...set, images, primary_image_id: images.find((i) => i.is_primary)?.id ?? null, slot_limits: { front: 1, back: 1, left: 1, right: 1, face_front: 1, face_side: 1, detail: 8 } }
}

function touch(set) { set.updated_at = now() }

function ensurePrimary(set) {
  if (set.images.length && !set.images.some((i) => i.is_primary)) {
    const next = [...set.images].sort((a, b) => PRIORITY.indexOf(a.slot) - PRIORITY.indexOf(b.slot))[0]
    next.is_primary = true
  }
}

function pngSize(buf) {
  if (buf.length > 24 && buf.readUInt32BE(0) === 0x89504e47) return { width: buf.readUInt32BE(16), height: buf.readUInt32BE(20), alpha: buf[25] === 6 || buf[25] === 4 }
  return { width: 512, height: 512, alpha: false }
}

function createReferenceSet(name = null) {
  const set = { id: `rs_${hex()}`, name, created_at: now(), updated_at: now(), images: [] }
  sets.set(set.id, set)
  return set
}

/** The upload path (slot replacement, primary rules). `source` is DESIGN §6.7 `ImageSourceOut` (null for uploads). */
function addReferenceImage(set, { bytes, mime, filename, slot, label = null, makePrimary = false, source = null }) {
  let wasPrimary = false
  if (slot !== 'detail') {
    const old = set.images.find((i) => i.slot === slot)
    if (old) { wasPrimary = old.is_primary; set.images = set.images.filter((i) => i !== old); files.delete(old.id) }
  }
  const size = pngSize(bytes)
  const image = {
    id: `img_${hex()}`, set_id: set.id, slot, label, original_filename: filename, mime_type: mime,
    width: size.width, height: size.height, has_alpha: size.alpha, size_bytes: bytes.length, sha256: createHash('sha256').update(bytes).digest('hex'),
    is_primary: false, created_at: now(), urls: {}, source,
  }
  image.urls = { file: `${P}/reference-sets/${set.id}/images/${image.id}/file`, thumbnail: `${P}/reference-sets/${set.id}/images/${image.id}/thumbnail` }
  if (makePrimary || wasPrimary || set.images.length === 0) {
    set.images.forEach((i) => { i.is_primary = false })
    image.is_primary = true
  }
  set.images.push(image); files.set(image.id, { bytes, mime }); touch(set)
  return image
}

// ------------------------------------------------------------------------------------------------
// Jobs
// ------------------------------------------------------------------------------------------------
const jobs = new Map()
const STAGES = [
  ['preprocessing', 1, 'Preparing image', 5, 1], ['conditioning', 2, 'Encoding image', 5, 1],
  ['sparse_structure', 3, 'Building structure', 15, 12], ['shape', 4, 'Generating geometry', 30, 12],
  ['texture', 5, 'Generating texture', 20, 12], ['decoding', 6, 'Decoding mesh', 8, 1],
  ['exporting', 7, 'Exporting GLB', 15, 1], ['finalizing', 8, 'Finalizing', 2, 1],
]
const GLB = buildSphereGlb()

function progress(stage, label, index, step, total, percent, message, job) {
  const elapsed = job.started_at ? (Date.now() - Date.parse(job.started_at)) / 1000 : 0
  const eta = percent > 2 && percent < 100 ? (elapsed / percent) * (100 - percent) : null
  return { stage, stage_label: label, stage_index: index, stage_count: 9, step, total_steps: total, percent, message, elapsed_seconds: elapsed, eta_seconds: eta, updated_at: now() }
}

function tickJobs() {
  const running = [...jobs.values()].find((j) => j.status === 'running')
  if (!running) {
    const next = [...jobs.values()].filter((j) => j.status === 'queued').sort((a, b) => a.created_at.localeCompare(b.created_at))[0]
    if (next) {
      if (engine.state !== 'ready') {
        if (next.progress.stage !== 'waiting_for_model') {
          next.progress = progress('waiting_for_model', 'Waiting for model', 0, null, null, 0, 'Waiting for the model to load', next)
          broadcast('job.updated', next)
        }
        return
      }
      next.status = 'running'
      next.started_at = now()
      next.queue_position = null
      next.sim = { stage: 0, step: 0 }
      broadcast('system.updated', systemStatus())
    } else return
  }
  const job = running ?? [...jobs.values()].find((j) => j.status === 'running')
  if (job.cancel) {
    job.status = 'cancelled'; job.finished_at = now(); broadcast('job.updated', job); broadcast('system.updated', systemStatus()); return
  }
  const [stage, index, label, , steps] = STAGES[job.sim.stage]
  job.sim.step += 1
  const done = STAGES.slice(0, job.sim.stage).reduce((s, x) => s + x[3], 0)
  const percent = Math.min(99.9, done + STAGES[job.sim.stage][3] * (job.sim.step / steps))
  job.progress = progress(stage, label, index, steps > 1 ? job.sim.step : null, steps > 1 ? steps : null, Number(percent.toFixed(1)), `${label}${steps > 1 ? ` step ${job.sim.step}/${steps}` : ''}`, job)
  if (job.settings.polygon_target === 5000 && stage === 'shape' && job.sim.step === 6) {
    job.status = 'failed'; job.finished_at = now(); job.error = { code: 'engine_error', message: 'CUDA out of memory (simulated by the mock backend)', details: null }
  } else if (job.sim.step >= steps) {
    job.sim.stage += 1; job.sim.step = 0
    if (job.sim.stage >= STAGES.length) {
      job.status = 'completed'; job.finished_at = now()
      job.progress = progress('done', 'Done', 9, null, null, 100, 'Completed', job)
      const total = (Date.parse(job.finished_at) - Date.parse(job.started_at)) / 1000
      job.result = {
        model_url: `${P}/jobs/${job.id}/model.glb`, download_url: `${P}/jobs/${job.id}/export/glb`, preview_url: `${P}/jobs/${job.id}/preview`,
        stats: { vertices: GLB.vertices, faces: GLB.faces, materials: 1, textures: 0, texture_size: job.settings.texture_size, has_uv: false, bbox_min: [-0.5, -0.5, -0.5], bbox_max: [0.5, 0.5, 0.5], dimensions: [1, 1, 1], file_size_bytes: GLB.buffer.length },
        timings: { preprocessing: 0.4, conditioning: 0.3, sparse_structure: 1.2, shape: 2.9, texture: 2.1, decoding: 0.5, exporting: 0.6, finalizing: 0.1, total },
        seed: job.seed, resolution: job.settings.resolution, effective_resolution: job.settings.resolution, texture_size: job.settings.texture_size, polygon_target: job.settings.polygon_target,
      }
    }
  }
  broadcast('job.updated', job)
  if (job.status !== 'running') broadcast('system.updated', systemStatus())
}
setInterval(tickJobs, 250)

function publicJob(job) {
  const { sim, cancel, primaryBytes, ...rest } = job
  void sim; void cancel; void primaryBytes
  if (rest.status === 'queued') {
    const queued = [...jobs.values()].filter((j) => j.status === 'queued').sort((a, b) => a.created_at.localeCompare(b.created_at))
    rest.queue_position = queued.findIndex((j) => j.id === job.id) + 1
  }
  return rest
}

/** The POST /jobs path (also used by workflow reconstruction steps): `{job}` or `{error: {status, code, message, details}}`. */
function createJob(body) {
  const set = sets.get(body.reference_set_id)
  if (!set) return { error: { status: 404, code: 'not_found', message: 'Reference set not found', details: null } }
  if (!set.images.length) return { error: { status: 422, code: 'validation_error', message: 'Reference set is empty', details: null } }
  const s = { resolution: 1024, seed: 0, randomize_seed: true, polygon_target: 500000, texture_size: 2048, remove_background: true, reference_mode: 'primary_only', advanced: null, ...body.settings }
  const primary = body.primary_image_id ?? set.images.find((i) => i.is_primary)?.id
  const job = {
    id: `job_${hex()}`, status: 'queued', engine: 'trellis2', created_at: now(), started_at: null, finished_at: null,
    reference_set_id: set.id, primary_image_id: primary, references: set.images.map((i) => ({ image_id: i.id, slot: i.slot, is_primary: i.id === primary })),
    settings: s, seed: s.randomize_seed ? Math.floor(Math.random() * 2147483647) : s.seed, queue_position: 1, error: null, result: null,
  }
  job.progress = progress('queued', 'Queued', 0, null, null, 0, 'Queued', job)
  job.primaryBytes = files.get(primary)
  jobs.set(job.id, job)
  broadcast('job.updated', job); broadcast('system.updated', systemStatus())
  return { job }
}

function cancelJob(job) {
  if (!job || ['completed', 'failed', 'cancelled'].includes(job.status)) return
  if (job.status === 'queued') { job.status = 'cancelled'; job.finished_at = now(); broadcast('job.updated', job) } else job.cancel = true
}

// ------------------------------------------------------------------------------------------------
// GLB (UV sphere, single PBR material) — synthetic geometry for the viewer test only
// ------------------------------------------------------------------------------------------------
function buildSphereGlb({ rings = 48, segments = 96, r = 0.5, bumpAmount = 0.06, color = [0.55, 0.42, 0.95, 1] } = {}) {
  const pos = [], nrm = [], idx = []
  for (let y = 0; y <= rings; y++) {
    const v = y / rings, phi = v * Math.PI
    for (let x = 0; x <= segments; x++) {
      const u = x / segments, theta = u * Math.PI * 2
      const nx = Math.sin(phi) * Math.cos(theta), ny = Math.cos(phi), nz = Math.sin(phi) * Math.sin(theta)
      const bump = 1 + bumpAmount * Math.sin(theta * 6) * Math.sin(phi * 5)
      pos.push(nx * r * bump, ny * r * bump, nz * r * bump); nrm.push(nx, ny, nz)
    }
  }
  for (let y = 0; y < rings; y++) for (let x = 0; x < segments; x++) {
    const a = y * (segments + 1) + x, b = a + segments + 1
    idx.push(a, b, a + 1, b, b + 1, a + 1)
  }
  const posBuf = Buffer.from(new Float32Array(pos).buffer), nrmBuf = Buffer.from(new Float32Array(nrm).buffer), idxBuf = Buffer.from(new Uint32Array(idx).buffer)
  const bin = Buffer.concat([posBuf, nrmBuf, idxBuf])
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity]
  for (let i = 0; i < pos.length; i += 3) for (let k = 0; k < 3; k++) { min[k] = Math.min(min[k], pos[i + k]); max[k] = Math.max(max[k], pos[i + k]) }
  const gltf = {
    asset: { version: '2.0', generator: 'img3d-mock' }, scene: 0, scenes: [{ nodes: [0] }], nodes: [{ mesh: 0, name: 'mock' }],
    meshes: [{ primitives: [{ attributes: { POSITION: 0, NORMAL: 1 }, indices: 2, material: 0 }] }],
    materials: [{ pbrMetallicRoughness: { baseColorFactor: color, metallicFactor: 0.1, roughnessFactor: 0.45 } }],
    buffers: [{ byteLength: bin.length }],
    bufferViews: [
      { buffer: 0, byteOffset: 0, byteLength: posBuf.length, target: 34962 },
      { buffer: 0, byteOffset: posBuf.length, byteLength: nrmBuf.length, target: 34962 },
      { buffer: 0, byteOffset: posBuf.length + nrmBuf.length, byteLength: idxBuf.length, target: 34963 },
    ],
    accessors: [
      { bufferView: 0, componentType: 5126, count: pos.length / 3, type: 'VEC3', min, max },
      { bufferView: 1, componentType: 5126, count: nrm.length / 3, type: 'VEC3' },
      { bufferView: 2, componentType: 5125, count: idx.length, type: 'SCALAR' },
    ],
  }
  let json = Buffer.from(JSON.stringify(gltf))
  if (json.length % 4) json = Buffer.concat([json, Buffer.alloc(4 - (json.length % 4), 0x20)])
  const header = Buffer.alloc(12), jh = Buffer.alloc(8), bh = Buffer.alloc(8)
  header.writeUInt32LE(0x46546c67, 0); header.writeUInt32LE(2, 4); header.writeUInt32LE(12 + 8 + json.length + 8 + bin.length, 8)
  jh.writeUInt32LE(json.length, 0); jh.writeUInt32LE(0x4e4f534a, 4); bh.writeUInt32LE(bin.length, 0); bh.writeUInt32LE(0x004e4942, 4)
  return { buffer: Buffer.concat([header, jh, json, bh, bin]), vertices: pos.length / 3, faces: idx.length / 3, min, max }
}

// ------------------------------------------------------------------------------------------------
// PNG encoder + synthetic before/after preview renders (shaded sphere stand-ins, test data only)
// ------------------------------------------------------------------------------------------------
const CRC_TABLE = (() => {
  const table = new Uint32Array(256)
  for (let n = 0; n < 256; n++) {
    let c = n
    for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1
    table[n] = c >>> 0
  }
  return table
})()

function crc32(buf) {
  let c = 0xffffffff
  for (const byte of buf) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

function pngChunk(type, data) {
  const length = Buffer.alloc(4); length.writeUInt32BE(data.length)
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
  const crc = Buffer.alloc(4); crc.writeUInt32BE(crc32(body))
  return Buffer.concat([length, body, crc])
}

function encodePng(width, height, rgba) {
  const stride = width * 4
  const raw = Buffer.alloc((stride + 1) * height)
  for (let y = 0; y < height; y++) rgba.copy(raw, y * (stride + 1) + 1, y * stride, (y + 1) * stride)
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0); ihdr.writeUInt32BE(height, 4); ihdr[8] = 8; ihdr[9] = 6
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
  return Buffer.concat([signature, pngChunk('IHDR', ihdr), pngChunk('IDAT', deflateSync(raw)), pngChunk('IEND', Buffer.alloc(0))])
}

/** "before" = faceted, noisy, with dark hole blotches; "after" = smooth. View changes zoom / pattern rotation. */
function renderPreview(view, variant, size = 192) {
  const rgba = Buffer.alloc(size * size * 4)
  const zoom = view === 'face' ? 1.7 : 1
  const yaw = { front: 0, three_quarter: 0.75, side: 1.5, face: 0 }[view] ?? 0
  const base = variant === 'after' ? [0.42, 0.8, 0.64] : [0.62, 0.52, 0.95]
  const light = [-0.42, 0.58, 0.7]
  const norm = Math.hypot(...light); light.forEach((v, i) => { light[i] = v / norm })
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      const i = (y * size + x) * 4
      const u = (((x + 0.5) / size) * 2 - 1) * (1.22 / zoom)
      const v = -(((y + 0.5) / size) * 2 - 1) * (1.22 / zoom)
      const d2 = u * u + v * v
      if (d2 > 1) {
        const g = 18 + Math.round(10 * (1 - y / size))
        rgba[i] = g; rgba[i + 1] = g + 3; rgba[i + 2] = g + 12; rgba[i + 3] = 255
        continue
      }
      let n = [u, v, Math.sqrt(1 - d2)]
      const theta = Math.atan2(n[0], n[2]) + yaw
      const phi = Math.acos(Math.max(-1, Math.min(1, n[1])))
      let hole = false
      if (variant === 'before') {
        n = n.map((c) => Math.round(c * 7) / 7)
        const len = Math.hypot(...n) || 1
        n = n.map((c) => c / len + (Math.sin(x * 12.9898 + y * 78.233) * 43758.5453 % 1) * 0.06)
        hole = Math.sin(theta * 7) * Math.sin(phi * 5) > 0.93
      }
      const lambert = Math.max(0, n[0] * light[0] + n[1] * light[1] + n[2] * light[2])
      const stripe = 0.06 * Math.sin(theta * 6) * Math.sin(phi * 5)
      const rim = Math.pow(1 - Math.max(0, n[2]), 3) * 0.3
      const shade = hole ? 0.08 : 0.2 + 0.78 * lambert + stripe
      for (let k = 0; k < 3; k++) rgba[i + k] = Math.max(0, Math.min(255, Math.round((base[k] * shade + rim) * 255)))
      rgba[i + 3] = 255
    }
  }
  return encodePng(size, size, rgba)
}

const PREVIEW_VIEWS = ['front', 'three_quarter', 'side', 'face']
const PREVIEWS = new Map()
for (const view of PREVIEW_VIEWS) for (const variant of ['before', 'after']) PREVIEWS.set(`${view}_${variant}.png`, { view, variant, png: renderPreview(view, variant) })

// Tuned model: finer (more segments), smoother (smaller bump) and a different colour so the toggle is obvious.
const TUNED_GLB = buildSphereGlb({ rings: 64, segments: 128, bumpAmount: 0.015, color: [0.36, 0.82, 0.64, 1] })

// ------------------------------------------------------------------------------------------------
// Blender runtime simulation (Auto Mesh Tuner DESIGN.md §2 selection policy, §6 BlenderRuntimeOut)
// ------------------------------------------------------------------------------------------------
const TICK_MS = 250
const RUNTIME_ROOT = 'E:\\14-OCT-2026\\Img3D\\backend\\runtime\\blender'
const MIN_VERSION = [5, 1, 0]
const ACTIVE_DOWNLOAD = ['resolving', 'downloading', 'verifying', 'extracting', 'validating']
const compareVersion = (a, b) => a[0] - b[0] || a[1] - b[1] || a[2] - b[2]

function blenderInstall({ executable, kind, version, lts = false, origins }) {
  const text = version.join('.')
  const compatible = compareVersion(version, MIN_VERSION) >= 0
  return {
    id: createHash('sha1').update(executable.toLowerCase()).digest('hex').slice(0, 12),
    executable, kind, origins, version: text, version_label: `${text}${lts ? ' LTS' : ''}`, version_tuple: version,
    cycle: 'release', is_lts: lts, is_stable: true, build_hash: hex(12), build_date: null, architecture: 'x64',
    valid: true, compatible, incompatible_reason: compatible ? null : `Blender ${text} is older than ${MIN_VERSION[0]}.${MIN_VERSION[1]}`,
    error: null, probe_ms: 110 + Math.round(Math.random() * 60),
  }
}

const SYSTEM_52 = blenderInstall({ executable: 'C:\\Program Files\\Blender Foundation\\Blender 5.2\\blender.exe', kind: 'system', version: [5, 2, 2], lts: true, origins: ['program_files', 'registry_uninstall', 'progid'] })
const SYSTEM_42 = blenderInstall({ executable: 'C:\\Program Files\\Blender Foundation\\Blender 4.2\\blender.exe', kind: 'system', version: [4, 2, 17], lts: true, origins: ['program_files', 'registry_uninstall'] })
const EMBEDDED_52 = blenderInstall({ executable: `${RUNTIME_ROOT}\\blender-5.2.2-windows-x64\\blender.exe`, kind: 'embedded', version: [5, 2, 2], lts: true, origins: ['embedded_dir'] })
const RELEASE = {
  version: '5.2.2', series: '5.2', file_name: 'blender-5.2.2-windows-x64.zip',
  url: 'https://download.blender.org/release/Blender5.2/blender-5.2.2-windows-x64.zip', size_bytes: 404453484,
  sha256: '3849d17a682cba006075aaa3f3597ecb5c9c30ec31035b2e092c53e40679b535',
  checksum_url: 'https://download.blender.org/release/Blender5.2/blender-5.2.2.sha256', architecture: 'x64', is_lts: true, release_date: null,
}

const blender = {
  installs: BLENDER_SCENARIO === 'ready' ? [SYSTEM_52, SYSTEM_42] : BLENDER_SCENARIO === 'none' ? [] : [SYSTEM_42],
  mode: 'auto', pinned: null, detecting: false, download: null, release: null, partialBytes: 0,
  lastScanAt: now(), scanMs: 386.4, timer: null,
}

function sortedInstalls() {
  return [...blender.installs].sort((a, b) => compareVersion(b.version_tuple, a.version_tuple))
}

/** DESIGN.md §2 selection policy (without the override / non-stable branches). */
function selectRuntime() {
  const warnings = []
  const usable = sortedInstalls().filter((install) => install.compatible && install.is_stable)
  const systems = usable.filter((install) => install.kind === 'system')
  const embedded = usable.filter((install) => install.kind === 'embedded')
  const pinned = blender.pinned ? blender.installs.find((install) => install.executable.toLowerCase() === blender.pinned.toLowerCase()) : null
  if (blender.mode === 'embedded') {
    if (pinned?.kind === 'embedded' && pinned.compatible) return { selected: pinned, reason: 'Embedded Blender pinned with Change Runtime', warnings }
    if (embedded[0]) return { selected: embedded[0], reason: 'Embedded mode: highest compatible embedded Blender', warnings }
    if (systems[0]) warnings.push(`Embedded Blender is selected but not installed — using system Blender ${systems[0].version}.`)
  }
  if (blender.mode === 'system' && pinned) {
    if (pinned.kind === 'system' && pinned.compatible) return { selected: pinned, reason: 'System Blender pinned with Change Runtime', warnings }
    warnings.push(`The pinned Blender ${pinned.version} cannot be used — falling back to the highest compatible install.`)
  }
  if (systems[0]) {
    return { selected: systems[0], reason: blender.mode === 'auto' ? 'Auto: highest stable compatible system Blender' : 'Highest stable compatible system Blender', warnings }
  }
  if (embedded[0]) {
    if (blender.mode === 'system') warnings.push(`No compatible system Blender is installed — using embedded Blender ${embedded[0].version}.`)
    return { selected: embedded[0], reason: 'Highest compatible embedded Blender (no compatible system Blender)', warnings }
  }
  return { selected: null, reason: null, warnings }
}

function runtimeStatus() {
  const { selected, reason, warnings } = selectRuntime()
  const downloadActive = !!blender.download && ACTIVE_DOWNLOAD.includes(blender.download.state)
  let message = null
  if (!selected) {
    const valid = sortedInstalls().filter((install) => install.valid)
    message = valid.length
      ? `Blender ${valid[0].version} is installed but Auto Mesh Tuner requires Blender 5.1 or newer. Download a supported portable Blender?`
      : 'No Blender installation was found. Auto Mesh Tuner requires Blender 5.1 or newer. Download a supported portable Blender?'
  }
  let state = selected ? 'ready' : downloadActive ? 'downloading' : 'needs_download'
  if (blender.detecting) state = 'detecting'
  return {
    state, mode: blender.mode, min_version: '5.1',
    selected: selected ? { ...selected, selected: true } : null, selection_reason: reason,
    installations: sortedInstalls().map((install) => ({ ...install, selected: install.id === selected?.id })),
    message, warnings, runtime_root: RUNTIME_ROOT, can_download: ALLOW_DOWNLOAD,
    download: blender.download ? { ...blender.download } : null, release: blender.release,
    last_scan_at: blender.lastScanAt, scan_duration_ms: blender.scanMs, error: null,
  }
}

function patchDownload(patch) {
  blender.download = { ...blender.download, ...patch, updated_at: now() }
}

function failDownload(error, code) {
  clearInterval(blender.timer); blender.timer = null
  patchDownload({ state: 'failed', error, error_code: code, speed_bps: null, eta_s: null })
  broadcast('blender.updated', runtimeStatus())
}

/** resolving → downloading (bytes/speed/ETA, ≤ 4 frames/s) → verifying → extracting → validating → completed. */
function startBlenderDownload() {
  const total = RELEASE.size_bytes
  const resumedFrom = blender.partialBytes
  blender.release = RELEASE
  blender.download = {
    state: 'resolving', version: RELEASE.version, file_name: RELEASE.file_name, url: RELEASE.url,
    bytes_done: resumedFrom, bytes_total: total, percent: (resumedFrom / total) * 100, speed_bps: null, eta_s: null,
    resumed: resumedFrom > 0, verified: null, error: null, error_code: null, install_dir: null, started_at: now(), updated_at: now(),
  }
  const perTick = total / ((DOWNLOAD_SECONDS * 1000) / TICK_MS)
  let phaseTicks = 0
  clearInterval(blender.timer)
  blender.timer = setInterval(() => {
    phaseTicks += 1
    const download = blender.download
    if (download.state === 'resolving' && phaseTicks >= 3) {
      patchDownload({ state: 'downloading' }); phaseTicks = 0
    } else if (download.state === 'downloading') {
      if (DOWNLOAD_FAIL === 'network' && download.bytes_done > total * 0.45) {
        blender.partialBytes = download.bytes_done
        return failDownload('Connection reset while downloading from download.blender.org (simulated). The partial file was kept for resuming.', 'blender_download_failed')
      }
      const step = perTick * (0.75 + Math.random() * 0.5)
      const done = Math.min(total, download.bytes_done + step)
      const speed = step * (1000 / TICK_MS)
      patchDownload({ bytes_done: Math.round(done), percent: (done / total) * 100, speed_bps: speed, eta_s: (total - done) / speed })
      if (done >= total) { patchDownload({ state: 'verifying', speed_bps: null, eta_s: null }); phaseTicks = 0 }
    } else if (download.state === 'verifying' && phaseTicks >= 6) {
      if (DOWNLOAD_FAIL === 'checksum') {
        blender.partialBytes = 0
        return failDownload(`SHA-256 mismatch for ${RELEASE.file_name}: expected ${RELEASE.sha256.slice(0, 16)}…, got 9f1c0d2e7a4b55c3… The corrupt file was deleted.`, 'blender_checksum_mismatch')
      }
      patchDownload({ state: 'extracting', verified: true }); phaseTicks = 0
    } else if (download.state === 'extracting' && phaseTicks >= 8) {
      patchDownload({ state: 'validating' }); phaseTicks = 0
    } else if (download.state === 'validating' && phaseTicks >= 3) {
      clearInterval(blender.timer); blender.timer = null; blender.partialBytes = 0
      if (!blender.installs.includes(EMBEDDED_52)) blender.installs.push(EMBEDDED_52)
      blender.lastScanAt = now()
      patchDownload({ state: 'completed', percent: 100, install_dir: `${RUNTIME_ROOT}\\blender-5.2.2-windows-x64` })
    }
    broadcast('blender.updated', runtimeStatus())
  }, TICK_MS)
}

function cancelBlenderDownload() {
  clearInterval(blender.timer); blender.timer = null
  blender.partialBytes = blender.download.bytes_done
  patchDownload({ state: 'cancelled', speed_bps: null, eta_s: null })
}

if (BLENDER_SCENARIO === 'downloading') setTimeout(startBlenderDownload, 0)

// ------------------------------------------------------------------------------------------------
// Auto Tune runs (protocol steps DESIGN.md §3, TuningRunOut §6) — test data only
// ------------------------------------------------------------------------------------------------
const STEP_PLAN = [
  ['load', 'Load mesh', 0.05, 3], ['analyze', 'Mesh analysis', 0.07, 3], ['geometry_repair', 'Geometry repair', 0.3, 8],
  ['face_refinement', 'Face refinement', 0.15, 5], ['topology_tuning', 'Topology tuning', 0.2, 6],
  ['rig_optimization', 'Rig optimization', 0.05, 1], ['validation', 'Validation', 0.13, 4], ['export', 'Save results', 0.05, 2],
]
const STEP_TICK_SCALE = (TUNING_SECONDS * 1000) / TICK_MS / STEP_PLAN.reduce((sum, step) => sum + step[3], 0)
const BALANCED = {
  preset: 'balanced', remove_inner_shell: true, fill_holes: true, max_hole_edges: 300, remove_islands: true, island_min_ratio: 0.001,
  smoothing: 0.5, refine_face: true, eye_correction: true, symmetry: 'auto', refine_density: true, quads: false, subdivision: 'auto',
  optimize_rig: true, render_previews: true, save_blend: false,
}
const PRESETS = {
  conservative: { ...BALANCED, preset: 'conservative', smoothing: 0.25, refine_density: false, max_hole_edges: 120 },
  balanced: BALANCED,
  strong: { ...BALANCED, preset: 'strong', smoothing: 0.8, max_hole_edges: 1000, island_min_ratio: 0.003 },
}
const MAX_ACTIVE_TUNINGS = 4
const tunings = new Map()

function effectiveOptions(body) {
  const preset = body.preset ?? 'balanced'
  const options = { ...PRESETS[preset] }
  for (const key of Object.keys(BALANCED)) if (key !== 'preset' && body[key] !== undefined && typeof body[key] === typeof BALANCED[key]) options[key] = body[key]
  return options
}

const metric = (key, label, unit, before, after, better) => ({ key, label, unit, before, after, better })

function tuningMetrics() {
  return [
    metric('vertices', 'Vertices', null, GLB.vertices, TUNED_GLB.vertices, 'neutral'),
    metric('faces', 'Faces', null, GLB.faces, TUNED_GLB.faces, 'neutral'),
    metric('inner_shell_faces', 'Inner shell faces', null, 6512, 0, 'lower'),
    metric('non_manifold_edges', 'Non-manifold edges', null, 1834, 0, 'lower'),
    metric('boundary_edges', 'Boundary edges', null, 412, 0, 'lower'),
    metric('holes', 'Holes', null, 37, 0, 'lower'),
    metric('largest_hole_edges', 'Largest hole', 'edges', 112, 0, 'lower'),
    metric('components', 'Components', null, 214, 3, 'lower'),
    metric('small_components', 'Floating parts', null, 209, 0, 'lower'),
    metric('degenerate_faces', 'Degenerate faces', null, 96, 0, 'lower'),
    metric('inconsistent_normal_edges', 'Inconsistent normals', null, 58, 0, 'lower'),
    metric('symmetry_error_pct', 'Symmetry error', '%', 0.112, 0.041, 'lower'),
    metric('surface_area', 'Surface area', null, 3.2142, 3.1987, 'neutral'),
    metric('mean_edge_length', 'Mean edge length', null, 0.02134, 0.01608, 'neutral'),
    metric('deviation_mean_pct', 'Mean deviation', '%', null, 0.082, 'lower'),
    metric('deviation_p95_pct', 'Deviation p95', '%', null, 0.31, 'lower'),
    metric('materials', 'Materials', null, 1, 1, 'neutral'),
    metric('armatures', 'Armatures', null, 0, 0, 'neutral'),
  ]
}

function tuningValidation(warn) {
  const checks = [
    { id: 'manifold', label: 'Manifold', status: 'pass', value: 0, threshold: 0, message: 'No non-manifold edges' },
    { id: 'watertight', label: 'Watertight', status: 'pass', value: 0, threshold: 0, message: 'No open boundaries' },
    { id: 'inner_shell', label: 'Inner shell removed', status: 'pass', value: 0, threshold: 0, message: null },
    { id: 'normals', label: 'Consistent normals', status: 'pass', value: 0, threshold: 0, message: null },
    { id: 'deviation', label: 'Surface deviation p95', status: 'pass', value: 0.31, threshold: 1, message: '% of the bounding-box diagonal' },
    { id: 'textures', label: 'Textures preserved', status: 'pass', value: true, threshold: null, message: null },
    { id: 'symmetry', label: 'Mirror symmetry', status: warn ? 'warn' : 'pass', value: 0.041, threshold: 0.05, message: warn ? 'Right eye recess is still slightly deeper than the left' : null },
  ]
  return { passed: !checks.some((check) => check.status === 'fail'), checks }
}

function stepOutcome(id, run) {
  const warn = TUNING_SCENARIO === 'warnings'
  switch (id) {
    case 'load': return { status: 'done', message: `Loaded 1 mesh · ${GLB.vertices.toLocaleString('en-US')} vertices · ${GLB.faces.toLocaleString('en-US')} faces` }
    case 'analyze': return { status: 'done', message: 'Inner shell on 79% of faces · 37 holes · 214 loose parts' }
    case 'geometry_repair': return { status: 'done', message: 'Removed 6,512 inner-shell faces, filled 37 holes, removed 209 floating parts' }
    case 'face_refinement':
      if (!run.options.refine_face) return { status: 'skipped', message: 'Disabled in the options' }
      return warn
        ? { status: 'warning', message: 'Eye correction skipped for the right eye (low confidence)', warning: 'Face refinement: eye correction was skipped for the right eye (low confidence).' }
        : { status: 'done', message: 'Smoothed eye recesses and the facial midline crease' }
    case 'topology_tuning': return { status: 'done', message: `Refined ${run.options.refine_density ? '41,220' : '0'} faceted triangles · denoise ${Math.round(run.options.smoothing * 100)}%` }
    case 'rig_optimization': return { status: 'skipped', message: 'No armature in the model' }
    case 'validation': return { status: warn ? 'warning' : 'done', message: warn ? '6 checks passed · 1 needs review' : 'All 7 checks passed' }
    default: return { status: 'done', message: `Saved tuned.glb (${(TUNED_GLB.buffer.length / 1024).toFixed(0)} KB)${run.options.render_previews ? ' and 8 previews' : ''}` }
  }
}

function publicRun(run) {
  const { sim, ...rest } = run
  void sim
  return rest
}

function completeRun(run) {
  const job = jobs.get(run.job_id)
  run.status = 'completed'
  run.finished_at = now()
  run.progress = { ...run.progress, percent: 100, message: 'Completed', updated_at: now() }
  run.metrics = tuningMetrics()
  run.validation = tuningValidation(TUNING_SCENARIO === 'warnings')
  if (run.options.quads) run.warnings.push('Quads are re-triangulated in the GLB export (kept only in .blend).')
  const base = `${P}/tunings/${run.id}`
  run.result = {
    model_url: `${base}/model.glb`, download_url: `${base}/export/glb`, report_url: `${base}/report`, log_url: `${base}/log`,
    previews: run.options.render_previews ? [...PREVIEWS].map(([name, p]) => ({ name, view: p.view, variant: p.variant, url: `${base}/previews/${name}` })) : [],
    stats: {
      vertices: TUNED_GLB.vertices, faces: TUNED_GLB.faces, materials: 1, textures: 0, texture_size: job?.settings.texture_size ?? null, has_uv: false,
      bbox_min: TUNED_GLB.min, bbox_max: TUNED_GLB.max, dimensions: TUNED_GLB.max.map((v, i) => v - TUNED_GLB.min[i]), file_size_bytes: TUNED_GLB.buffer.length,
    },
  }
}

function tickTunings() {
  const running = [...tunings.values()].find((run) => run.status === 'running')
  if (!running) {
    const next = [...tunings.values()].filter((run) => run.status === 'queued').sort((a, b) => a.created_at.localeCompare(b.created_at))[0]
    if (!next) return
    next.sim.wait += 1
    if (next.sim.wait < 3) return
    next.status = 'running'
    next.started_at = now()
    next.sim.stepStarted = Date.now()
    next.steps[0].status = 'running'
    next.progress = { percent: 0, step_id: 'load', step_index: 1, step_count: STEP_PLAN.length, message: 'Starting Blender 5.2.2 LTS in the background…', updated_at: now() }
    return broadcast('tuning.updated', publicRun(next))
  }
  const run = running
  const current = run.steps[run.sim.step]
  if (run.sim.cancel) {
    current.status = 'skipped'; current.message = 'Cancelled'
    run.status = 'cancelled'; run.finished_at = now()
    run.progress = { ...run.progress, message: 'Cancelled', updated_at: now() }
    return broadcast('tuning.updated', publicRun(run))
  }
  const [id, , weight, ticks] = STEP_PLAN[run.sim.step]
  const total = Math.max(1, Math.round(ticks * STEP_TICK_SCALE))
  run.sim.tick += 1
  const fraction = Math.min(1, run.sim.tick / total)
  const done = STEP_PLAN.slice(0, run.sim.step).reduce((sum, step) => sum + step[2], 0)
  run.progress = {
    percent: Number(((done + weight * fraction) * 100).toFixed(1)), step_id: id, step_index: run.sim.step + 1, step_count: STEP_PLAN.length,
    message: `${STEP_PLAN[run.sim.step][1]} — ${Math.round(fraction * 100)}%`, updated_at: now(),
  }
  if (TUNING_SCENARIO === 'fail' && id === 'geometry_repair' && fraction >= 0.5) {
    current.status = 'failed'; current.message = 'Hole filling failed on a non-manifold rim'
    current.duration_s = (Date.now() - run.sim.stepStarted) / 1000
    run.status = 'failed'; run.finished_at = now()
    run.error = { code: 'blender_processing_failed', message: 'Geometry repair failed: bmesh.ops.holes_fill raised RuntimeError (simulated by the mock backend).', details: { step: 'geometry_repair', exit_code: 4 } }
    return broadcast('tuning.updated', publicRun(run))
  }
  if (fraction >= 1) {
    const outcome = stepOutcome(id, run)
    current.status = outcome.status
    current.message = outcome.message
    current.duration_s = (Date.now() - run.sim.stepStarted) / 1000
    if (outcome.warning) run.warnings.push(outcome.warning)
    run.sim.step += 1
    run.sim.tick = 0
    run.sim.stepStarted = Date.now()
    if (run.sim.step >= STEP_PLAN.length) completeRun(run)
    else {
      const [nextId, nextLabel] = STEP_PLAN[run.sim.step]
      run.steps[run.sim.step].status = 'running'
      run.progress = { ...run.progress, step_id: nextId, step_index: run.sim.step + 1, message: `${nextLabel}…` }
    }
  }
  broadcast('tuning.updated', publicRun(run))
}
setInterval(tickTunings, TICK_MS)

function createTuning(job, body) {
  const id = `tn_${hex()}`
  const selected = selectRuntime().selected
  const run = {
    id, job_id: job.id, status: 'queued', created_at: now(), started_at: null, finished_at: null, options: effectiveOptions(body),
    progress: { percent: 0, step_id: null, step_index: 0, step_count: STEP_PLAN.length, message: 'Queued', updated_at: now() },
    steps: STEP_PLAN.map(([stepId, label]) => ({ id: stepId, label, status: 'pending', message: null, duration_s: null })),
    metrics: [], warnings: [], validation: null,
    blender: selected ? { executable: selected.executable, version: selected.version_label, kind: selected.kind } : null,
    result: null, error: null, sim: { wait: 0, step: 0, tick: 0, stepStarted: Date.now(), cancel: false },
  }
  tunings.set(id, run)
  return run
}

function tuningReport(run) {
  return {
    schema: 1, tool: 'Img3D Auto Mesh Tuner', script_version: 'mock', protocol: 1, blender_version: run.blender?.version ?? null,
    created_at: run.finished_at, duration_s: run.started_at && run.finished_at ? (Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000 : null,
    input: { path: `runtime\\temp\\tuning\\${run.id}\\input.glb`, format: 'glb', size_bytes: GLB.buffer.length },
    options: run.options, steps: run.steps, metrics: run.metrics, warnings: run.warnings, validation: run.validation,
    outputs: { model: 'tuned.glb', blend: null, previews: run.result?.previews.map(({ name, view, variant }) => ({ name, view, variant })) ?? [] },
    environment: { gpu: true, gpu_renderer: 'mock', python: '3.13.13', numpy: '2.3.4' }, note: 'TEST DOUBLE — generated by test/frontend/mock_backend.mjs',
  }
}

function tuningLog(run) {
  const lines = [`Blender ${run.blender?.version ?? '?'} (mock backend log — test double)`, 'Read prefs: <factory startup>']
  lines.push(`@@AMT@@{"event":"hello","protocol":1,"script_version":"mock","blender_version":"${run.blender?.version ?? ''}"}`)
  for (const step of run.steps) if (step.status !== 'pending') lines.push(`@@AMT@@${JSON.stringify({ event: 'step', id: step.id, status: step.status, message: step.message, duration_s: step.duration_s })}`)
  if (run.error) lines.push(`@@AMT@@${JSON.stringify({ event: 'error', code: run.error.code, message: run.error.message, step: 'geometry_repair', traceback: null })}`)
  lines.push(`Blender quit (status: ${run.status})`)
  return `${lines.join('\n')}\n`
}

// ------------------------------------------------------------------------------------------------
// Seeded completed jobs (--seed-jobs N) so the tuner UI can be exercised without generating first
// ------------------------------------------------------------------------------------------------
function seedJobs(count) {
  for (let i = 0; i < count; i++) {
    const finished = Date.now() - i * 3600e3 - 60e3
    const settings = { resolution: 1024, seed: 1000 + i, randomize_seed: false, polygon_target: 500000, texture_size: 2048, remove_background: true, reference_mode: 'primary_only', advanced: null }
    const job = {
      id: `job_${hex()}`, status: 'completed', engine: 'trellis2', created_at: new Date(finished - 95e3).toISOString(),
      started_at: new Date(finished - 90e3).toISOString(), finished_at: new Date(finished).toISOString(),
      reference_set_id: `rs_${hex()}`, primary_image_id: null, references: [], settings, seed: settings.seed, queue_position: null, error: null,
      progress: { stage: 'done', stage_label: 'Done', stage_index: 9, stage_count: 9, step: null, total_steps: null, percent: 100, message: 'Completed', elapsed_seconds: 90, eta_seconds: null, updated_at: new Date(finished).toISOString() },
      primaryBytes: { bytes: PREVIEWS.get('front_before.png').png, mime: 'image/png' },
    }
    job.result = {
      model_url: `${P}/jobs/${job.id}/model.glb`, download_url: `${P}/jobs/${job.id}/export/glb`, preview_url: `${P}/jobs/${job.id}/preview`,
      stats: { vertices: GLB.vertices, faces: GLB.faces, materials: 1, textures: 0, texture_size: 2048, has_uv: false, bbox_min: GLB.min, bbox_max: GLB.max, dimensions: GLB.max.map((v, k) => v - GLB.min[k]), file_size_bytes: GLB.buffer.length },
      timings: { preprocessing: 0.4, conditioning: 0.3, sparse_structure: 11.2, shape: 32.9, texture: 28.1, decoding: 6.5, exporting: 9.6, finalizing: 0.1, total: 89.1 },
      seed: settings.seed, resolution: 1024, effective_resolution: 1024, texture_size: 2048, polygon_target: 500000,
    }
    jobs.set(job.id, job)
  }
}
seedJobs(SEED_JOBS)

// ------------------------------------------------------------------------------------------------
// HTTP helpers
// ------------------------------------------------------------------------------------------------
const tokens = new Set()
function send(res, status, body, headers = {}) {
  const rid = hex(6)
  if (body === undefined) { res.writeHead(status, { 'X-Request-ID': rid, ...headers }); return res.end() }
  if (Buffer.isBuffer(body)) { res.writeHead(status, { 'Content-Length': body.length, 'X-Request-ID': rid, ...headers }); return res.end(body) }
  const text = JSON.stringify(body)
  res.writeHead(status, { 'Content-Type': 'application/json', 'X-Request-ID': rid, ...headers })
  res.end(text)
}
const fail = (res, status, code, message, details = null) => send(res, status, { error: { code, message, details, request_id: hex(6) } })

/** The raw request body; read once per request (before routing) so the request log sees the exact bytes. */
function readBody(req) {
  if (req.rawBody) return Promise.resolve(req.rawBody)
  return new Promise((resolve) => { const chunks = []; req.on('data', (c) => chunks.push(c)); req.on('end', () => resolve((req.rawBody = Buffer.concat(chunks)))) })
}

// Request log (GET/DELETE /api/__test/requests): the only source of the request bytes for the browser-pane checks.
const REQUEST_LOG_MAX = 5000
const requestLog = []
const markCreated = (res, id) => { res.img3dCreatedId = id }

function logRequest(req, res, url, ts) {
  const raw = req.rawBody ?? Buffer.alloc(0)
  const type = req.headers['content-type'] ?? ''
  const textual = !type || /json|text|x-www-form-urlencoded/.test(type)
  const query = Object.fromEntries([...url.searchParams].map(([key, value]) => [key, key === 'token' ? 'REDACTED' : value]))
  requestLog.push({
    ts, method: req.method, path: url.pathname, query, status: res.statusCode,
    body: raw.length && textual ? raw.toString('utf8') : null, body_sha256: raw.length ? createHash('sha256').update(raw).digest('hex') : null,
    created_id: res.img3dCreatedId ?? null,
  })
  if (requestLog.length > REQUEST_LOG_MAX) requestLog.shift()
}

function parseMultipart(buf, boundary) {
  const parts = {}
  const delim = Buffer.from(`--${boundary}`)
  let start = buf.indexOf(delim)
  while (start !== -1) {
    const next = buf.indexOf(delim, start + delim.length)
    if (next === -1) break
    const part = buf.subarray(start + delim.length + 2, next - 2)
    const sep = part.indexOf('\r\n\r\n')
    const head = part.subarray(0, sep).toString()
    const data = part.subarray(sep + 4)
    const name = /name="([^"]+)"/.exec(head)?.[1]
    const filename = /filename="([^"]*)"/.exec(head)?.[1]
    const type = /Content-Type: ([^\r\n]+)/i.exec(head)?.[1]
    if (name) parts[name] = filename !== undefined ? { filename, type, data } : data.toString()
    start = next
  }
  return parts
}

function authorized(req, url) {
  if (!AUTH) return true
  const header = req.headers.authorization ?? ''
  const token = header.startsWith('Bearer ') ? header.slice(7) : url.searchParams.get('token')
  return !!token && tokens.has(token)
}

/** DESIGN §6.0 "J" routes: Bearer header only (file routes "F" also take ?token=, see `authorized`). */
function headerAuthorized(req) {
  if (!AUTH) return true
  const header = req.headers.authorization ?? ''
  return header.startsWith('Bearer ') && tokens.has(header.slice(7))
}

// ------------------------------------------------------------------------------------------------
// Part-aware routes (DESIGN.md §6.1–§6.6) and test-only scenario switching
// ------------------------------------------------------------------------------------------------
const partAware = createPartAwareMock({
  send, fail, readBody, broadcast, now, hex, encodePng, createJob, cancelJob, jobs, sets, files, scenario,
  createReferenceSet, addReferenceImage, setView, markCreated, headerAuthorized, baseGlb: GLB.buffer,
})
setInterval(partAware.tick, TICK_MS)

const SCENARIO_KEYS = { features: FEATURE_SCENARIOS, parts: PARTS_SCENARIOS, t2i_cold_seconds: 'number', tuner_enabled: 'boolean', queue_full: 'boolean' }
const scenarioView = () => ({ features: scenario.features, parts: scenario.parts, t2i_cold_seconds: scenario.t2i_cold_seconds, tuner_enabled: scenario.tuner_enabled, queue_full: scenario.queue_full, strict_requests: scenario.strict ? 'on' : 'off' })

/** POST /api/__test/scenario body → list of 422 details (empty when valid). */
function scenarioDetails(body) {
  const details = []
  for (const [key, value] of Object.entries(body)) {
    const rule = SCENARIO_KEYS[key]
    const ok = Array.isArray(rule) ? rule.includes(value) : rule === 'number' ? Number.isFinite(value) && value >= 0 : rule === 'boolean' ? typeof value === 'boolean' : false
    if (!ok) details.push({ loc: ['body', key], type: rule ? 'value_error' : 'extra_forbidden', msg: rule ? `Invalid value for ${key}` : 'Extra inputs are not permitted' })
  }
  return details
}

// ------------------------------------------------------------------------------------------------
// Router
// ------------------------------------------------------------------------------------------------
const server = createServer(async (req, res) => {
  const url = new URL(req.url, `http://${req.headers.host}`)
  const path = url.pathname
  const method = req.method
  const ts = now()
  let m
  try {
    await readBody(req)
    if (!path.startsWith('/api/__test/')) res.on('finish', () => logRequest(req, res, url, ts))
    if (path === '/api/__test/requests') {
      if (method === 'DELETE') { requestLog.length = 0; return send(res, 204) }
      return send(res, 200, requestLog)
    }
    if (path === '/api/__test/scenario') {
      if (method === 'POST') {
        let body
        try { body = JSON.parse(req.rawBody.toString() || '{}') } catch { return fail(res, 422, 'validation_error', 'Request validation failed', [{ loc: ['body'], type: 'json_invalid', msg: 'Invalid JSON' }]) }
        const details = scenarioDetails(body ?? {})
        if (details.length) return fail(res, 422, 'validation_error', 'Request validation failed', details)
        Object.assign(scenario, body)
        partAware.scenarioChanged()
      }
      return send(res, 200, scenarioView())
    }
    if (path.startsWith('/api/__test/sample/')) {
      const name = decodeURIComponent(path.slice('/api/__test/sample/'.length))
      const all = readdirSync(IMAGES_DIR).flatMap((d) => (statSync(join(IMAGES_DIR, d)).isDirectory() ? readdirSync(join(IMAGES_DIR, d)).map((f) => join(IMAGES_DIR, d, f)) : []))
      const file = all.find((f) => f.endsWith(name))
      return file ? send(res, 200, readFileSync(file), { 'Content-Type': 'image/png' }) : fail(res, 404, 'not_found', 'sample not found')
    }
    if (path === `${P}/health`) return send(res, 200, { status: 'ok', app: 'Img3D', version: '1.0.0', uptime_seconds: process.uptime(), time: now() })
    if (path === `${P}/auth/status`) return send(res, 200, { auth_required: AUTH, authenticated: authorized(req, url) && AUTH, username: AUTH && authorized(req, url) ? 'tester' : null })
    if (path === `${P}/auth/login` && method === 'POST') {
      const body = JSON.parse((await readBody(req)).toString() || '{}')
      if (body.username !== process.env.MOCK_USER || body.password !== process.env.MOCK_PASSWORD || !process.env.MOCK_USER) return fail(res, 401, 'unauthorized', 'Invalid credentials')
      const token = hex(32); tokens.add(token)
      return send(res, 200, { token, token_type: 'bearer', expires_at: new Date(Date.now() + 12 * 3600e3).toISOString() })
    }
    if (path === `${P}/auth/logout`) return send(res, 204)
    if (!authorized(req, url)) return fail(res, 401, 'unauthorized', 'Authentication required')

    if (path === `${P}/system` && method === 'GET') return send(res, 200, systemStatus())
    if (path === `${P}/system/engine/load` && method === 'POST') return send(res, 202, systemStatus())

    // Part-aware routes (features, models, image generations, segmentations, part analyses, workflows)
    if (await partAware.handle(req, res, url)) return

    // Reference sets
    if (path === `${P}/reference-sets` && method === 'POST') {
      const set = createReferenceSet(null)
      markCreated(res, set.id)
      return send(res, 201, setView(set))
    }
    if (path === `${P}/reference-sets` && method === 'GET') return send(res, 200, { items: [...sets.values()].map(setView) })
    if ((m = new RegExp(`^${P}/reference-sets/(rs_\\w+)$`).exec(path))) {
      const set = sets.get(m[1]); if (!set) return fail(res, 404, 'not_found', 'Reference set not found')
      if (method === 'DELETE') { sets.delete(set.id); return send(res, 204) }
      return send(res, 200, setView(set))
    }
    if ((m = new RegExp(`^${P}/reference-sets/(rs_\\w+)/primary$`).exec(path)) && method === 'PUT') {
      const set = sets.get(m[1]); if (!set) return fail(res, 404, 'not_found', 'Reference set not found')
      const { image_id } = JSON.parse((await readBody(req)).toString())
      if (!set.images.some((i) => i.id === image_id)) return fail(res, 404, 'not_found', 'Image not found')
      set.images.forEach((i) => { i.is_primary = i.id === image_id }); touch(set)
      return send(res, 200, setView(set))
    }
    if ((m = new RegExp(`^${P}/reference-sets/(rs_\\w+)/images$`).exec(path)) && method === 'POST') {
      const set = sets.get(m[1]); if (!set) return fail(res, 404, 'not_found', 'Reference set not found')
      const boundary = /boundary=(.+)$/.exec(req.headers['content-type'] ?? '')?.[1]
      const parts = parseMultipart(await readBody(req), boundary)
      const file = parts.file, slot = parts.slot
      if (!file || !SLOT_ORDER.includes(slot)) return fail(res, 422, 'validation_error', 'file and a valid slot are required')
      if (!['image/png', 'image/jpeg', 'image/webp'].includes(file.type)) return fail(res, 415, 'unsupported_media_type', 'Unsupported image type')
      if (file.data.length > 25 * 1024 * 1024) return fail(res, 413, 'payload_too_large', 'File too large')
      if (slot === 'detail' && set.images.filter((i) => i.slot === 'detail').length >= 8) return fail(res, 422, 'validation_error', 'Too many detail images')
      const image = addReferenceImage(set, { bytes: file.data, mime: file.type, filename: file.filename, slot, label: parts.label ?? null, makePrimary: parts.make_primary === 'true' })
      markCreated(res, image.id)
      return send(res, 201, { image, set: setView(set) })
    }
    if ((m = new RegExp(`^${P}/reference-sets/(rs_\\w+)/images/(img_\\w+)(/file|/thumbnail)?$`).exec(path))) {
      const set = sets.get(m[1]); const image = set?.images.find((i) => i.id === m[2])
      if (!set || !image) return fail(res, 404, 'not_found', 'Image not found')
      if (m[3]) { const f = files.get(image.id); return send(res, 200, f.bytes, { 'Content-Type': f.mime, 'Cache-Control': 'private, max-age=3600' }) }
      if (method === 'DELETE') { set.images = set.images.filter((i) => i !== image); files.delete(image.id); ensurePrimary(set); touch(set); return send(res, 200, setView(set)) }
      if (method === 'PATCH') {
        const body = JSON.parse((await readBody(req)).toString())
        if (body.slot && body.slot !== 'detail' && set.images.some((i) => i.slot === body.slot && i !== image)) return fail(res, 409, 'conflict', 'Slot occupied')
        if (body.slot) image.slot = body.slot
        if ('label' in body) image.label = body.label
        touch(set); return send(res, 200, setView(set))
      }
    }

    // Blender runtime (Auto Mesh Tuner)
    if (path === `${P}/blender/runtime` && method === 'GET') return send(res, 200, runtimeStatus())
    if (path === `${P}/blender/runtime/detect` && method === 'POST') {
      blender.detecting = true
      broadcast('blender.updated', runtimeStatus())
      await new Promise((resolve) => setTimeout(resolve, 700))
      blender.detecting = false
      blender.lastScanAt = now()
      blender.scanMs = 300 + Math.random() * 200
      const status = runtimeStatus()
      broadcast('blender.updated', status)
      return send(res, 200, status)
    }
    if (path === `${P}/blender/runtime/selection` && method === 'PUT') {
      const body = JSON.parse((await readBody(req)).toString() || '{}')
      if (!['auto', 'system', 'embedded'].includes(body.mode)) return fail(res, 422, 'validation_error', 'mode must be auto, system or embedded')
      if (body.executable) {
        const install = blender.installs.find((item) => item.executable.toLowerCase() === String(body.executable).toLowerCase())
        if (!install) return fail(res, 404, 'blender_not_found', `No Blender executable was found at ${body.executable}`)
        if (!install.compatible) return fail(res, 422, 'blender_incompatible', install.incompatible_reason)
      }
      blender.mode = body.mode
      blender.pinned = body.executable ?? null
      const status = runtimeStatus()
      broadcast('blender.updated', status)
      return send(res, 200, status)
    }
    if (path === `${P}/blender/runtime/release` && method === 'POST') {
      await new Promise((resolve) => setTimeout(resolve, 400))
      blender.release = RELEASE
      return send(res, 200, RELEASE)
    }
    if (path === `${P}/blender/runtime/download` && method === 'POST') {
      const body = JSON.parse((await readBody(req)).toString() || '{}')
      if (body.confirm !== true) return fail(res, 422, 'validation_error', 'Downloading Blender requires explicit confirmation (confirm: true)')
      if (!ALLOW_DOWNLOAD) return fail(res, 403, 'blender_download_disabled', 'Blender downloads are disabled on this server')
      if (blender.download && ACTIVE_DOWNLOAD.includes(blender.download.state)) return fail(res, 409, 'blender_download_in_progress', 'A Blender download is already in progress')
      if (body.version && body.version !== RELEASE.version) return fail(res, 502, 'blender_release_unresolved', `Blender ${body.version} is not available`)
      startBlenderDownload()
      const status = runtimeStatus()
      broadcast('blender.updated', status)
      return send(res, 202, status)
    }
    if (path === `${P}/blender/runtime/download` && method === 'DELETE') {
      if (blender.download && ACTIVE_DOWNLOAD.includes(blender.download.state)) cancelBlenderDownload()
      const status = runtimeStatus()
      broadcast('blender.updated', status)
      return send(res, 202, status)
    }

    // Auto Tune runs
    if ((m = new RegExp(`^${P}/jobs/(job_\\w+)/tunings$`).exec(path))) {
      const job = jobs.get(m[1]); if (!job) return fail(res, 404, 'not_found', 'Job not found')
      if (method === 'GET') {
        const items = [...tunings.values()].filter((run) => run.job_id === job.id).sort((a, b) => b.created_at.localeCompare(a.created_at))
        return send(res, 200, { items: items.map(publicRun) })
      }
      if (method === 'POST') {
        const body = JSON.parse((await readBody(req)).toString() || '{}')
        if (body.preset !== undefined && !PRESETS[body.preset]) return fail(res, 422, 'validation_error', `Unknown preset ${body.preset}`)
        if (job.status !== 'completed') return fail(res, 409, 'conflict', 'Only completed jobs can be tuned')
        const runtime = runtimeStatus()
        if (!runtime.selected) return fail(res, 503, 'blender_unavailable', runtime.message ?? 'No compatible Blender runtime is selected.')
        if ([...tunings.values()].filter((run) => ['queued', 'running'].includes(run.status)).length >= MAX_ACTIVE_TUNINGS) {
          return fail(res, 429, 'queue_full', 'The Auto Tune queue is full')
        }
        const run = createTuning(job, body)
        markCreated(res, run.id)
        broadcast('tuning.updated', publicRun(run))
        return send(res, 202, publicRun(run))
      }
    }
    if ((m = new RegExp(`^${P}/tunings/(tn_\\w+)(/cancel|/report|/model\\.glb|/export/glb|/log|/previews/([a-z0-9_]+\\.png))?$`).exec(path))) {
      const run = tunings.get(m[1]); if (!run) return fail(res, 404, 'not_found', 'Tuning run not found')
      const sub = m[2]
      const active = ['queued', 'running'].includes(run.status)
      if (!sub && method === 'GET') return send(res, 200, publicRun(run))
      if (!sub && method === 'DELETE') {
        if (active) return fail(res, 409, 'conflict', 'Cancel the tuning run before deleting it')
        tunings.delete(run.id); return send(res, 204)
      }
      if (sub === '/cancel' && method === 'POST') {
        if (!active) return fail(res, 409, 'conflict', 'Tuning run already finished')
        if (run.status === 'queued') {
          run.status = 'cancelled'; run.finished_at = now(); run.progress = { ...run.progress, message: 'Cancelled', updated_at: now() }
          broadcast('tuning.updated', publicRun(run))
        } else run.sim.cancel = true
        return send(res, 202, publicRun(run))
      }
      if (sub === '/log') return send(res, 200, Buffer.from(tuningLog(run)), { 'Content-Type': 'text/plain; charset=utf-8' })
      if (!run.result) return fail(res, 409, 'conflict', 'Tuning run has no result')
      if (sub === '/report') return send(res, 200, tuningReport(run))
      if (sub === '/model.glb') return send(res, 200, TUNED_GLB.buffer, { 'Content-Type': 'model/gltf-binary' })
      if (sub === '/export/glb') return send(res, 200, TUNED_GLB.buffer, { 'Content-Type': 'model/gltf-binary', 'Content-Disposition': `attachment; filename="img3d_${run.job_id}_tuned_${run.id}.glb"` })
      if (m[3]) {
        const preview = PREVIEWS.get(m[3])
        return preview ? send(res, 200, preview.png, { 'Content-Type': 'image/png', 'Cache-Control': 'private, max-age=3600' }) : fail(res, 404, 'not_found', 'Preview not found')
      }
    }

    // Jobs
    if (path === `${P}/jobs` && method === 'POST') {
      const body = await partAware.readJson(req, res, 'CreateJobRequest')
      if (!body) return
      if (typeof body.reference_set_id !== 'string') return fail(res, 422, 'validation_error', 'Request validation failed', [{ loc: ['body', 'reference_set_id'], type: 'missing', msg: 'Field required' }])
      if (scenario.queue_full) return fail(res, 429, 'queue_full', 'The generation queue is full')
      const created = createJob(body)
      if (created.error) return fail(res, created.error.status, created.error.code, created.error.message, created.error.details)
      markCreated(res, created.job.id)
      return send(res, 202, publicJob(created.job))
    }
    if (path === `${P}/jobs` && method === 'GET') {
      const limit = Number(url.searchParams.get('limit') ?? 20)
      return send(res, 200, { items: [...jobs.values()].sort((a, b) => b.created_at.localeCompare(a.created_at)).slice(0, limit).map(publicJob) })
    }
    if ((m = new RegExp(`^${P}/jobs/(job_\\w+)(/cancel|/result|/preview|/model\\.glb|/export/glb)?$`).exec(path))) {
      const job = jobs.get(m[1]); if (!job) return fail(res, 404, 'not_found', 'Job not found')
      const sub = m[2]
      if (!sub && method === 'GET') return send(res, 200, publicJob(job))
      if (!sub && method === 'DELETE') {
        if (['queued', 'running'].includes(job.status)) return fail(res, 409, 'conflict', 'Job is active')
        // Deleting a job cancels and removes its tuning runs (DESIGN.md §6) and its part analyses (part-aware §6.4).
        for (const run of [...tunings.values()].filter((item) => item.job_id === job.id)) tunings.delete(run.id)
        partAware.onJobDeleted(job.id)
        jobs.delete(job.id); return send(res, 204)
      }
      if (sub === '/cancel') {
        if (['completed', 'failed', 'cancelled'].includes(job.status)) return fail(res, 409, 'conflict', 'Job already finished')
        cancelJob(job)
        return send(res, 200, publicJob(job))
      }
      if (sub === '/result') return job.result ? send(res, 200, job.result) : fail(res, 409, 'conflict', 'Job not completed')
      if (sub === '/preview') return job.primaryBytes && job.status !== 'queued' ? send(res, 200, job.primaryBytes.bytes, { 'Content-Type': job.primaryBytes.mime }) : fail(res, 404, 'not_found', 'Preview not ready')
      if (sub === '/model.glb') return job.result ? send(res, 200, GLB.buffer, { 'Content-Type': 'model/gltf-binary' }) : fail(res, 409, 'conflict', 'Job not completed')
      if (sub === '/export/glb') return job.result ? send(res, 200, GLB.buffer, { 'Content-Type': 'model/gltf-binary', 'Content-Disposition': `attachment; filename="img3d_${job.id}.glb"` }) : fail(res, 409, 'conflict', 'Job not completed')
    }
    return fail(res, 404, 'not_found', `No route for ${method} ${path}`)
  } catch (error) {
    console.error(error)
    return fail(res, 500, 'internal_error', 'Internal server error')
  }
})

// ------------------------------------------------------------------------------------------------
// Minimal WebSocket (server → client text frames only)
// ------------------------------------------------------------------------------------------------
const sockets = new Set()
function wsFrame(text, opcode = 0x1) {
  const payload = Buffer.from(text)
  const len = payload.length
  const head = len < 126 ? Buffer.from([0x80 | opcode, len]) : len < 65536 ? Buffer.from([0x80 | opcode, 126, len >> 8, len & 255]) : (() => { const b = Buffer.alloc(10); b[0] = 0x80 | opcode; b[1] = 127; b.writeBigUInt64BE(BigInt(len), 2); return b })()
  return Buffer.concat([head, payload])
}
function broadcast(type, data) {
  const frame = wsFrame(JSON.stringify({ type, ts: now(), data: type === 'job.updated' ? publicJob(data) : data }))
  for (const socket of sockets) socket.write(frame)
}
server.on('upgrade', (req, socket) => {
  const url = new URL(req.url, `http://${req.headers.host}`)
  if (url.pathname !== `${P}/ws`) return socket.destroy()
  const key = req.headers['sec-websocket-key']
  const accept = createHash('sha1').update(`${key}258EAFA5-E914-47DA-95CA-C5AB0DC85B11`).digest('base64')
  socket.write(`HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Accept: ${accept}\r\n\r\n`)
  if (!authorized(req, url)) { socket.write(Buffer.concat([Buffer.from([0x88, 2]), Buffer.from([4401 >> 8, 4401 & 255])])); return socket.end() }
  sockets.add(socket)
  socket.write(wsFrame(JSON.stringify({ type: 'hello', ts: now(), data: { system: systemStatus() } })))
  socket.on('data', (buf) => { if ((buf[0] & 0x0f) === 0x8) socket.end() })
  socket.on('close', () => sockets.delete(socket))
  socket.on('error', () => sockets.delete(socket))
})
setInterval(() => { for (const s of sockets) s.write(wsFrame(JSON.stringify({ type: 'ping', ts: now(), data: {} }))) }, 20000)

server.listen(PORT, '127.0.0.1', () =>
  console.log(
    `[mock-backend] listening on http://127.0.0.1:${PORT} (auth=${AUTH}, blender=${BLENDER_SCENARIO}, download=${ALLOW_DOWNLOAD ? `${DOWNLOAD_SECONDS}s/${DOWNLOAD_FAIL}` : 'disabled'}, tuning=${TUNING_SCENARIO}/${TUNING_SECONDS}s, seeded jobs=${SEED_JOBS}, features=${scenario.features}, parts=${scenario.parts}, strict=${scenario.strict ? 'on' : 'off'}, t2i cold=${scenario.t2i_cold_seconds}s, tuner flag=${scenario.tuner_enabled})`,
  ),
)
