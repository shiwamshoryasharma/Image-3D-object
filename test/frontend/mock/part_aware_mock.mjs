/**
 * TEST DOUBLE — the part-aware routes of the frontend mock backend (UI_DESIGN.md §10.4), with the DESIGN.md §6.1–§6.6
 * shapes, statuses and error envelopes. Used ONLY by test/frontend/mock_backend.mjs to exercise the UI without a GPU;
 * never part of the application. No dependencies (Node >= 22).
 *
 *   const partAware = createPartAwareMock(ctx)
 *   if (await partAware.handle(req, res, url)) return      // router, after auth, before the legacy routes
 *   setInterval(partAware.tick, 250)
 *
 * ctx (from mock_backend.mjs) = {send, fail, readBody, broadcast, now, hex, encodePng, createJob(body), cancelJob(job),
 *   jobs, sets, files, scenario, createReferenceSet(name), addReferenceImage(set, image), setView(set),
 *   markCreated(res, id), headerAuthorized(req), baseGlb}
 *
 * Simulated (test data only, every payload derived from test/frontend/fixtures/):
 * - Discovery: `/features` = `features/<scenario>.json` with live model states, run queue, catalog ETag and the tuner
 *   flag; `/models` = the scenario's models + the hidden entries of `models.json`; `POST /models/{id}/load` → `loading`
 *   for `t2i_cold_seconds` → `loaded`. `features.updated` is broadcast whenever the view changes (≤ 1 per tick).
 * - Runs (`ig_`, `sg_`, `pa_`): one shared queue (8), one run at a time, the DESIGN §4.4.4 / §4.5.5 / §4.6.4 step
 *   tables with their weights and the §4.3 percent formula; `<kind>.updated` on every change. Results and files are
 *   the fixtures with ids rewritten, plus the procedural GLB/PNG/WebP data of glb_parts.mjs.
 * - Workflows (`wf_`): children chained per DESIGN §4.7.3 (ig → import → sg → job → pa → export) with `node_id`s from
 *   the graph, step artifacts, outputs, `step_failed` errors; `/workflows/validate` with the §4.7.2 issue codes;
 *   `/workflows/catalog` with an ETag over the canonical JSON and a 304 with an empty body.
 * - Scenarios (`ctx.scenario`, switched at runtime by `POST /api/__test/scenario`): `features` full | no-t2i | no-seg |
 *   disabled | legacy404, `parts` ok | partial | fail | slow | no-attribute, `t2i_cold_seconds`, `tuner_enabled`,
 *   `queue_full`, `strict` (request keys checked against fixtures/request_keys.json → 422 `extra_forbidden`).
 */
import { createHash } from 'node:crypto'
import { findUnknownKeys, readFixture } from '../unit/_fixtures.mjs'
import { buildPartsGlb, candidatePng, candidateThumbWebp, crc32, gray16Png, maskPng, overlayPng, previewPng, thumbWebp } from './glb_parts.mjs'

const P = '/api/v1'
export const FEATURE_SCENARIOS = ['full', 'no-t2i', 'no-seg', 'disabled', 'legacy404']
export const PARTS_SCENARIOS = ['ok', 'partial', 'fail', 'slow', 'no-attribute']
const FEATURES = Object.fromEntries(FEATURE_SCENARIOS.filter((name) => name !== 'legacy404').map((name) => [name, readFixture(`features/${name}.json`)]))
const MODELS = readFixture('models.json').items
const CATALOG = readFixture('catalog.json')
const IG_FIXTURE = readFixture('image_generation_completed.json')
const SG_FIXTURE = readFixture('segmentation_completed.json')
const PA_FIXTURE = readFixture('part_analysis_completed.json')
const MANIFEST_FIXTURE = readFixture('manifest_parts.json')
const FIXTURE_IDS = { pa: 'pa_0123456789ab', job: 'job_0123456789ab', sg: 'sg_0123456789ab', rs: 'rs_0123456789ab', img: 'img_0123456789ab' }

const RUN_QUEUE_MAX = 8
const STATUSES = ['queued', 'running', 'completed', 'failed', 'cancelled']
const ACTIVE = ['queued', 'running']
const SLOTS = ['front', 'back', 'left', 'right', 'face_front', 'face_side', 'detail']
const SEG_PROFILES = ['auto', 'baseline', 'anime', 'realistic']
const PREVIEW_VIEWS = ['front', 'back', 'left', 'right']
const USABLE_STATES = ['installed', 'loading', 'loaded']
const LEGACY_404 = new RegExp(`^${P}/(features|models|image-generations|segmentations|part-analyses|workflows)(/|$)|^${P}/jobs/[^/]+/part-analyses(/|$)`)
const SOURCE_TYPES = ['input_image', 'prompt', 'job_result']
const TICK_S = 0.25

/** Step tables: [id, label, weight, ticks] (ticks of 250 ms; DESIGN §4.4.4, §4.5.5, §4.6.4). */
const IG_STEPS = [['load_model', 'Loading text-to-image model', 15, 1], ['generate', 'Generating images', 70, 2], ['check', 'Checking 3D readiness', 10, 1], ['save', 'Saving candidates', 5, 1]]
const SG_WEIGHTS = { prepare: [5, 1], frame: [5, 1], parse: [25, 3], detect: [25, 3], refine: [30, 3], fuse: [10, 1] }
const SG_STEPS = SG_FIXTURE.steps.map((step) => [step.id, step.label, ...SG_WEIGHTS[step.id]])
const PA_STEPS = [
  ['prepare', 'Preparing inputs', 4, 2], ['segmentation_2d', 'Segmenting image', 12, 3], ['camera_fit', 'Aligning camera', 8, 2],
  ['label_lift', 'Lifting labels to 3D', 22, 4], ['assign_labels', 'Assigning labels', 8, 2], ['partition', 'Separating parts', 16, 3],
  ['export', 'Exporting parts', 22, 3], ['previews', 'Rendering previews', 8, 2],
]
const SEMANTIC_ONLY_STEPS = ['segmentation_2d', 'camera_fit', 'label_lift', 'assign_labels']
const SLOW_PA_TICKS = 120 // `--parts-scenario slow`: 30 s

/** Workflow steps (DESIGN §4.7.3): [id, node type, weight, failure label]. */
const WORKFLOW_STEPS = [
  ['text_to_image', 'prompt', 6, 'Generating the image'], ['import_image', 'input_image', 1, 'Importing the image'],
  ['segmentation', 'segmentation', 4, 'Segmenting the image'], ['reconstruction', 'image_to_3d', 70, 'Generating the 3D model'],
  ['part_extraction', 'part_extraction', 17, 'Separating parts'], ['export', 'export', 2, 'Exporting'],
]
const STEP_MESSAGES = { text_to_image: 'Generating the image', import_image: 'Importing the image', segmentation: 'Segmenting the image', reconstruction: 'Generating the 3D model', part_extraction: 'Separating parts', export: 'Exporting' }

/** DESIGN §4.4.3 templates (exact texts). */
const CHARACTER_TEMPLATE = 'A full-body 3D character reference image of {subject}. Exactly one character, standing upright in a neutral A-pose: arms straight and angled about 30 degrees away from the torso, hands open and relaxed, feet shoulder-width apart and flat on the ground. Front view, facing the camera directly, eye-level camera, telephoto lens with minimal perspective distortion. The whole body is visible from the top of the head to the soles of the shoes, centered in the frame with empty space on all sides. Neutral expression, eyes open, mouth closed, hair not covering the face. Plain seamless {background} studio background, no floor line, no props, no text. Soft, even, diffuse lighting with no cast shadows and no strong highlights. Sharp focus everywhere. {style_clause} {part_hint_clause}'
const OBJECT_TEMPLATE = 'A product-style 3D reference image of {subject}. A single complete object, fully visible and centered in the frame with empty space on all sides, three-quarter front view from slightly above eye level. Plain seamless {background} studio background, no other objects, no text. Soft, even, diffuse lighting with no cast shadows. Sharp focus, true-to-life materials. {style_clause} {part_hint_clause}'
const STYLE_CLAUSES = { realistic: 'Photorealistic, physically accurate materials and colors.', stylized: 'Stylized 3D game-asset render, clean readable shapes, hand-painted textures.', anime: 'Anime-style character, clean cel shading, crisp line art.' }
const PART_HINT_CLAUSE = 'Hair, clothing layers and accessories are clearly distinct from each other and from the skin; both eyes clearly visible.'
/** A tiny stand-in for `taxonomy.match_terms` (DESIGN §4.4.3 "Expected terms"). */
const TERM_ALIASES = {
  sword: 'object.held', katana: 'object.held', staff: 'object.held', shield: 'object.held', spear: 'object.held', bow: 'object.held',
  cape: 'clothing.outer', cloak: 'clothing.outer', coat: 'clothing.outer', jacket: 'clothing.outer', hat: 'accessory.headwear',
  helmet: 'accessory.headwear', crown: 'accessory.headwear', glasses: 'accessory.eyewear', necklace: 'accessory.jewelry',
  bag: 'accessory.bag', belt: 'accessory.belt', tail: 'appendage.tail', wings: 'appendage.wings',
}

const isObject = (value) => value !== null && typeof value === 'object' && !Array.isArray(value)
const sha256 = (data) => createHash('sha256').update(data).digest('hex')
const issue = (loc, type, msg, extra = {}) => ({ loc, type, msg, ...extra })
const round1 = (value) => Math.round(value * 10) / 10

/** Canonical JSON (sorted keys) for the catalog ETag (DESIGN §6.5). */
export function canonical(value) {
  if (Array.isArray(value)) return `[${value.map(canonical).join(',')}]`
  if (isObject(value)) return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonical(value[key])}`).join(',')}}`
  return JSON.stringify(value)
}

/** Replace fixture ids in a JSON-able value (`{pa: 'pa_…'}` etc.). */
function rewriteIds(value, ids) {
  let text = JSON.stringify(value)
  for (const [kind, id] of Object.entries(ids)) if (id) text = text.split(FIXTURE_IDS[kind]).join(id)
  return JSON.parse(text)
}

/** Minimal ZIP (method 0 = stored) for `/part-analyses/{id}/export/zip`. */
function zipStore(entries) {
  const locals = []
  const centrals = []
  let offset = 0
  for (const { name, data } of entries) {
    const nameBytes = Buffer.from(name, 'utf8')
    const crc = crc32(data)
    const local = Buffer.alloc(30)
    local.writeUInt32LE(0x04034b50, 0)
    local.writeUInt16LE(20, 4)
    local.writeUInt32LE(crc, 14)
    local.writeUInt32LE(data.length, 18)
    local.writeUInt32LE(data.length, 22)
    local.writeUInt16LE(nameBytes.length, 26)
    const central = Buffer.alloc(46)
    central.writeUInt32LE(0x02014b50, 0)
    central.writeUInt16LE(20, 4)
    central.writeUInt16LE(20, 6)
    central.writeUInt32LE(crc, 16)
    central.writeUInt32LE(data.length, 20)
    central.writeUInt32LE(data.length, 24)
    central.writeUInt16LE(nameBytes.length, 28)
    central.writeUInt32LE(offset, 42)
    locals.push(local, nameBytes, data)
    centrals.push(central, nameBytes)
    offset += local.length + nameBytes.length + data.length
  }
  const directory = Buffer.concat(centrals)
  const end = Buffer.alloc(22)
  end.writeUInt32LE(0x06054b50, 0)
  end.writeUInt16LE(entries.length, 8)
  end.writeUInt16LE(entries.length, 10)
  end.writeUInt32LE(directory.length, 12)
  end.writeUInt32LE(offset, 16)
  return Buffer.concat([...locals, directory, end])
}

export function createPartAwareMock(ctx) {
  const { send, fail, now, hex, scenario } = ctx
  const runs = new Map()
  const workflows = new Map()
  const modelStates = new Map() // model id → {state, loaded_device, last_used_at, until}
  let featuresSignature = null

  // ----------------------------------------------------------------------------------------------
  // Discovery: features, models, catalog
  // ----------------------------------------------------------------------------------------------
  const scenarioFeatures = () => FEATURES[scenario.features] ?? FEATURES.full
  const usable = (model) => USABLE_STATES.includes(model.state)

  function applyModelState(model) {
    const live = modelStates.get(model.id)
    if (live) Object.assign(model, { state: live.state, loaded_device: live.loaded_device, last_used_at: live.last_used_at })
    return model
  }

  /** `ModelOut` for an id: the scenario's `/features.models` entry, else a hidden entry of models.json. */
  function modelView(id) {
    const base = scenarioFeatures().models.find((model) => model.id === id) ?? MODELS.find((model) => model.id === id && model.hidden_by_default)
    return base ? applyModelState(structuredClone(base)) : null
  }

  const catalogCache = new Map()
  function catalogView() {
    if (catalogCache.has(scenario.features)) return catalogCache.get(scenario.features)
    const catalog = structuredClone(CATALOG)
    const nodes = new Map(scenarioFeatures().nodes.map((node) => [node.type, node]))
    for (const def of catalog.node_types) {
      const node = nodes.get(def.type)
      if (node) Object.assign(def, { available: node.available, locked: node.locked, code: node.code, reason: node.reason, hidden_by_default: node.hidden_by_default })
    }
    delete catalog.etag
    const etag = `"sha256-${sha256(canonical(catalog))}"`
    const view = { schema: catalog.schema, etag, ...catalog }
    catalogCache.set(scenario.features, view)
    return view
  }

  function queueStats() {
    const list = [...runs.values()]
    const running = list.find((run) => run.status === 'running')
    return { pending: list.filter((run) => run.status === 'queued').length, running_id: running?.id ?? null, running_kind: running?.kind ?? null, max_size: RUN_QUEUE_MAX }
  }

  /** `GET /features` and the `features.updated` payload. */
  function featuresView() {
    const features = structuredClone(scenarioFeatures())
    features.models.forEach(applyModelState)
    const prompt = features.pipeline.prompt_to_3d
    const defaultModel = features.models.find((model) => model.id === prompt.default_model_id)
    if (defaultModel && modelStates.has(defaultModel.id)) prompt.default_model_state = defaultModel.state
    features.queues.runs = queueStats()
    features.workflow.catalog_etag = catalogView().etag
    if (scenario.tuner_enabled) {
      features.flags.mesh_tuning = true
      features.mesh_tuning = { enabled: true, reason: null }
    }
    return features
  }

  function broadcastFeaturesIfChanged(force = false) {
    if (scenario.features === 'legacy404') {
      featuresSignature = null
      return
    }
    const view = featuresView()
    const signature = JSON.stringify(view)
    if (!force && signature === featuresSignature) return
    featuresSignature = signature
    ctx.broadcast('features.updated', view)
  }

  function setModelState(id, state) {
    const live = modelStates.get(id) ?? { state, loaded_device: null, last_used_at: null, until: null }
    live.state = state
    live.loaded_device = state === 'loaded' ? 'cpu' : null
    if (state === 'loaded') live.last_used_at = now()
    if (state !== 'loading') live.until = null
    modelStates.set(id, live)
  }

  const t2iModelId = () => scenarioFeatures().pipeline.prompt_to_3d.default_model_id

  // ----------------------------------------------------------------------------------------------
  // Errors and request parsing
  // ----------------------------------------------------------------------------------------------
  const validationFail = (res, details) => fail(res, 422, 'validation_error', 'Request validation failed', details)

  /** Feature flag check (DESIGN §6.0, §8.1): in the `disabled` scenario the master switch is off. */
  function featureDisabled(res) {
    if (scenario.features !== 'disabled') return false
    fail(res, 503, 'feature_disabled', 'The part-aware layer is disabled (IMG3D_PART_AWARE_ENABLED=false)', { feature: 'part_aware', flag: 'IMG3D_PART_AWARE_ENABLED' })
    return true
  }

  /** 503 `capability_unavailable` with one `reasons` entry per unusable model of the capability (DESIGN §6.8). */
  function capabilityFail(res, capability) {
    const kinds = { prompt_to_3d: ['text_to_image'], segmentation: ['segmentation'] }[capability]
    const pipeline = featuresView().pipeline[capability]
    const reasons = featuresView().models.filter((model) => kinds.includes(model.kind) && !usable(model)).map((model) => ({ model_id: model.id, state: model.state, reason_code: model.reason_code, reason: model.reason }))
    return fail(res, 503, 'capability_unavailable', pipeline.reason ?? `${capability} is unavailable`, { capability, reasons })
  }

  function queueFull(res) {
    if (!scenario.queue_full && [...runs.values()].filter((run) => run.status === 'queued').length < RUN_QUEUE_MAX) return false
    fail(res, 429, 'queue_full', 'The run queue is full')
    return true
  }

  /**
   * Parse a JSON request body like the backend's `RequestModel` (`extra="forbid"`): 422 for a missing or malformed
   * body and, in strict mode, `extra_forbidden` for every key outside fixtures/request_keys.json (recursively, §2.6).
   * Returns the body, or `null` once a 422 was sent.
   */
  async function readJson(req, res, rootName, { optional = false } = {}) {
    const raw = (await ctx.readBody(req)).toString('utf8')
    if (!raw.trim()) {
      if (optional) return {}
      validationFail(res, [issue(['body'], 'missing', 'Field required')])
      return null
    }
    let body
    try {
      body = JSON.parse(raw)
    } catch (error) {
      validationFail(res, [issue(['body'], 'json_invalid', `JSON decode error: ${error.message}`)])
      return null
    }
    if (!isObject(body)) {
      validationFail(res, [issue(['body'], 'model_type', 'Input should be a valid dictionary or object')])
      return null
    }
    if (scenario.strict) {
      // Params of a node type without a key set (unknown, locked or unsupported types) are the compiler's business
      // (`unknown_node_type` / `node_locked` issues of an `invalid_workflow`), not a schema 422.
      const problems = findUnknownKeys(body, rootName).filter((problem) => !(problem.type === 'unknown_object' && problem.name.startsWith('node.')))
      if (problems.length) {
        validationFail(res, problems.map((problem) => {
          if (problem.type === 'extra_forbidden') return issue(problem.loc, 'extra_forbidden', 'Extra inputs are not permitted')
          if (problem.type === 'unknown_object') return issue(problem.loc, 'union_tag_invalid', `Unknown object ${problem.name}`)
          return issue(problem.loc, 'model_type', `Unexpected object at ${problem.path} (not in fixtures/request_keys.json)`)
        }))
        return null
      }
    }
    return body
  }

  /** J routes accept the Bearer header only (DESIGN §6.0); F routes also take `?token=` (checked by mock_backend). */
  function jAuth(req, res) {
    if (ctx.headerAuthorized(req)) return true
    fail(res, 401, 'unauthorized', 'Authentication required')
    return false
  }

  function listParams(url, res, filters = []) {
    const text = url.searchParams.get('limit')
    const limit = text === null ? 20 : Number(text)
    const details = []
    if (!Number.isInteger(limit)) details.push(issue(['query', 'limit'], 'int_parsing', 'Input should be a valid integer', { input: text }))
    else if (limit < 1 || limit > 100) details.push(issue(['query', 'limit'], limit < 1 ? 'greater_than_equal' : 'less_than_equal', 'Input should be between 1 and 100', { input: text }))
    const status = url.searchParams.get('status')
    if (status !== null && !STATUSES.includes(status)) details.push(issue(['query', 'status'], 'enum', `Input should be ${STATUSES.join(', ')}`, { input: status }))
    if (details.length) {
      validationFail(res, details)
      return null
    }
    return { limit, status, ...Object.fromEntries(filters.map((name) => [name, url.searchParams.get(name)])) }
  }

  // ----------------------------------------------------------------------------------------------
  // Request validation beyond the key sets (DESIGN §4.4.1, §4.5.1, §4.6.2, §4.7.1)
  // ----------------------------------------------------------------------------------------------
  function checkEnum(out, loc, value, allowed) {
    if (value !== undefined && !allowed.includes(value)) out.push(issue(loc, 'literal_error', `Input should be ${allowed.map((item) => JSON.stringify(item)).join(', ')}`, { input: value }))
  }
  function checkInt(out, loc, value, min, max, { nullable = false } = {}) {
    if (value === undefined || (nullable && value === null)) return
    if (!Number.isInteger(value)) out.push(issue(loc, 'int_type', 'Input should be a valid integer', { input: value }))
    else if (value < min || value > max) out.push(issue(loc, value < min ? 'greater_than_equal' : 'less_than_equal', `Input should be between ${min} and ${max}`, { input: value }))
  }
  function checkBool(out, loc, value) {
    if (value !== undefined && typeof value !== 'boolean') out.push(issue(loc, 'bool_type', 'Input should be a valid boolean', { input: value }))
  }

  function generationDetails(gen, loc, { inWorkflow }) {
    const out = []
    if (!isObject(gen)) return [issue(loc, 'model_type', 'Input should be a valid dictionary or object')]
    const prompt = scenarioFeatures().pipeline.prompt_to_3d
    if (typeof gen.prompt !== 'string') out.push(issue([...loc, 'prompt'], gen.prompt === undefined ? 'missing' : 'string_type', gen.prompt === undefined ? 'Field required' : 'Input should be a valid string'))
    else {
      const text = gen.prompt.trim()
      if (!text) out.push(issue([...loc, 'prompt'], 'string_too_short', 'String should have at least 1 character'))
      else if (text.length > prompt.max_prompt_chars) out.push(issue([...loc, 'prompt'], 'string_too_long', `String should have at most ${prompt.max_prompt_chars} characters`))
      else if (/[\u0000-\u0008\u000b\u000c\u000e-\u001f\u007f]/.test(text)) out.push(issue([...loc, 'prompt'], 'invalid_characters', 'The prompt contains control characters'))
    }
    if (gen.negative_prompt) {
      if (String(gen.negative_prompt).length > 1000) out.push(issue([...loc, 'negative_prompt'], 'string_too_long', 'String should have at most 1000 characters'))
      else if (!prompt.supports_negative_prompt) out.push(issue([...loc, 'negative_prompt'], 'unsupported_by_model', 'The model does not support a negative prompt', { ctx: { allowed: null } }))
    }
    checkEnum(out, [...loc, 'preset'], gen.preset, prompt.presets)
    if (gen.style !== null) checkEnum(out, [...loc, 'style'], gen.style, prompt.styles)
    checkBool(out, [...loc, 'part_hint'], gen.part_hint)
    checkBool(out, [...loc, 'randomize_seed'], gen.randomize_seed)
    checkInt(out, [...loc, 'num_images'], gen.num_images, 1, prompt.max_images)
    if (inWorkflow && gen.num_images !== undefined && gen.num_images !== 1) out.push(issue([...loc, 'num_images'], 'value_error', 'num_images must be 1 inside workflows', { input: gen.num_images }))
    checkInt(out, [...loc, 'seed'], gen.seed, 0, 2147483647)
    if ((gen.width ?? null) !== null || (gen.height ?? null) !== null) {
      if (!prompt.sizes.some(([w, h]) => w === gen.width && h === gen.height)) out.push(issue([...loc, 'width'], 'unsupported_by_model', 'Unsupported size for this model', { ctx: { allowed: prompt.sizes } }))
    }
    if ((gen.steps ?? null) !== null && !(Number.isInteger(gen.steps) && gen.steps >= 1 && gen.steps <= 50)) out.push(issue([...loc, 'steps'], 'unsupported_by_model', 'steps must be within 1..50', { ctx: { allowed: { min: 1, max: 50 } } }))
    if ((gen.guidance ?? null) !== null && gen.guidance !== 0) out.push(issue([...loc, 'guidance'], 'unsupported_by_model', 'guidance must be 0.0 for this model', { ctx: { allowed: { min: 0, max: 0 } } }))
    return out
  }

  function segOptionsDetails(opts, loc) {
    if (opts === undefined || opts === null) return []
    if (!isObject(opts)) return [issue(loc, 'model_type', 'Input should be a valid dictionary or object')]
    const out = []
    const features = scenarioFeatures()
    checkEnum(out, [...loc, 'profile'], opts.profile, SEG_PROFILES)
    checkEnum(out, [...loc, 'subject'], opts.subject, features.pipeline.segmentation.subjects)
    if (Array.isArray(opts.classes)) {
      const known = new Set(features.taxonomy.classes.map((cls) => cls.id))
      opts.classes.forEach((id, index) => { if (!known.has(id)) out.push(issue([...loc, 'classes', index], 'unknown_class', `Unknown class ${id}`, { input: id })) })
    } else if (opts.classes !== undefined && opts.classes !== null) out.push(issue([...loc, 'classes'], 'list_type', 'Input should be a valid list'))
    if (opts.extra_terms !== undefined) {
      if (!Array.isArray(opts.extra_terms)) out.push(issue([...loc, 'extra_terms'], 'list_type', 'Input should be a valid list'))
      else {
        if (opts.extra_terms.length > features.limits.extra_terms_max) out.push(issue([...loc, 'extra_terms'], 'too_long', `List should have at most ${features.limits.extra_terms_max} items`))
        opts.extra_terms.forEach((term, index) => { if (typeof term !== 'string' || !term.trim() || term.length > 40) out.push(issue([...loc, 'extra_terms', index], 'string_too_long', 'Each term is 1..40 characters')) })
      }
    }
    checkBool(out, [...loc, 'use_prompt_terms'], opts.use_prompt_terms)
    checkBool(out, [...loc, 'head_pass'], opts.head_pass)
    if (opts.min_confidence !== undefined && !(typeof opts.min_confidence === 'number' && opts.min_confidence >= 0.05 && opts.min_confidence <= 0.95)) out.push(issue([...loc, 'min_confidence'], 'less_than_equal', 'Input should be between 0.05 and 0.95', { input: opts.min_confidence }))
    return out
  }

  function partOptionsDetails(opts, loc) {
    if (opts === undefined || opts === null) return []
    if (!isObject(opts)) return [issue(loc, 'model_type', 'Input should be a valid dictionary or object')]
    const out = []
    const extraction = scenarioFeatures().pipeline.part_extraction
    if (opts.strategy !== undefined && opts.strategy !== null) checkEnum(out, [...loc, 'strategy'], opts.strategy, extraction.strategies.map((strategy) => strategy.id))
    if (opts.separate !== undefined) {
      const groups = extraction.separable_groups.map((group) => group.id)
      if (!Array.isArray(opts.separate)) out.push(issue([...loc, 'separate'], 'list_type', 'Input should be a valid list'))
      else opts.separate.forEach((id, index) => checkEnum(out, [...loc, 'separate', index], id, groups))
    }
    out.push(...segOptionsDetails(opts.segmentation, [...loc, 'segmentation']))
    checkInt(out, [...loc, 'views'], opts.views, 0, 24)
    checkInt(out, [...loc, 'min_part_faces'], opts.min_part_faces, 0, 100000, { nullable: true })
    checkInt(out, [...loc, 'max_parts'], opts.max_parts, 1, 256)
    checkBool(out, [...loc, 'split_shells'], opts.split_shells)
    if (opts.export !== undefined) {
      if (!isObject(opts.export)) out.push(issue([...loc, 'export'], 'model_type', 'Input should be a valid dictionary or object'))
      else {
        checkBool(out, [...loc, 'export', 'per_part_glbs'], opts.export.per_part_glbs)
        checkBool(out, [...loc, 'export', 'previews'], opts.export.previews)
      }
    }
    return out
  }

  /** Body-level rules of `WorkflowRequest` (DESIGN §4.7.1); graph semantics are `invalid_workflow` issues instead. */
  function workflowDetails(body) {
    const out = []
    if (body.name !== undefined && body.name !== null && (typeof body.name !== 'string' || body.name.length > 120)) out.push(issue(['body', 'name'], 'string_too_long', 'String should have at most 120 characters'))
    if (isObject(body.graph)) {
      for (const key of ['source', 'segmentation', 'reconstruction', 'parts']) if (key in body) out.push(issue(['body', key], 'conflicting_fields', '`graph` cannot be combined with simple-form fields'))
      const graph = body.graph
      if (graph.schema !== 'img3d.workflow_graph') out.push(issue(['body', 'graph', 'schema'], 'literal_error', "Input should be 'img3d.workflow_graph'"))
      if (graph.version !== 1) out.push(issue(['body', 'graph', 'version'], 'literal_error', 'Input should be 1'))
      if (!Array.isArray(graph.nodes)) out.push(issue(['body', 'graph', 'nodes'], 'list_type', 'Input should be a valid list'))
      if (!Array.isArray(graph.edges)) out.push(issue(['body', 'graph', 'edges'], 'list_type', 'Input should be a valid list'))
      return out
    }
    if (body.graph !== undefined && body.graph !== null) return [issue(['body', 'graph'], 'model_type', 'Input should be a valid dictionary or object')]
    if (isObject(body.segmentation) && !isObject(body.parts)) out.push(issue(['body', 'segmentation'], 'conflicting_fields', '`segmentation` requires `parts` (a preview without parts is POST /segmentations)'))
    if (isObject(body.parts) && body.parts.segmentation_id !== undefined && body.parts.segmentation_id !== null) out.push(issue(['body', 'parts', 'segmentation_id'], 'conflicting_fields', 'A workflow links its own segmentation; parts.segmentation_id must be null'))
    const source = body.source
    if (!isObject(source)) out.push(issue(['body', 'source'], 'missing', 'Field required'))
    else if (source.kind === 'prompt') out.push(...generationDetails(source.generation, ['body', 'source', 'generation'], { inWorkflow: true }))
    else if (source.kind === 'image') { if (typeof source.reference_set_id !== 'string') out.push(issue(['body', 'source', 'reference_set_id'], 'missing', 'Field required')) }
    else if (source.kind === 'job') { if (typeof source.job_id !== 'string') out.push(issue(['body', 'source', 'job_id'], 'missing', 'Field required')) }
    else out.push(issue(['body', 'source'], 'union_tag_invalid', "Input tag does not match 'image', 'prompt' or 'job'", { input: source.kind }))
    if (isObject(source) && ['image', 'prompt'].includes(source.kind) && !isObject(body.reconstruction?.settings)) out.push(issue(['body', 'reconstruction'], 'missing', 'reconstruction is required for image and prompt sources'))
    if (isObject(source) && source.kind === 'job' && body.reconstruction !== undefined && body.reconstruction !== null) out.push(issue(['body', 'reconstruction'], 'conflicting_fields', 'reconstruction must be null for a job source'))
    out.push(...segOptionsDetails(body.segmentation, ['body', 'segmentation']))
    out.push(...partOptionsDetails(body.parts, ['body', 'parts']))
    return out
  }

  // ----------------------------------------------------------------------------------------------
  // Normalisation (the effective `options` a run records)
  // ----------------------------------------------------------------------------------------------
  function buildPrompt(prompt, preset, style, partHint) {
    if (preset === 'raw') return prompt
    const background = /\b(white|silver|pale|snow|ivory|light gray|light grey)\b/i.test(prompt) ? 'medium-gray' : 'light-gray'
    const template = preset === 'object' ? OBJECT_TEMPLATE : CHARACTER_TEMPLATE
    return template.replace('{subject}', prompt).replace('{background}', background).replace('{style_clause}', style ? STYLE_CLAUSES[style] : '').replace('{part_hint_clause}', partHint ? PART_HINT_CLAUSE : '').replace(/\s+/g, ' ').trim()
  }

  function normalizeGeneration(gen) {
    const prompt = gen.prompt.trim()
    const preset = gen.preset ?? 'character'
    const style = gen.style ?? null
    const partHint = gen.part_hint ?? true
    const [width, height] = gen.width ? [gen.width, gen.height] : preset === 'character' ? [896, 1152] : [1024, 1024]
    const effective = buildPrompt(prompt, preset, style, partHint)
    const words = prompt.toLowerCase().match(/[a-z]+/g) ?? []
    return {
      prompt, effective_prompt: effective, negative_prompt: gen.negative_prompt || null, model_id: gen.model_id ?? t2iModelId(), preset, style, part_hint: partHint,
      num_images: gen.num_images ?? 1, seed: gen.randomize_seed === false ? gen.seed ?? 0 : Math.floor(Math.random() * 2147483647), width, height,
      steps: gen.steps ?? 9, guidance: gen.guidance ?? 0, expected_terms: [...new Set(words.map((word) => TERM_ALIASES[word]).filter(Boolean))].sort(), final_prompt_sha256: sha256(effective),
    }
  }

  const normalizeSegOptions = (opts) => ({ profile: 'auto', subject: 'auto', classes: null, extra_terms: [], use_prompt_terms: true, head_pass: true, min_confidence: 0.35, ...(opts ?? {}) })

  function normalizePartOptions(opts) {
    const extraction = scenarioFeatures().pipeline.part_extraction
    return {
      strategy: opts.strategy ?? extraction.default_strategy ?? 'components', segmentation_id: opts.segmentation_id ?? null,
      segmentation: opts.segmentation ? normalizeSegOptions(opts.segmentation) : null,
      separate: opts.separate ?? extraction.separable_groups.filter((group) => group.default_selected).map((group) => group.id),
      views: opts.views ?? extraction.default_views, min_part_faces: opts.min_part_faces ?? null, max_parts: opts.max_parts ?? 64, split_shells: opts.split_shells ?? true,
      export: { per_part_glbs: false, previews: true, ...(opts.export ?? {}) },
    }
  }

  /** 503 `capability_unavailable` for a strategy that is not usable (DESIGN §4.6.2); false when it is usable. */
  function strategyUnavailable(res, id) {
    const strategy = scenarioFeatures().pipeline.part_extraction.strategies.find((item) => item.id === id)
    if (strategy?.available) return false
    if (strategy?.requires_segmentation && strategy.code !== 'not_implemented') capabilityFail(res, 'segmentation')
    else fail(res, 503, 'capability_unavailable', strategy?.reason ?? 'Part extraction is unavailable', { capability: 'part_extraction', reasons: [{ model_id: null, state: null, reason_code: strategy?.code ?? 'unsupported', reason: strategy?.reason ?? null }] })
    return true
  }

  // ----------------------------------------------------------------------------------------------
  // Runs (ig_ / sg_ / pa_): one queue, one run at a time (DESIGN §4.3)
  // ----------------------------------------------------------------------------------------------
  const RUN_KINDS = {
    image_generation: { prefix: 'ig', event: 'image_generation.updated' },
    segmentation: { prefix: 'sg', event: 'segmentation.updated' },
    part_analysis: { prefix: 'pa', event: 'part_analysis.updated' },
  }

  /** Public `RunOut` (key order of DESIGN §4.4.6; `job_id` last for part analyses). */
  function publicRun(run) {
    const { sim, ...rest } = run
    void sim
    return rest
  }

  const broadcastRun = (run) => ctx.broadcast(RUN_KINDS[run.kind].event, publicRun(run))

  function newRun(kind, options, plan, { workflowId = null, jobId = null } = {}) {
    const run = {
      id: `${RUN_KINDS[kind].prefix}_${hex()}`, kind, status: 'queued', created_at: now(), started_at: null, finished_at: null, workflow_id: workflowId, options, models: [],
      progress: { percent: 0, step_id: null, step_index: 0, step_count: plan.length, message: 'Queued', updated_at: now() },
      steps: plan.map((step) => ({ id: step.id, label: step.label, status: step.skip ? 'skipped' : 'pending', message: step.skip ?? null, progress: null, duration_s: null, details: {} })),
      warnings: [], result: null, error: null,
      ...(kind === 'part_analysis' ? { job_id: jobId } : {}),
      sim: { plan, index: -1, tick: 0, cancel: false },
    }
    runs.set(run.id, run)
    broadcastRun(run)
    return run
  }

  function percentOf(run) {
    const total = run.sim.plan.reduce((sum, step) => sum + step.weight, 0)
    const done = run.steps.reduce((sum, step, index) => sum + (['done', 'warning', 'skipped'].includes(step.status) ? run.sim.plan[index].weight : step.status === 'running' ? run.sim.plan[index].weight * (step.progress ?? 0) : 0), 0)
    return Math.max(run.progress.percent, Math.min(100, round1((100 * done) / total)))
  }

  function setProgress(run, message) {
    const index = Math.max(0, run.sim.index)
    run.progress = { percent: percentOf(run), step_id: run.sim.plan[index]?.id ?? null, step_index: Math.min(run.sim.plan.length, index + 1), step_count: run.sim.plan.length, message, updated_at: now() }
  }

  /** Move to the next step that is not pre-skipped; returns false when there is none. */
  function enterNextStep(run) {
    let index = run.sim.index + 1
    while (index < run.steps.length && run.steps[index].status === 'skipped') index += 1
    run.sim.index = index
    run.sim.tick = 0
    if (index >= run.steps.length) return false
    const step = run.steps[index]
    const plan = run.sim.plan[index]
    if (plan.enter) plan.enter(run, step)
    if (step.status === 'skipped') return enterNextStep(run)
    step.status = 'running'
    step.progress = 0
    setProgress(run, `${step.label}…`)
    return true
  }

  function finishRun(run, status, error = null) {
    run.status = status
    run.finished_at = now()
    run.error = error
    if (status === 'completed') run.progress = { ...run.progress, percent: 100, step_id: run.sim.plan.at(-1).id, step_index: run.sim.plan.length, message: 'Done', updated_at: now() }
    else run.progress = { ...run.progress, message: status === 'cancelled' ? 'Cancelled' : error?.message ?? 'Failed', updated_at: now() }
  }

  function cancelRun(run) {
    if (run.status === 'queued') {
      finishRun(run, 'cancelled')
      broadcastRun(run)
    } else if (run.status === 'running') run.sim.cancel = true
  }

  /** Build the result (the one plan entry with `finalize`) and mark the run completed. */
  function completeRun(run) {
    run.sim.plan.find((item) => item.finalize)?.finalize(run)
    finishRun(run, 'completed')
  }

  function tickRuns() {
    let running = [...runs.values()].find((run) => run.status === 'running')
    if (!running) {
      running = [...runs.values()].filter((run) => run.status === 'queued').sort((a, b) => a.created_at.localeCompare(b.created_at))[0]
      if (!running) return
      running.status = 'running'
      running.started_at = now()
      if (!enterNextStep(running)) completeRun(running)
      broadcastRun(running)
      return
    }
    const run = running
    const step = run.steps[run.sim.index]
    const plan = run.sim.plan[run.sim.index]
    if (run.sim.cancel) {
      step.status = 'cancelled'
      step.message = 'Cancelled'
      finishRun(run, 'cancelled')
      plan.onCancel?.(run)
      return broadcastRun(run)
    }
    run.sim.tick += 1
    const fraction = Math.min(1, run.sim.tick / plan.ticks)
    step.progress = Math.round(fraction * 100) / 100
    const failure = plan.fail?.(run, fraction)
    if (failure) {
      step.status = 'failed'
      step.message = failure.message
      step.duration_s = run.sim.tick * TICK_S
      finishRun(run, 'failed', failure)
      return broadcastRun(run)
    }
    if (fraction < 1) {
      setProgress(run, `${step.label} — ${Math.round(fraction * 100)}%`)
      return broadcastRun(run)
    }
    const outcome = plan.complete?.(run, step) ?? {}
    step.status = outcome.status ?? 'done'
    step.message = outcome.message ?? null
    step.progress = 1
    step.duration_s = run.sim.tick * TICK_S
    if (!enterNextStep(run)) completeRun(run)
    broadcastRun(run)
  }

  // ---- image generations (DESIGN §4.4) ----
  function createImageGeneration(gen, { workflowId = null } = {}) {
    const options = normalizeGeneration(gen)
    const modelId = options.model_id
    const plan = IG_STEPS.map(([id, label, weight, ticks]) => ({ id, label, weight, ticks }))
    const [load, generate, check, save] = plan
    load.enter = (run, step) => {
      const model = modelView(modelId)
      if (model?.state === 'loaded') {
        step.status = 'skipped'
        step.message = 'Model already in memory'
        step.progress = 1
        step.duration_s = 0
      } else if (scenario.t2i_cold_seconds > 0) {
        load.ticks = Math.max(1, Math.round(scenario.t2i_cold_seconds / TICK_S))
        setModelState(modelId, 'loading')
      }
    }
    load.complete = () => {
      if (modelStates.get(modelId)?.state === 'loading') setModelState(modelId, 'loaded')
      return {}
    }
    load.onCancel = () => { if (modelStates.get(modelId)?.state === 'loading') setModelState(modelId, 'installed') }
    generate.ticks = 2 + options.num_images * 2
    check.complete = (run) => {
      const issues = run.sim.readiness.flatMap((list, index) => list.map((code) => ({ code, index })))
      for (const { code, index } of issues) run.warnings.push({ code, message: `Candidate ${index + 1}: the subject touches the image border.`, step_id: 'check' })
      return issues.length ? { status: 'warning', message: `${issues.length} readiness issue${issues.length === 1 ? '' : 's'}` } : {}
    }
    save.finalize = (run) => {
      const model = modelView(modelId) ?? IG_FIXTURE.models[0]
      run.models = [{ id: modelId, revision: model.revision ?? null, license_id: model.license?.id ?? 'apache-2.0' }]
      run.result = {
        candidates: Array.from({ length: options.num_images }, (_, index) => ({
          index, seed: (options.seed + index) % 2 ** 31, width: options.width, height: options.height,
          url: `${P}/image-generations/${run.id}/candidates/${index}`, thumbnail_url: `${P}/image-generations/${run.id}/candidates/${index}/thumbnail`,
          readiness: { ok: run.sim.readiness[index].length === 0, issues: run.sim.readiness[index], method: 'border_color' }, used_as: [],
        })),
        timings: Object.fromEntries([...run.steps.map((step) => [step.id, step.duration_s ?? 0]), ['total', run.steps.reduce((sum, step) => sum + (step.duration_s ?? 0), 0)]]),
      }
    }
    const run = newRun('image_generation', options, plan, { workflowId })
    // Candidate 2 (index 1) touches the border, like the fixture: exercises the readiness chips and run warnings.
    run.sim.readiness = Array.from({ length: options.num_images }, (_, index) => (index === 1 ? ['t2i_touches_border'] : []))
    run.sim.pngs = new Map()
    return run
  }

  function candidateBytes(run, index) {
    if (!run.sim.pngs.has(index)) run.sim.pngs.set(index, candidatePng(ctx.encodePng, index + run.id.charCodeAt(5)))
    return run.sim.pngs.get(index)
  }

  /** `POST …/candidates/{index}/use` (DESIGN §4.4.7); also the workflow `import_image` step. */
  function useCandidate(run, index, body) {
    if (run.status !== 'completed') return { status: 409, code: 'conflict', message: 'The image generation is not completed', details: { reason: 'not_completed' } }
    const candidate = run.result.candidates.find((item) => item.index === index)
    if (!candidate) return { status: 404, code: 'not_found', message: 'Candidate not found', details: null }
    let set
    if (body.reference_set_id) {
      set = ctx.sets.get(body.reference_set_id)
      if (!set) return { status: 404, code: 'not_found', message: 'Reference set not found', details: null }
    } else set = ctx.createReferenceSet(`Prompt: ${run.options.prompt.slice(0, 60)}`)
    const model = run.models[0]
    const image = ctx.addReferenceImage(set, {
      bytes: candidateBytes(run, index), mime: 'image/png', filename: `${run.id}_${index}.png`, slot: body.slot ?? 'front',
      label: (body.label ?? `Prompt: ${run.options.prompt}`).slice(0, 200), makePrimary: body.make_primary ?? true,
      source: { kind: 't2i', image_generation_id: run.id, candidate_index: index, model_id: model.id, model_revision: model.revision, license_id: model.license_id, seed: candidate.seed },
    })
    candidate.used_as.push({ reference_set_id: set.id, image_id: image.id })
    broadcastRun(run)
    return { image, set: ctx.setView(set) }
  }

  // ---- segmentations (DESIGN §4.5) ----
  function sourceImageInfo(source) {
    if (source.kind === 'job') {
      const job = ctx.jobs.get(source.job_id)
      return { bytes: job?.primaryBytes?.bytes ?? null, alpha: true }
    }
    const set = ctx.sets.get(source.reference_set_id)
    const image = set?.images.find((item) => item.id === source.image_id)
    return { bytes: ctx.files.get(source.image_id)?.bytes ?? null, alpha: !!image?.has_alpha }
  }

  function createSegmentation(request, { workflowId = null } = {}) {
    const options = { source: request.source, options: normalizeSegOptions(request.options) }
    const info = sourceImageInfo(request.source)
    const parser = options.options.profile !== 'baseline'
    const plan = SG_STEPS.map(([id, label, weight, ticks]) => ({ id, label, weight, ticks, ...(id === 'parse' && !parser ? { skip: 'No parser for the baseline profile' } : {}) }))
    const frame = plan.find((step) => step.id === 'frame')
    frame.complete = (run) => {
      if (request.source.kind === 'job') return { message: 'Job source: the image is already the conditioning image' }
      if (info.alpha) return { message: 'Frame recomputed from the alpha channel' }
      run.warnings.push({ ...SG_FIXTURE.warnings[0] })
      return { status: 'warning', message: SG_FIXTURE.steps.find((step) => step.id === 'frame').message }
    }
    plan.at(-1).finalize = (run) => {
      const fixture = rewriteIds(SG_FIXTURE, { sg: run.id, rs: request.source.reference_set_id, img: request.source.image_id })
      const subject = options.options.subject === 'auto' ? fixture.result.subject : options.options.subject
      const profile = options.options.profile === 'auto' ? (subject === 'anime_character' ? 'anime' : subject === 'realistic_human' ? 'realistic' : 'baseline') : options.options.profile
      const exact = request.source.kind === 'job' || info.alpha
      run.models = fixture.models.filter((model) => profile !== 'baseline' || model.id !== 'see_through_l2d')
      run.result = { ...fixture.result, space: exact ? 'conditioning' : 'source', frame: request.source.kind === 'job' ? null : exact ? { ...fixture.result.frame, method: 'recompute', verified: true, residual: 0.7 } : fixture.result.frame, subject, profile }
    }
    const run = newRun('segmentation', options, plan, { workflowId })
    run.sim.sourceBytes = info.bytes
    return run
  }

  // ---- part analyses (DESIGN §4.6) ----
  function createPartAnalysis(job, options, { workflowId = null } = {}) {
    const semantic = options.strategy !== 'components'
    const partsScenario = scenario.parts
    // DESIGN §4.6.3 rule 1: a given sg_ is reused only for a job source of this job, or a real-alpha upload that is
    // this job's primary image (the mock's stand-in for "verified recompute frame + equal pixel hash").
    const sg = options.segmentation_id ? runs.get(options.segmentation_id) : null
    const sgImage = sg?.options.source.kind === 'reference_image' ? ctx.sets.get(sg.options.source.reference_set_id)?.images.find((image) => image.id === sg.options.source.image_id) : null
    const reusable = !!sg && ((sg.options.source.kind === 'job' && sg.options.source.job_id === job.id) || (!!sgImage?.has_alpha && sgImage.id === job.primary_image_id))
    const plan = PA_STEPS.map(([id, label, weight, ticks]) => {
      let skip
      if (!semantic && SEMANTIC_ONLY_STEPS.includes(id)) skip = 'Not used by the components strategy'
      else if (id === 'segmentation_2d' && reusable) skip = `Reused segmentation ${sg.id}`
      else if (id === 'previews' && !options.export.previews) skip = 'Previews turned off'
      return { id, label, weight, ticks, ...(skip ? { skip } : {}) }
    })
    if (partsScenario === 'slow') {
      const active = plan.filter((step) => !step.skip)
      const total = active.reduce((sum, step) => sum + step.ticks, 0)
      for (const step of active) step.ticks = Math.max(1, Math.round((step.ticks * SLOW_PA_TICKS) / total))
    }
    const step2d = plan.find((step) => step.id === 'segmentation_2d')
    step2d.complete = (run) => {
      if (!sg) return {}
      run.warnings.push({ code: 'segmentation_rerun_for_alignment', message: `Segmentation ${sg.id} is not pixel-aligned with this job; the conditioning image was segmented again.`, step_id: 'segmentation_2d' })
      return { status: 'warning', message: 'Segmentation re-run on the conditioning image for alignment' }
    }
    plan.find((step) => step.id === 'partition').fail = (run, fraction) => (partsScenario === 'fail' && fraction >= 0.5 ? { code: 'partition_failed', message: 'The reassembly check failed: positions differ on 3 vertices of part:hair. (simulated by the mock backend)', details: null } : null)
    plan.find((step) => step.id === 'export').finalize = (run) => finalizePartAnalysis(run, { semantic, partsScenario })
    return newRun('part_analysis', options, plan, { workflowId, jobId: job.id })
  }

  function partFileUrls(run, part) {
    const base = `${P}/part-analyses/${run.id}`
    const hasMesh = part.kind === 'base' || part.kind === 'physical'
    return {
      model_url: hasMesh ? `${base}/parts/${part.id}/model.glb` : null, download_url: hasMesh ? `${base}/parts/${part.id}/export/glb` : null,
      mask_url: part.files.mask ? `${base}/masks/${part.region.value}` : null, thumbnail_url: part.files.thumbnail ? `${base}/thumbs/${part.files.thumbnail.split('/').pop()}` : null,
    }
  }

  /** Manifest for the `components` strategy, derived from the components GLB and the fixture's records. */
  function componentsManifest(run, glb) {
    const manifest = rewriteIds(MANIFEST_FIXTURE, { pa: run.id, job: run.job_id })
    const template = manifest.parts.find((part) => part.kind === 'physical')
    const baseRecord = manifest.parts.find((part) => part.kind === 'base')
    const stats = (node) => ({ ...template.stats, faces: node.faces, vertices: node.vertices, bbox_min: node.bboxMin, bbox_max: node.bboxMax, centroid: node.centroid })
    manifest.strategy = { ...manifest.strategy, id: 'components' }
    manifest.label_table = [{ value: 0, category: 'unknown' }, { value: 1, category: 'body' }]
    manifest.meshes = glb.nodes.map((node, index) => ({ index, part_id: node.id, node_name: node.name, faces: node.faces, vertices: node.vertices, unique_vertices: node.vertices, vertex_map: `vmap_${node.id}` }))
    manifest.parts = glb.nodes.map((node, index) => (node.kind === 'base'
      ? { ...baseRecord, region: { ...baseRecord.region, face_count: node.faces }, stats: stats(node) }
      : {
          ...template, id: node.id, category: 'unknown', group: 'other', label: `Component ${index}`, side: null, image_side: null, attach_to: null, host: null, symmetric_with: null,
          node_name: node.name, mesh_index: index, region: { ...template.region, value: 0, face_count: node.faces }, files: { glb: null, mask: null, thumbnail: `thumbs/${node.id}.webp` },
          stats: stats(node), geometry: { ...template.geometry, source: 'components', floaters: [] }, confidence: null, expected: false,
        }))
    manifest.relations = []
    manifest.seams = []
    manifest.expectations = { ...manifest.expectations, expected_classes: [], missing: [] }
    manifest.summary = { part_count: 4, physical_count: 4, deferred_count: 0, region_count: 0, unlabelled_face_ratio: 0, partner_filled_ratio: 0, cut_partner_consistency: null, camera_fit_iou: null }
    manifest.warnings = []
    return manifest
  }

  function finalizePartAnalysis(run, { semantic, partsScenario }) {
    const glb = buildPartsGlb({ analysisId: run.id, jobId: run.job_id, featureIds: partsScenario !== 'no-attribute', layout: semantic ? 'semantic' : 'components' })
    const manifest = semantic ? rewriteIds(MANIFEST_FIXTURE, { pa: run.id, job: run.job_id }) : componentsManifest(run, glb)
    manifest.created_at = now()
    const fixture = rewriteIds(PA_FIXTURE, { pa: run.id, job: run.job_id })
    const base = `${P}/part-analyses/${run.id}`
    const parts = manifest.parts.map((part) => ({
      id: part.id, label: part.label, category: part.category, group: part.group, kind: part.kind, node_name: part.node_name, region_value: part.region.value,
      faces: part.kind === 'base' ? glb.nodes.find((node) => node.kind === 'base').faces : part.region.face_count, confidence: part.confidence, side: part.side,
      ...partFileUrls(run, part), refinement: { status: part.refinement.status, locked: part.refinement.locked, eligible: part.refinement.eligible, needs: part.refinement.needs },
    }))
    const previews = run.options.export.previews ? Object.fromEntries(PREVIEW_VIEWS.map((view) => [view, `${base}/previews/${view}.png`])) : {}
    const result = {
      ...fixture.result, overlay_url: semantic ? fixture.result.overlay_url : null, preview_urls: previews,
      part_count: manifest.summary.part_count, physical_count: manifest.summary.physical_count, deferred_count: manifest.summary.deferred_count, region_count: manifest.summary.region_count,
      camera_fit_iou: manifest.summary.camera_fit_iou, unlabelled_face_ratio: manifest.summary.unlabelled_face_ratio, partner_filled_ratio: manifest.summary.partner_filled_ratio,
      cut_partner_consistency: manifest.summary.cut_partner_consistency, parts,
      stats: { ...fixture.result.stats, vertices: glb.vertices, faces: glb.faces, bbox_min: glb.bboxMin, bbox_max: glb.bboxMax, dimensions: glb.bboxMax.map((value, k) => Math.round((value - glb.bboxMin[k]) * 1e4) / 1e4), file_size_bytes: glb.buffer.length },
      timings: Object.fromEntries([...run.steps.map((step) => [step.id, step.duration_s ?? 0]), ['total', run.steps.reduce((sum, step) => sum + (step.duration_s ?? 0), 0)]]),
    }
    if (partsScenario === 'partial') {
      result.status = 'partial'
      result.camera_fit_iou = semantic ? 0.62 : null
      manifest.summary.camera_fit_iou = result.camera_fit_iou
      const warning = { code: 'camera_fit_low_iou', message: 'The camera fit was rejected (IoU 0.62 < 0.75); labels were lifted from the input view only.', step_id: 'camera_fit' }
      run.warnings.push(warning)
      manifest.warnings.push({ code: warning.code, message: warning.message, part_id: null })
    }
    run.models = semantic ? PA_FIXTURE.models : PA_FIXTURE.models.filter((model) => model.id === 'raster_lifter')
    run.result = result
    run.sim.artifacts = { glb, manifest, semantic, featureIds: partsScenario !== 'no-attribute', partGlbs: new Map() }
  }

  // ----------------------------------------------------------------------------------------------
  // Workflows (DESIGN §4.7)
  // ----------------------------------------------------------------------------------------------
  function resourceExists(resource) {
    if (!resource) return false
    if (resource.kind === 'job') return ctx.jobs.has(resource.id)
    if (resource.kind === 'reference_image') return !!ctx.sets.get(resource.reference_set_id)?.images.some((image) => image.id === resource.id)
    return runs.has(resource.id)
  }

  function publicWorkflow(wf) {
    const { sim, ...rest } = wf
    void sim
    return { ...rest, steps: wf.steps.map((step) => ({ ...step, resource: step.resource ? { ...step.resource, exists: resourceExists(step.resource) } : null })) }
  }

  function broadcastWorkflow(wf, force = false) {
    const signature = JSON.stringify([wf.status, wf.current_step, wf.steps.map((step) => [step.status, step.resource?.id, step.warning_count])])
    const due = Date.now() - wf.sim.sentAt >= 500 && wf.progress.percent !== wf.sim.sentPercent
    if (!force && signature === wf.sim.signature && !due) return
    wf.sim.signature = signature
    wf.sim.sentAt = Date.now()
    wf.sim.sentPercent = wf.progress.percent
    ctx.broadcast('workflow.updated', publicWorkflow(wf))
  }

  /** Graph → simple form (DESIGN §4.7.2 "Compiles to"); only for graphs that passed `validateGraph`. */
  function compileGraph(graph) {
    const byType = Object.fromEntries(graph.nodes.map((node) => [node.type, node]))
    const params = (type) => ({ ...(byType[type]?.params ?? {}) })
    const request = { name: null }
    if (byType.input_image) request.source = { kind: 'image', reference_set_id: byType.input_image.params.reference_set_id, primary_image_id: byType.input_image.params.primary_image_id ?? null }
    else if (byType.prompt) request.source = { kind: 'prompt', generation: params('prompt') }
    else if (byType.job_result) request.source = { kind: 'job', job_id: byType.job_result.params.job_id }
    if (byType.segmentation && byType.part_extraction) request.segmentation = params('segmentation')
    if (byType.image_to_3d) request.reconstruction = { settings: params('image_to_3d') }
    if (byType.part_extraction) {
      request.parts = params('part_extraction')
      if (byType.export && Object.keys(byType.export.params ?? {}).length) request.parts.export = params('export')
    }
    return { request, nodeIds: Object.fromEntries(graph.nodes.map((node) => [node.type, node.id])) }
  }

  /** `POST /workflows/validate` semantics for a graph (DESIGN §4.7.2 issue table). */
  function validateGraph(graph, rawBytes) {
    const issues = []
    const add = (severity, code, message, nodeId = null, port = null, extra = {}) => issues.push({ severity, code, message, node_id: nodeId, port, edge_index: extra.edgeIndex ?? null, details: extra.details ?? null })
    const catalog = catalogView()
    const features = featuresView()
    if (!features.workflow.available) add('error', 'feature_disabled', features.workflow.reason ?? 'Workflows are turned off on the server.')
    if (graph.nodes.length > catalog.limits.max_nodes || graph.edges.length > catalog.limits.max_edges || rawBytes > catalog.limits.graph_max_bytes) {
      add('error', 'graph_too_large', `The workflow is too large (max ${catalog.limits.max_nodes} nodes, ${catalog.limits.max_edges} links, ${catalog.limits.graph_max_bytes} bytes).`)
      return finishReport(issues, null)
    }
    const defs = new Map(catalog.node_types.map((def) => [def.type, def]))
    const nodes = new Map()
    const seenTypes = new Set()
    let structural = false
    for (const node of graph.nodes) {
      const def = defs.get(node.type)
      if (!def) {
        add('error', 'unknown_node_type', `Unknown node type "${node.type}".`, node.id)
        structural = true
        continue
      }
      if (seenTypes.has(node.type)) {
        add('error', 'too_many_instances', `Only one ${def.title} node is supported.`, node.id)
        structural = true
        continue
      }
      seenTypes.add(node.type)
      nodes.set(node.id, { node, def })
    }
    const incoming = new Map()
    graph.edges.forEach((edge, edgeIndex) => {
      const from = nodes.get(edge.from?.node)
      const to = nodes.get(edge.to?.node)
      const output = from?.def.outputs.find((port) => port.id === edge.from.port)
      const input = to?.def.inputs.find((port) => port.id === edge.to.port)
      if (!from || !output) { add('error', 'unknown_port', `Unknown port "${edge.from?.port}".`, edge.from?.node ?? null, edge.from?.port ?? null, { edgeIndex }); structural = true; return }
      if (!to || !input) { add('error', 'unknown_port', `${to?.def.title ?? 'Node'}: unknown port "${edge.to?.port}".`, edge.to?.node ?? null, edge.to?.port ?? null, { edgeIndex }); structural = true; return }
      if (!input.accepts.includes(output.type)) { add('error', 'type_mismatch', `${from.def.title} → ${to.def.title}: ${output.label} cannot connect to ${input.label}.`, to.node.id, input.id, { edgeIndex }); structural = true; return }
      const key = `${to.node.id}.${input.id}`
      if (incoming.has(key) && !input.multiple) { add('error', 'port_already_connected', `${to.def.title}: "${input.id}" already has a connection.`, to.node.id, input.id, { edgeIndex }); structural = true; return }
      incoming.set(key, edge)
    })
    // Kahn: leftover nodes are on a cycle.
    const indegree = new Map([...nodes.keys()].map((id) => [id, 0]))
    for (const edge of incoming.values()) indegree.set(edge.to.node, indegree.get(edge.to.node) + 1)
    const queue = [...indegree].filter(([, degree]) => degree === 0).map(([id]) => id)
    let visited = 0
    while (queue.length) {
      const id = queue.shift()
      visited += 1
      for (const edge of incoming.values()) if (edge.from.node === id) { indegree.set(edge.to.node, indegree.get(edge.to.node) - 1); if (indegree.get(edge.to.node) === 0) queue.push(edge.to.node) }
    }
    const nodeIssues = []
    for (const { node, def } of nodes.values()) {
      if (def.locked) nodeIssues.push(['error', 'node_locked', `${def.title} is locked: ${def.reason ?? 'paused'}.`, node.id])
      else if (!def.available) nodeIssues.push(['error', 'capability_unavailable', `${def.title} is unavailable: ${def.reason}`, node.id, null, { details: { reasons: [{ code: def.code, reason: def.reason }] } }])
    }
    if (visited < nodes.size) add('error', 'cycle', 'The workflow contains a cycle.')
    for (const item of nodeIssues) add(...item)
    if (structural || visited < nodes.size) return finishReport(issues, null)

    const sources = [...nodes.values()].filter(({ def }) => SOURCE_TYPES.includes(def.type))
    if (!sources.length) {
      add('error', 'no_source', 'Connect an Input Image, Prompt or Existing Result node.')
      return finishReport(issues, null)
    }
    const feeder = (nodeId, port) => incoming.get(`${nodeId}.${port}`)?.from.node ?? null
    const byType = (type) => [...nodes.values()].find(({ def }) => def.type === type)?.node
    const seg = byType('segmentation')
    const recon = byType('image_to_3d')
    const differentFeeders = seg && recon && feeder(seg.id, 'images') && feeder(recon.id, 'images') && feeder(seg.id, 'images') !== feeder(recon.id, 'images')
    if (sources.length > 1 || differentFeeders) {
      add('error', 'multiple_sources', 'Use one source for the whole run.')
      return finishReport(issues, null)
    }
    for (const { node, def } of nodes.values()) {
      if (def.locked) continue
      for (const input of def.inputs) if (input.required && !incoming.has(`${node.id}.${input.id}`)) add('error', 'missing_required_input', `${def.title}: connect "${input.label}".`, node.id, input.id)
    }
    const parts = byType('part_extraction')
    if (byType('job_result') && recon) add('error', 'unsupported_topology', 'An Existing Result cannot be reconstructed again.', recon.id)
    if (seg && !(parts && feeder(parts.id, 'masks') === seg.id)) add('error', 'unsupported_topology', 'Segmentation must feed the Masks input of Part Extraction.', seg.id)
    for (const { node, def } of nodes.values()) paramIssues(node, def, add)
    if (!byType('export')) add('warning', 'no_sink', 'No Export node: an implicit export is added.')
    const valid = !issues.some((item) => item.severity === 'error')
    if (!valid) return finishReport(issues, null)
    const { request } = compileGraph(graph)
    const mode = request.source.kind === 'image' && !request.parts && !request.segmentation ? 'job' : 'workflow'
    return finishReport(issues, request, mode)
  }

  /** `invalid_param` / `not_found` per node (pydantic errors relocated to `nodes[i].params.<field>`). */
  function paramIssues(node, def, add) {
    const params = isObject(node.params) ? node.params : {}
    if (def.type === 'input_image') {
      const set = ctx.sets.get(params.reference_set_id)
      if (!set) add('error', 'not_found', 'The reference set does not exist.', node.id, null, { details: { field: 'reference_set_id' } })
      else if (!set.images.length) add('error', 'invalid_param', 'Add at least one reference image.', node.id, null, { details: { field: 'reference_set_id' } })
      else if (params.primary_image_id && !set.images.some((image) => image.id === params.primary_image_id)) add('error', 'not_found', 'The primary image does not exist.', node.id, null, { details: { field: 'primary_image_id' } })
    } else if (def.type === 'job_result') {
      const job = ctx.jobs.get(params.job_id)
      if (!job) add('error', 'not_found', 'The job does not exist.', node.id, null, { details: { field: 'job_id' } })
      else if (job.status !== 'completed') add('error', 'invalid_param', 'The job is not completed.', node.id, null, { details: { field: 'job_id' } })
    } else if (def.type === 'prompt') {
      for (const detail of generationDetails(params, ['params'], { inWorkflow: true })) add('error', 'invalid_param', detail.msg, node.id, null, { details: { field: detail.loc.slice(1).join('.'), type: detail.type } })
    } else if (def.type === 'segmentation') {
      for (const detail of segOptionsDetails(params, ['params'])) add('error', 'invalid_param', detail.msg, node.id, null, { details: { field: detail.loc.slice(1).join('.'), type: detail.type } })
    } else if (def.type === 'part_extraction') {
      for (const detail of partOptionsDetails(params, ['params'])) add('error', 'invalid_param', detail.msg, node.id, null, { details: { field: detail.loc.slice(1).join('.'), type: detail.type } })
    }
  }

  const finishReport = (issues, request, mode = null) => ({ valid: !issues.some((item) => item.severity === 'error'), issues, mode: request ? mode : null, request })

  /** Simple-form validate (no graph): capability, existence and the `mode` rule. */
  function validateSimple(body) {
    const issues = []
    const add = (code, message, details = null) => issues.push({ severity: 'error', code, message, node_id: null, port: null, edge_index: null, details })
    const features = featuresView()
    if (!features.workflow.available) add('feature_disabled', features.workflow.reason ?? 'Workflows are turned off on the server.')
    if (body.source.kind === 'prompt' && !features.pipeline.prompt_to_3d.available) add('capability_unavailable', features.pipeline.prompt_to_3d.reason)
    if (body.segmentation && !features.pipeline.segmentation.available) add('capability_unavailable', features.pipeline.segmentation.reason)
    const existence = sourceExistence(body.source)
    if (existence) add(existence.status === 404 ? 'not_found' : 'invalid_param', existence.message)
    const request = issues.length ? null : normalizedRequest(body)
    return finishReport(issues, request, body.source.kind === 'image' && !body.parts && !body.segmentation ? 'job' : 'workflow')
  }

  const normalizedRequest = (body) => Object.fromEntries([['name', body.name ?? null], ...['source', 'segmentation', 'reconstruction', 'parts'].filter((key) => body[key] !== undefined && body[key] !== null).map((key) => [key, structuredClone(body[key])])])

  /** 404/409/422 checks of a workflow source (DESIGN §4.7.4 "At creation"). */
  function sourceExistence(source) {
    if (source.kind === 'image') {
      const set = ctx.sets.get(source.reference_set_id)
      if (!set) return { status: 404, code: 'not_found', message: 'Reference set not found' }
      if (!set.images.length) return { status: 422, code: 'validation_error', message: 'Reference set is empty' }
      if (source.primary_image_id && !set.images.some((image) => image.id === source.primary_image_id)) return { status: 404, code: 'not_found', message: 'Image not found' }
    }
    if (source.kind === 'job') {
      const job = ctx.jobs.get(source.job_id)
      if (!job) return { status: 404, code: 'not_found', message: 'Job not found' }
      if (job.status !== 'completed') return { status: 409, code: 'conflict', message: 'The job is not completed', details: { reason: 'not_completed' } }
    }
    return null
  }

  function createWorkflow(request, { graph = null, layout = null, nodeIds = {} } = {}) {
    const kind = request.source.kind
    const wf = {
      id: `wf_${hex()}`, name: request.name ?? null, status: 'queued', created_at: now(), started_at: null, finished_at: null, request, graph_stored: !!graph, current_step: null,
      progress: { percent: 0, message: 'Queued', updated_at: now() }, steps: [],
      outputs: { reference_set_id: null, image_id: null, segmentation_id: null, job_id: null, part_analysis_id: null, model_url: null, parts_model_url: null, manifest_url: null },
      error: null, sim: { graph, layout, signature: null, sentAt: 0, sentPercent: null },
    }
    for (const [id, node] of WORKFLOW_STEPS) {
      if (id === 'text_to_image' && kind !== 'prompt') continue
      wf.steps.push({ id, node, node_id: nodeIds[node] ?? null, status: 'pending', message: null, progress: null, warning_count: 0, resource: null, artifacts: [] })
    }
    const step = (id) => wf.steps.find((item) => item.id === id)
    if (kind === 'image') {
      const set = ctx.sets.get(request.source.reference_set_id)
      const imageId = request.source.primary_image_id ?? set.images.find((image) => image.is_primary)?.id ?? set.images[0].id
      Object.assign(step('import_image'), { status: 'done', progress: 100, resource: { kind: 'reference_image', id: imageId, reference_set_id: set.id } })
      Object.assign(wf.outputs, { reference_set_id: set.id, image_id: imageId })
    }
    if (kind === 'job') {
      Object.assign(step('import_image'), { status: 'skipped', message: 'Job source', progress: 100 })
      Object.assign(step('reconstruction'), { status: 'skipped', message: 'Job source', progress: 100, resource: { kind: 'job', id: request.source.job_id } })
      Object.assign(wf.outputs, { job_id: request.source.job_id, model_url: `${P}/jobs/${request.source.job_id}/model.glb` })
    }
    if (!request.segmentation) Object.assign(step('segmentation'), { status: 'skipped', message: 'No segmentation requested', progress: 100 })
    if (!request.parts) Object.assign(step('part_extraction'), { status: 'skipped', message: 'No part extraction requested', progress: 100 })
    if (kind === 'prompt') {
      const ig = createImageGeneration({ ...request.source.generation, num_images: 1 }, { workflowId: wf.id })
      step('text_to_image').resource = { kind: 'image_generation', id: ig.id }
    }
    workflows.set(wf.id, wf)
    updateWorkflowProgress(wf)
    return wf
  }

  function updateWorkflowProgress(wf) {
    const weights = Object.fromEntries(WORKFLOW_STEPS.map(([id, , weight]) => [id, weight]))
    const total = wf.steps.reduce((sum, step) => sum + weights[step.id], 0)
    const done = wf.steps.reduce((sum, step) => sum + weights[step.id] * (['done', 'skipped'].includes(step.status) ? 100 : step.progress ?? 0), 0)
    const current = wf.steps.find((step) => step.status === 'running' || step.status === 'failed') ?? (ACTIVE.includes(wf.status) ? wf.steps.find((step) => step.status === 'pending') : undefined)
    wf.current_step = ACTIVE.includes(wf.status) || wf.status === 'failed' ? current?.id ?? null : null
    const percent = wf.status === 'completed' ? 100 : Math.max(wf.progress.percent, round1(done / total))
    const message = wf.status === 'completed' ? 'Completed' : wf.status === 'cancelled' ? 'Cancelled' : wf.status === 'failed' ? wf.error.message : current ? current.message ?? STEP_MESSAGES[current.id] : 'Queued'
    wf.progress = { percent, message, updated_at: now() }
  }

  function failWorkflow(wf, step, childKind, childId, childError) {
    const label = WORKFLOW_STEPS.find(([id]) => id === step.id)[3]
    step.status = 'failed'
    step.message = `${label} failed`
    for (const other of wf.steps) if (other.status === 'pending') Object.assign(other, { status: 'skipped', message: null, progress: null })
    wf.status = 'failed'
    wf.finished_at = now()
    wf.error = { code: 'step_failed', message: `${label} failed`, details: { step: step.id, child_kind: childKind, child_id: childId, child_error: { code: childError?.code ?? 'run_error', message: childError?.message ?? `${label} failed`, details: childError?.details ?? null } } }
  }

  function cancelWorkflow(wf) {
    for (const step of wf.steps) {
      if (step.status === 'running') {
        if (step.resource?.kind === 'job') ctx.cancelJob(ctx.jobs.get(step.resource.id))
        else if (step.resource && runs.has(step.resource.id)) cancelRun(runs.get(step.resource.id))
        step.status = 'cancelled'
      } else if (step.status === 'pending') Object.assign(step, { status: 'skipped', message: 'Cancelled', progress: null })
    }
    wf.status = 'cancelled'
    wf.finished_at = now()
    updateWorkflowProgress(wf)
    broadcastWorkflow(wf, true)
  }

  /** Map a child run onto its step; returns 'done' once the child completed. */
  function followRun(wf, step, childKind) {
    const child = runs.get(step.resource.id)
    if (!child) return failWorkflow(wf, step, childKind, step.resource.id, { code: 'source_missing', message: 'The child run was deleted', details: null })
    step.warning_count = child.warnings.length
    if (ACTIVE.includes(child.status)) {
      Object.assign(step, { status: 'running', progress: child.progress.percent, message: child.progress.message })
      return 'running'
    }
    if (child.status === 'completed') {
      Object.assign(step, { status: 'done', progress: 100, message: null })
      return 'done'
    }
    failWorkflow(wf, step, childKind, child.id, child.error ?? { code: 'cancelled', message: 'The run was cancelled', details: null })
    return 'failed'
  }

  const STEP_HANDLERS = {
    text_to_image(wf, step) {
      if (followRun(wf, step, 'image_generation') !== 'done') return
      step.artifacts = [{ role: 'candidate', url: runs.get(step.resource.id).result.candidates[0].thumbnail_url }]
    },
    import_image(wf, step) {
      const ig = runs.get(wf.steps.find((item) => item.id === 'text_to_image').resource.id)
      const used = useCandidate(ig, 0, { slot: 'front', make_primary: true })
      if (used.status) return failWorkflow(wf, step, 'image_generation', ig.id, used)
      Object.assign(step, { status: 'done', progress: 100, resource: { kind: 'reference_image', id: used.image.id, reference_set_id: used.set.id } })
      Object.assign(wf.outputs, { reference_set_id: used.set.id, image_id: used.image.id })
    },
    segmentation(wf, step) {
      if (!step.resource) {
        if (queueBlocked()) return failWorkflow(wf, step, 'segmentation', null, { code: 'queue_full', message: 'The run queue is full', details: null })
        const source = wf.request.source.kind === 'job' ? { kind: 'job', job_id: wf.request.source.job_id } : { kind: 'reference_image', reference_set_id: wf.outputs.reference_set_id, image_id: wf.outputs.image_id }
        step.resource = { kind: 'segmentation', id: createSegmentation({ source, options: wf.request.segmentation }, { workflowId: wf.id }).id }
      }
      if (followRun(wf, step, 'segmentation') !== 'done') return
      wf.outputs.segmentation_id = step.resource.id
      step.artifacts = [{ role: 'overlay', url: runs.get(step.resource.id).result.overlay_url }]
    },
    reconstruction(wf, step) {
      if (!step.resource) {
        const created = ctx.createJob({ reference_set_id: wf.outputs.reference_set_id, primary_image_id: wf.outputs.image_id, settings: wf.request.reconstruction.settings })
        if (created.error) return failWorkflow(wf, step, 'job', null, created.error)
        step.resource = { kind: 'job', id: created.job.id }
        Object.assign(wf.outputs, { job_id: created.job.id })
      }
      const job = ctx.jobs.get(step.resource.id)
      if (!job) return failWorkflow(wf, step, 'job', step.resource.id, { code: 'job_deleted', message: 'The job was deleted', details: null })
      if (ACTIVE.includes(job.status)) return Object.assign(step, { status: 'running', progress: job.progress.percent, message: job.progress.message })
      if (job.status === 'completed') {
        Object.assign(step, { status: 'done', progress: 100, message: null, artifacts: [{ role: 'preview', url: `${P}/jobs/${job.id}/preview` }] })
        wf.outputs.model_url = job.result.model_url
        return
      }
      failWorkflow(wf, step, 'job', job.id, job.error ?? { code: 'cancelled', message: 'The job was cancelled', details: null })
    },
    part_extraction(wf, step) {
      if (!step.resource) {
        if (queueBlocked()) return failWorkflow(wf, step, 'part_analysis', null, { code: 'queue_full', message: 'The run queue is full', details: null })
        const job = ctx.jobs.get(wf.outputs.job_id)
        const options = normalizePartOptions({ ...wf.request.parts, segmentation_id: wf.outputs.segmentation_id })
        step.resource = { kind: 'part_analysis', id: createPartAnalysis(job, options, { workflowId: wf.id }).id }
        wf.outputs.part_analysis_id = step.resource.id
      }
      if (followRun(wf, step, 'part_analysis') !== 'done') return
      const pa = runs.get(step.resource.id)
      step.artifacts = pa.result.preview_urls.front ? [{ role: 'preview', url: pa.result.preview_urls.front }] : []
      Object.assign(wf.outputs, { parts_model_url: pa.result.assembled_url, manifest_url: pa.result.manifest_url })
    },
    export(wf, step) {
      Object.assign(step, { status: 'done', progress: 100, message: null })
    },
  }

  const queueBlocked = () => scenario.queue_full || [...runs.values()].filter((run) => run.status === 'queued').length >= RUN_QUEUE_MAX

  function advanceWorkflow(wf) {
    if (wf.status === 'queued') {
      // "queued ─► running (first child created / first step done)": the 202 says queued, the first tick starts it.
      wf.status = 'running'
      wf.started_at = now()
    }
    const step = wf.steps.find((item) => !['done', 'skipped'].includes(item.status))
    if (step) STEP_HANDLERS[step.id](wf, step)
    if (wf.status === 'running' && wf.steps.every((item) => ['done', 'skipped'].includes(item.status))) {
      wf.status = 'completed'
      wf.finished_at = now()
    }
    updateWorkflowProgress(wf)
    broadcastWorkflow(wf)
  }

  // ----------------------------------------------------------------------------------------------
  // Tick (every 250 ms from mock_backend)
  // ----------------------------------------------------------------------------------------------
  function tick() {
    for (const [id, live] of modelStates) if (live.state === 'loading' && live.until && Date.now() >= live.until) setModelState(id, 'loaded')
    tickRuns()
    for (const wf of workflows.values()) if (ACTIVE.includes(wf.status)) advanceWorkflow(wf)
    broadcastFeaturesIfChanged()
  }

  function onJobDeleted(jobId) {
    for (const run of [...runs.values()]) {
      if (run.kind !== 'part_analysis' || run.job_id !== jobId) continue
      cancelRun(run)
      runs.delete(run.id)
    }
  }

  // ----------------------------------------------------------------------------------------------
  // Files
  // ----------------------------------------------------------------------------------------------
  const pngHeaders = { 'Content-Type': 'image/png', 'Cache-Control': 'private, max-age=3600' }
  const webpHeaders = { 'Content-Type': 'image/webp', 'Cache-Control': 'private, max-age=3600' }
  const glbHeaders = (filename) => ({ 'Content-Type': 'model/gltf-binary', ...(filename ? { 'Content-Disposition': `attachment; filename="${filename}"` } : {}) })

  function partGlb(run, partId) {
    const { partGlbs, semantic, featureIds } = run.sim.artifacts
    if (!partGlbs.has(partId)) partGlbs.set(partId, buildPartsGlb({ analysisId: run.id, jobId: run.job_id, featureIds, layout: semantic ? 'semantic' : 'components', only: partId }).buffer)
    return partGlbs.get(partId)
  }

  function bufferBytes(run, entry) {
    const size = { uint8: 1, uint16: 2, uint32: 4 }[entry.dtype] ?? 1
    const bytes = Buffer.alloc(entry.count * size * (entry.components ?? 1))
    if (entry.id === 'face_labels') {
      const labels = run.sim.artifacts.glb.nodes.flatMap((node) => node.faceLabels)
      labels.slice(0, entry.count).forEach((value, index) => bytes.writeUInt16LE(value, index * 2))
    }
    return bytes
  }

  function partAnalysisFile(req, res, run, sub) {
    if (sub === 'log') return send(res, 200, Buffer.from(`[mock] part analysis ${run.id} of ${run.job_id}: ${run.status}\n${run.steps.map((step) => `${step.id}: ${step.status}${step.message ? ` (${step.message})` : ''}`).join('\n')}\n`), { 'Content-Type': 'text/plain; charset=utf-8' })
    if (run.status !== 'completed') return fail(res, 409, 'conflict', 'The part analysis is not completed', { reason: 'not_completed' })
    const { glb, manifest } = run.sim.artifacts
    const jobId = run.job_id
    let m
    if (sub === 'manifest') return send(res, 200, manifest, { 'Cache-Control': 'no-cache' })
    if (sub === 'model.glb') return send(res, 200, glb.buffer, glbHeaders())
    if (sub === 'export/glb') return send(res, 200, glb.buffer, glbHeaders(`img3d_${jobId}_parts_${run.id}.glb`))
    if (sub === 'export/zip') {
      const entries = [{ name: 'manifest.json', data: Buffer.from(JSON.stringify(manifest, null, 2)) }, { name: 'model_parts.glb', data: glb.buffer }]
      if (new URL(req.url, 'http://mock').searchParams.get('include_base') === 'true') entries.push({ name: 'model.glb', data: ctx.baseGlb })
      return send(res, 200, zipStore(entries), { 'Content-Type': 'application/zip', 'Content-Disposition': `attachment; filename="img3d_${jobId}_parts_${run.id}.zip"` })
    }
    if ((m = /^parts\/([^/]+)\/(model\.glb|export\/glb)$/.exec(sub))) {
      const part = manifest.parts.find((item) => item.id === decodeURIComponent(m[1]))
      if (!part) return fail(res, 404, 'not_found', 'Unknown part')
      if (part.kind !== 'base' && part.kind !== 'physical') return fail(res, 409, 'conflict', 'This part has no mesh of its own', { reason: 'no_mesh' })
      return send(res, 200, partGlb(run, part.id), glbHeaders(m[2] === 'export/glb' ? `img3d_${jobId}_${part.id}.glb` : null))
    }
    if ((m = /^masks\/(\d{1,5})$/.exec(sub))) {
      const value = Number(m[1])
      return manifest.label_table.some((row) => row.value === value) ? send(res, 200, maskPng(ctx.encodePng, value), pngHeaders) : fail(res, 404, 'not_found', 'Mask not found')
    }
    if ((m = /^thumbs\/([a-z0-9_.]{1,90}\.webp)$/.exec(sub))) {
      const part = manifest.parts.find((item) => item.files.thumbnail === `thumbs/${m[1]}`)
      return part ? send(res, 200, thumbWebp(part.id), webpHeaders) : fail(res, 404, 'not_found', 'Thumbnail not found')
    }
    if ((m = /^segmentation\/(source\.png|labels\.png|overlay\.png)$/.exec(sub))) {
      if (!run.sim.artifacts.semantic) return fail(res, 404, 'not_found', 'The components strategy has no segmentation')
      if (m[1] === 'labels.png') return send(res, 200, gray16Png(128, 128, (x, y) => (y < 40 ? 30 : y < 80 ? 3 : 40) * (x > 8 && x < 120 ? 1 : 0)), pngHeaders)
      return send(res, 200, m[1] === 'overlay.png' ? overlayPng(ctx.encodePng) : candidatePng(ctx.encodePng, 0), pngHeaders)
    }
    if ((m = /^previews\/(front|back|left|right)\.png$/.exec(sub))) {
      return run.result.preview_urls[m[1]] ? send(res, 200, previewPng(ctx.encodePng, m[1]), pngHeaders) : fail(res, 404, 'not_found', 'Preview not found')
    }
    if ((m = /^buffers\/([a-z0-9_.]+)$/.exec(sub))) {
      const entry = manifest.buffers.find((item) => item.id === m[1])
      if (!entry) return fail(res, 404, 'not_found', 'Buffer not found')
      return send(res, 200, bufferBytes(run, entry), { 'Content-Type': 'application/octet-stream', 'X-Img3d-Dtype': entry.dtype, 'X-Img3d-Count': String(entry.count) })
    }
    return fail(res, 404, 'not_found', `No route for ${req.method} ${req.url}`)
  }

  function segmentationFile(res, run, sub) {
    if (sub === 'source') return run.sim.sourceBytes ? send(res, 200, run.sim.sourceBytes, pngHeaders) : fail(res, 404, 'not_found', 'Source snapshot not found')
    if (run.status !== 'completed') return fail(res, 409, 'conflict', 'The segmentation is not completed', { reason: 'not_completed' })
    if (sub === 'overlay') return send(res, 200, overlayPng(ctx.encodePng, run.result.labels.map((label) => label.value)), pngHeaders)
    if (sub === 'labels') return send(res, 200, gray16Png(128, 128, (x, y) => run.result.labels[Math.min(run.result.labels.length - 1, Math.floor((y / 128) * run.result.labels.length))].value * (x > 16 && x < 112 ? 1 : 0)), pngHeaders)
    const value = Number(/^masks\/(\d{1,5})$/.exec(sub)?.[1])
    return run.result.labels.some((label) => label.value === value) ? send(res, 200, maskPng(ctx.encodePng, value), pngHeaders) : fail(res, 404, 'not_found', 'Mask not found')
  }

  // ----------------------------------------------------------------------------------------------
  // Create routes (order of checks: 422 body → 503 flag → 404/409 existence → 503 capability → 429 queue)
  // ----------------------------------------------------------------------------------------------
  async function postImageGeneration(req, res) {
    const body = await readJson(req, res, 'ImageGenerationRequest')
    if (!body) return
    const details = generationDetails(body, ['body'], { inWorkflow: false })
    if (details.length) return validationFail(res, details)
    if (featureDisabled(res)) return
    if (!featuresView().pipeline.prompt_to_3d.available) return capabilityFail(res, 'prompt_to_3d')
    const model = body.model_id ? modelView(body.model_id) : null
    if (body.model_id && !(model && usable(model))) {
      return fail(res, 503, 'model_unavailable', `Model ${body.model_id} is not usable`, { model_id: body.model_id, state: model?.state ?? 'unavailable', reason_code: model?.reason_code ?? 'unsupported' })
    }
    if (queueFull(res)) return
    const run = createImageGeneration(body)
    ctx.markCreated(res, run.id)
    send(res, 202, publicRun(run))
  }

  async function postCandidateUse(req, res, run, index) {
    const body = await readJson(req, res, 'UseCandidateRequest', { optional: true })
    if (!body) return
    const details = []
    checkEnum(details, ['body', 'slot'], body.slot, SLOTS)
    checkBool(details, ['body', 'make_primary'], body.make_primary)
    if (details.length) return validationFail(res, details)
    if (featureDisabled(res)) return
    if (index < 0) return fail(res, 404, 'not_found', 'Candidate not found')
    const used = useCandidate(run, index, body)
    if (used.status) return fail(res, used.status, used.code, used.message, used.details)
    ctx.markCreated(res, used.image.id)
    send(res, 201, used)
  }

  async function postWorkflow(req, res) {
    const body = await readJson(req, res, 'WorkflowRequest')
    if (!body) return
    const details = workflowDetails(body)
    const graphBytes = isObject(body.graph) ? Buffer.byteLength(JSON.stringify(body.graph)) : 0
    if (graphBytes > catalogView().limits.graph_max_bytes) details.push(issue(['body', 'graph'], 'graph_too_large', 'The graph exceeds workflow_graph_max_bytes'))
    if (details.length) return validationFail(res, details)
    if (featureDisabled(res)) return
    let request = normalizedRequest(body)
    let nodeIds = {}
    if (isObject(body.graph)) {
      const report = validateGraph(body.graph, graphBytes)
      if (!report.valid) return fail(res, 422, 'invalid_workflow', 'The workflow graph is invalid', { issues: report.issues })
      ;({ request, nodeIds } = compileGraph(body.graph))
    }
    const existence = sourceExistence(request.source)
    if (existence) return fail(res, existence.status, existence.code, existence.message, existence.details ?? null)
    const features = featuresView()
    if (request.source.kind === 'prompt' && !features.pipeline.prompt_to_3d.available) return capabilityFail(res, 'prompt_to_3d')
    if (request.segmentation && !features.pipeline.segmentation.available) return capabilityFail(res, 'segmentation')
    if (request.parts && strategyUnavailable(res, normalizePartOptions(request.parts).strategy)) return
    if (queueFull(res)) return
    const wf = createWorkflow(request, { graph: isObject(body.graph) ? body.graph : null, layout: body.layout ?? null, nodeIds })
    ctx.markCreated(res, wf.id)
    send(res, 202, publicWorkflow(wf))
    broadcastWorkflow(wf, true)
  }

  // ----------------------------------------------------------------------------------------------
  // Router
  // ----------------------------------------------------------------------------------------------
  const sortNewest = (items) => items.sort((a, b) => b.created_at.localeCompare(a.created_at))

  async function handle(req, res, url) {
    const path = url.pathname
    const method = req.method
    if (!path.startsWith(`${P}/`)) return false
    if (scenario.features === 'legacy404' && LEGACY_404.test(path)) {
      fail(res, 404, 'not_found', `No route for ${method} ${path}`)
      return true
    }
    let m

    // ---- discovery (§6.1) ----
    if (path === `${P}/features` && method === 'GET') {
      if (jAuth(req, res)) send(res, 200, featuresView())
      return true
    }
    if (path === `${P}/models` && method === 'GET') {
      if (!jAuth(req, res)) return true
      const kind = url.searchParams.get('kind')
      const kinds = [...new Set(MODELS.map((model) => model.kind))]
      if (kind !== null && !kinds.includes(kind)) {
        validationFail(res, [issue(['query', 'kind'], 'enum', `Input should be ${kinds.join(', ')}`, { input: kind })])
        return true
      }
      const includeHidden = url.searchParams.get('include_hidden') === 'true'
      const ids = [...scenarioFeatures().models.map((model) => model.id), ...(includeHidden ? MODELS.filter((model) => model.hidden_by_default).map((model) => model.id) : [])]
      send(res, 200, { items: ids.map(modelView).filter((model) => kind === null || model.kind === kind) })
      return true
    }
    if ((m = new RegExp(`^${P}/models/([\\w.-]+)(/load|/unload)?$`).exec(path))) {
      if (!jAuth(req, res)) return true
      const model = modelView(m[1])
      if (!model) {
        fail(res, 404, 'not_found', 'Model not found')
        return true
      }
      if (!m[2] && method === 'GET') {
        send(res, 200, model)
        return true
      }
      if (method !== 'POST') return false
      if (featureDisabled(res)) return true
      const busy = [...runs.values()].some((run) => run.status === 'running' && run.models.some((use) => use.id === model.id))
      if (m[2] === '/load') {
        if (model.state === 'loading') fail(res, 409, 'conflict', 'The model is already loading', { reason: 'loading' })
        else if (!usable(model)) fail(res, 503, 'model_unavailable', model.reason ?? `${model.display_name} is not usable`, { model_id: model.id, state: model.state, reason_code: model.reason_code })
        else {
          if (model.state !== 'loaded') {
            setModelState(model.id, 'loading')
            modelStates.get(model.id).until = Date.now() + scenario.t2i_cold_seconds * 1000
            if (scenario.t2i_cold_seconds <= 0) setModelState(model.id, 'loaded')
          }
          broadcastFeaturesIfChanged()
          send(res, 202, modelView(model.id))
        }
        return true
      }
      if (busy) fail(res, 409, 'conflict', 'The model is in use by the running run', { reason: 'in_use' })
      else {
        if (model.state === 'loaded' || model.state === 'loading') setModelState(model.id, 'installed')
        broadcastFeaturesIfChanged()
        send(res, 202, modelView(model.id))
      }
      return true
    }

    // ---- image generations (§6.2) ----
    if (path === `${P}/image-generations` && method === 'POST') {
      if (jAuth(req, res)) await postImageGeneration(req, res)
      return true
    }
    if (path === `${P}/image-generations` && method === 'GET') {
      if (!jAuth(req, res)) return true
      const params = listParams(url, res)
      if (params) send(res, 200, { items: sortNewest([...runs.values()].filter((run) => run.kind === 'image_generation' && (!params.status || run.status === params.status))).slice(0, params.limit).map(publicRun) })
      return true
    }
    if ((m = new RegExp(`^${P}/image-generations/([a-z]+_\\w+)(/cancel|/candidates/([^/]+)(/thumbnail|/use)?)?$`).exec(path))) {
      const fileRoute = m[3] !== undefined && m[4] !== '/use'
      if (!fileRoute && !jAuth(req, res)) return true
      const run = runs.get(m[1])
      if (!run || run.kind !== 'image_generation') {
        fail(res, 404, 'not_found', 'Image generation not found')
        return true
      }
      if (!m[2] && method === 'GET') send(res, 200, publicRun(run))
      else if (!m[2] && method === 'DELETE') {
        const referenced = [...workflows.values()].some((wf) => ACTIVE.includes(wf.status) && wf.steps.some((step) => step.resource?.id === run.id))
        if (ACTIVE.includes(run.status) || referenced) fail(res, 409, 'conflict', 'The image generation is active or used by an active workflow')
        else {
          runs.delete(run.id)
          send(res, 204)
        }
      } else if (m[2] === '/cancel' && method === 'POST') {
        if (!ACTIVE.includes(run.status)) fail(res, 409, 'conflict', 'The image generation already finished')
        else {
          cancelRun(run)
          send(res, 202, publicRun(run))
        }
      } else if (m[3] !== undefined) {
        const index = /^[0-3]$/.test(m[3]) ? Number(m[3]) : -1
        if (m[4] === '/use') {
          if (method !== 'POST') return false
          await postCandidateUse(req, res, run, index)
        } else if (method === 'GET') {
          if (index < 0 || (run.status === 'completed' && !run.result.candidates.some((candidate) => candidate.index === index))) fail(res, 404, 'not_found', 'Candidate not found')
          else if (run.status !== 'completed') fail(res, 409, 'conflict', 'The image generation is not completed', { reason: 'not_completed' })
          else if (m[4] === '/thumbnail') send(res, 200, candidateThumbWebp(index), webpHeaders)
          else send(res, 200, candidateBytes(run, index), pngHeaders)
        } else return false
      } else return false
      return true
    }

    // ---- segmentations (§6.3) ----
    if (path === `${P}/segmentations` && method === 'POST') {
      if (!jAuth(req, res)) return true
      const body = await readJson(req, res, 'SegmentationRequest')
      if (!body) return true
      const details = []
      const source = body.source
      if (!isObject(source)) details.push(issue(['body', 'source'], 'missing', 'Field required'))
      else if (source.kind === 'reference_image') {
        if (typeof source.reference_set_id !== 'string') details.push(issue(['body', 'source', 'reference_set_id'], 'missing', 'Field required'))
        if (typeof source.image_id !== 'string') details.push(issue(['body', 'source', 'image_id'], 'missing', 'Field required'))
      } else if (source.kind === 'job') {
        if (typeof source.job_id !== 'string') details.push(issue(['body', 'source', 'job_id'], 'missing', 'Field required'))
      } else details.push(issue(['body', 'source'], 'union_tag_invalid', "Input tag does not match 'reference_image' or 'job'", { input: source.kind }))
      details.push(...segOptionsDetails(body.options, ['body', 'options']))
      if (details.length) {
        validationFail(res, details)
        return true
      }
      if (featureDisabled(res)) return true
      if (source.kind === 'reference_image') {
        const set = ctx.sets.get(source.reference_set_id)
        if (!set || !set.images.some((image) => image.id === source.image_id)) {
          fail(res, 404, 'not_found', set ? 'Image not found' : 'Reference set not found')
          return true
        }
      } else {
        const job = ctx.jobs.get(source.job_id)
        if (!job) {
          fail(res, 404, 'not_found', 'Job not found')
          return true
        }
        if (job.status !== 'completed') {
          fail(res, 409, 'conflict', 'The job is not completed', { reason: 'not_completed' })
          return true
        }
      }
      if (!featuresView().pipeline.segmentation.available) capabilityFail(res, 'segmentation')
      else if (!queueFull(res)) {
        const run = createSegmentation(body)
        ctx.markCreated(res, run.id)
        send(res, 202, publicRun(run))
      }
      return true
    }
    if (path === `${P}/segmentations` && method === 'GET') {
      if (!jAuth(req, res)) return true
      const params = listParams(url, res, ['reference_set_id', 'job_id'])
      if (params) {
        const items = [...runs.values()].filter((run) => run.kind === 'segmentation' && (!params.status || run.status === params.status)
          && (!params.reference_set_id || run.options.source.reference_set_id === params.reference_set_id) && (!params.job_id || run.options.source.job_id === params.job_id))
        send(res, 200, { items: sortNewest(items).slice(0, params.limit).map(publicRun) })
      }
      return true
    }
    if ((m = new RegExp(`^${P}/segmentations/([a-z]+_\\w+)(?:/(cancel|overlay|labels|source|masks/[^/]+))?$`).exec(path))) {
      const sub = m[2]
      const fileRoute = sub !== undefined && sub !== 'cancel'
      if (!fileRoute && !jAuth(req, res)) return true
      const run = runs.get(m[1])
      if (!run || run.kind !== 'segmentation') {
        fail(res, 404, 'not_found', 'Segmentation not found')
        return true
      }
      if (!sub && method === 'GET') send(res, 200, publicRun(run))
      else if (!sub && method === 'DELETE') {
        const referenced = [...workflows.values()].some((wf) => ACTIVE.includes(wf.status) && wf.steps.some((step) => step.resource?.id === run.id))
        if (ACTIVE.includes(run.status) || referenced) fail(res, 409, 'conflict', 'The segmentation is active or used by an active workflow')
        else {
          runs.delete(run.id)
          send(res, 204)
        }
      } else if (sub === 'cancel' && method === 'POST') {
        if (!ACTIVE.includes(run.status)) fail(res, 409, 'conflict', 'The segmentation already finished')
        else {
          cancelRun(run)
          send(res, 202, publicRun(run))
        }
      } else if (fileRoute && method === 'GET') segmentationFile(res, run, sub)
      else return false
      return true
    }

    // ---- part analyses (§6.4) ----
    if ((m = new RegExp(`^${P}/jobs/([a-z]+_\\w+)/part-analyses(/latest)?$`).exec(path))) {
      if (!jAuth(req, res)) return true
      if (method === 'POST' && !m[2]) {
        const body = await readJson(req, res, 'PartAnalysisOptions', { optional: true })
        if (!body) return true
        const details = partOptionsDetails(body, ['body'])
        if (details.length) {
          validationFail(res, details)
          return true
        }
        if (featureDisabled(res)) return true
        const job = ctx.jobs.get(m[1])
        const sg = body.segmentation_id ? runs.get(body.segmentation_id) : null
        const options = normalizePartOptions(body)
        if (!job) fail(res, 404, 'not_found', 'Job not found')
        else if (job.status !== 'completed') fail(res, 409, 'conflict', 'The job is not completed', { reason: 'not_completed' })
        else if (body.segmentation_id && sg?.kind !== 'segmentation') fail(res, 404, 'not_found', 'Segmentation not found')
        else if (sg && sg.status !== 'completed') fail(res, 409, 'conflict', 'The segmentation is not completed', { reason: 'not_completed' })
        else if (!strategyUnavailable(res, options.strategy) && !queueFull(res)) {
          const run = createPartAnalysis(job, options)
          ctx.markCreated(res, run.id)
          send(res, 202, publicRun(run))
        }
        return true
      }
      if (method !== 'GET') return false
      if (!ctx.jobs.has(m[1])) {
        fail(res, 404, 'not_found', 'Job not found')
        return true
      }
      const items = sortNewest([...runs.values()].filter((run) => run.kind === 'part_analysis' && run.job_id === m[1]))
      if (!m[2]) send(res, 200, { items: items.map(publicRun) })
      else {
        const latest = items.find((run) => run.status === 'completed')
        if (latest) send(res, 200, publicRun(latest))
        else fail(res, 404, 'not_found', 'No completed part analysis for this job', { reason: 'none' })
      }
      return true
    }
    if ((m = new RegExp(`^${P}/part-analyses/([a-z]+_\\w+)(?:/(.+))?$`).exec(path))) {
      const sub = m[2]
      const fileRoute = sub !== undefined && sub !== 'cancel'
      if (!fileRoute && !jAuth(req, res)) return true
      const run = runs.get(m[1])
      if (!run || run.kind !== 'part_analysis') {
        fail(res, 404, 'not_found', 'Part analysis not found')
        return true
      }
      if (!sub && method === 'GET') send(res, 200, publicRun(run))
      else if (!sub && method === 'DELETE') {
        if (ACTIVE.includes(run.status)) fail(res, 409, 'conflict', 'The part analysis is active')
        else {
          runs.delete(run.id)
          send(res, 204)
        }
      } else if (sub === 'cancel' && method === 'POST') {
        if (!ACTIVE.includes(run.status)) fail(res, 409, 'conflict', 'The part analysis already finished')
        else {
          cancelRun(run)
          send(res, 202, publicRun(run))
        }
      } else if (fileRoute && method === 'GET') partAnalysisFile(req, res, run, sub)
      else return false
      return true
    }

    // ---- workflows (§6.5) ----
    if (path === `${P}/workflows/catalog` && method === 'GET') {
      if (!jAuth(req, res)) return true
      const catalog = catalogView()
      if (req.headers['if-none-match'] === catalog.etag) send(res, 304, undefined, { ETag: catalog.etag })
      else send(res, 200, catalog, { ETag: catalog.etag })
      return true
    }
    if (path === `${P}/workflows/validate` && method === 'POST') {
      if (!jAuth(req, res)) return true
      const body = await readJson(req, res, 'WorkflowRequest')
      if (!body) return true
      const details = workflowDetails(body)
      if (details.length) validationFail(res, details)
      else send(res, 200, isObject(body.graph) ? validateGraph(body.graph, Buffer.byteLength(JSON.stringify(body.graph))) : validateSimple(body))
      return true
    }
    if (path === `${P}/workflows` && method === 'POST') {
      if (jAuth(req, res)) await postWorkflow(req, res)
      return true
    }
    if (path === `${P}/workflows` && method === 'GET') {
      if (!jAuth(req, res)) return true
      const params = listParams(url, res, ['job_id', 'image_generation_id'])
      if (params) {
        const items = [...workflows.values()].filter((wf) => (!params.status || wf.status === params.status) && (!params.job_id || wf.outputs.job_id === params.job_id)
          && (!params.image_generation_id || wf.steps.some((step) => step.id === 'text_to_image' && step.resource?.id === params.image_generation_id)))
        send(res, 200, { items: sortNewest(items).slice(0, params.limit).map(publicWorkflow) })
      }
      return true
    }
    if ((m = new RegExp(`^${P}/workflows/([a-z]+_\\w+)(/graph|/cancel)?$`).exec(path))) {
      if (!jAuth(req, res)) return true
      const wf = workflows.get(m[1])
      if (!wf) {
        fail(res, 404, 'not_found', 'Workflow not found')
        return true
      }
      if (!m[2] && method === 'GET') send(res, 200, publicWorkflow(wf))
      else if (!m[2] && method === 'DELETE') {
        if (ACTIVE.includes(wf.status)) fail(res, 409, 'conflict', 'The workflow is active')
        else {
          workflows.delete(wf.id)
          send(res, 204)
        }
      } else if (m[2] === '/graph' && method === 'GET') {
        if (wf.sim.graph) send(res, 200, { graph: wf.sim.graph, layout: wf.sim.layout })
        else fail(res, 404, 'not_found', 'No graph stored for this workflow')
      } else if (m[2] === '/cancel' && method === 'POST') {
        if (!ACTIVE.includes(wf.status)) fail(res, 409, 'conflict', 'The workflow already finished')
        else {
          cancelWorkflow(wf)
          send(res, 202, publicWorkflow(wf))
        }
      } else return false
      return true
    }
    return false
  }

  return {
    handle,
    tick,
    readJson,
    onJobDeleted,
    featuresView,
    /** Broadcast `features.updated` right away (POST /api/__test/scenario); legacy404 sends nothing. */
    scenarioChanged: () => broadcastFeaturesIfChanged(true),
    /** Run-queue capacity for the legacy `POST /jobs` route (`queue_full` scenario flag). */
    queueFull,
  }
}
