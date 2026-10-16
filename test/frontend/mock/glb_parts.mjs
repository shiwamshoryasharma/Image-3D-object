/**
 * TEST DOUBLE DATA — procedural part-aware assets for the frontend mock backend (UI_DESIGN.md §10.4). Never used by
 * the app. No dependencies (Node >= 22).
 *
 * `buildPartsGlb()` writes a multi-node `model_parts.glb` laid out exactly like DESIGN.md §3.12:
 *
 *   scene "img3d_parts"  extras.img3d = {schema, analysis_id, job_id, region_attribute, manifest}
 *   └─ node "img3d_asset" (identity, flat)
 *      ├─ node "part:body"         POSITION NORMAL TEXCOORD_0 _FEATURE_ID_0 (UNSIGNED_SHORT) + UNSIGNED_INT indices
 *      │                           extras.img3d = {id, kind: "base", parent: null, regions: [{value, category}, …]}
 *      ├─ node "part:hair"         extras.img3d = {id, category, group, kind: "physical", parent, attach_to, seams}
 *      ├─ node "part:eye.left" · "part:eye.right" · "part:clothing.top"
 *
 * - `_FEATURE_ID_0` sits in its own bufferView with `byteStride: 4` and `accessor.byteOffset = 0` (2 bytes of zero
 *   padding per vertex), so three.js' GLTFLoader exposes it as an `InterleavedBufferAttribute`, as with the real file.
 * - Vertices are duplicated wherever incident faces carry different labels (DESIGN §3.4), so the per-face label is
 *   exact on every node. Every primitive uses material 0.
 * - `featureIds: false` writes the same file without `_FEATURE_ID_0` (mock `--parts-scenario no-attribute`).
 * - `layout: 'components'` relabels the four physical nodes as `component_001…004` (the `components` strategy).
 *
 * Also exported: a GLB reader (`parseGlb`, `readAccessor`) for the smoke test, procedural PNGs (candidates, overlay,
 * masks, previews; the RGBA encoder is injected: `encodePng(width, height, rgba)` from mock_backend.mjs), a 16-bit
 * grayscale PNG (segmentation `labels`), lossless two-colour WebPs (the `image/webp` thumbnail routes) and `crc32`.
 */
import { deflateSync } from 'node:zlib'

export const REGION_ATTRIBUTE = '_FEATURE_ID_0'

const GLB_MAGIC = 0x46546c67
const CHUNK_JSON = 0x4e4f534a
const CHUNK_BIN = 0x004e4942
const FLOAT = 5126
const UNSIGNED_SHORT = 5123
const UNSIGNED_INT = 5125
const ARRAY_BUFFER = 34962
const ELEMENT_ARRAY_BUFFER = 34963

// ------------------------------------------------------------------------------------------------
// Layout (taxonomy values from DESIGN.md §2.3)
// ------------------------------------------------------------------------------------------------
const BODY_ROWS = 24
const BODY_SEGMENTS = 32

/** Body labels: base 1, face region 3, eyebrows 22/23 (deferred), a jewelry band 53 (two overflow regions), and a
 *  value-0 patch (overflow `component_001` region) at the bottom. */
function bodyLabel(row, seg, layout) {
  if (layout === 'components') return 1
  if (row >= 22 && seg < 4) return 0
  // Segment 8 faces +Z (the front); the character's left is +X (segments below 8).
  if (row === 10 && seg >= 4 && seg < 13) return 53
  if (row === 3 && seg >= 6 && seg < 8) return 22
  if (row === 3 && seg >= 9 && seg < 11) return 23
  if (row >= 2 && row < 8 && seg >= 5 && seg < 12) return 3
  return 1
}

const SEMANTIC_PHYSICAL = [
  { id: 'hair', category: 'hair', group: 'hair', value: 30, attach_to: 'body.head.face', seams: ['seam_0'] },
  { id: 'eye.left', category: 'eye.left', group: 'eyes', value: 20, attach_to: 'body.head.face', seams: ['seam_1'] },
  { id: 'eye.right', category: 'eye.right', group: 'eyes', value: 21, attach_to: 'body.head.face', seams: ['seam_2'] },
  { id: 'clothing.top', category: 'clothing.top', group: 'clothes', value: 40, attach_to: 'body', seams: ['seam_3'] },
]

const COMPONENT_PHYSICAL = SEMANTIC_PHYSICAL.map((part, index) => ({
  id: `component_${String(index + 1).padStart(3, '0')}`, category: 'unknown', group: 'other', value: 0, attach_to: null, seams: [],
}))

const CATEGORY_BY_VALUE = { 0: 'unknown', 1: 'body', 3: 'body.head.face', 22: 'eyebrow.left', 23: 'eyebrow.right', 53: 'accessory.jewelry' }

// Geometry generators: each returns {rows, segs, at(r, s) -> {p, n, uv}} over an (rows+1) x (segs+1) vertex grid.
function sphere(center, radius, rows, segs, rowFrom = 0, rowTo = rows) {
  return {
    rows: rowTo - rowFrom,
    segs,
    at(r, s) {
      const phi = ((r + rowFrom) / rows) * Math.PI
      const theta = (s / segs) * Math.PI * 2
      const n = [Math.sin(phi) * Math.cos(theta), Math.cos(phi), Math.sin(phi) * Math.sin(theta)]
      return { p: n.map((v, k) => center[k] + v * radius), n, uv: [s / segs, (r + rowFrom) / rows] }
    },
  }
}

function cylinder(radius, y0, y1, rows, segs) {
  return {
    rows,
    segs,
    at(r, s) {
      const theta = (s / segs) * Math.PI * 2
      const n = [Math.cos(theta), 0, Math.sin(theta)]
      return { p: [radius * n[0], y0 + ((y1 - y0) * r) / rows, radius * n[2]], n, uv: [s / segs, r / rows] }
    },
  }
}

function layoutNodes(layout) {
  const physical = layout === 'components' ? COMPONENT_PHYSICAL : SEMANTIC_PHYSICAL
  const shapes = [
    sphere([0, 0.22, 0], 0.29, 24, 32, 0, 7),
    sphere([0.07, 0.3, 0.262], 0.03, 8, 12),
    sphere([-0.07, 0.3, 0.262], 0.03, 8, 12),
    cylinder(0.35, -0.2, 0.06, 4, 32),
  ]
  return [
    { id: 'body', kind: 'base', category: 'body', group: 'body', shape: sphere([0, 0, 0], 0.5, BODY_ROWS, BODY_SEGMENTS), label: (r, s) => bodyLabel(r, s, layout), scaleXZ: 0.68 },
    ...physical.map((part, index) => ({ ...part, kind: 'physical', shape: shapes[index], label: () => part.value })),
  ]
}

/** Grid patch → vertex arrays with one vertex per (grid vertex, label) pair (DESIGN §3.4 region-border duplicates). */
function buildPatch({ shape, label, scaleXZ = 1 }) {
  const { rows, segs } = shape
  const positions = [], normals = [], uvs = [], featureIds = [], indices = [], faceLabels = []
  const vertexIds = new Map()
  const vertex = (r, s, value) => {
    const key = `${r}:${s}:${value}`
    let id = vertexIds.get(key)
    if (id === undefined) {
      const { p, n, uv } = shape.at(r, s)
      id = positions.length / 3
      positions.push(p[0] * scaleXZ, p[1], p[2] * scaleXZ)
      normals.push(...n)
      uvs.push(...uv)
      featureIds.push(value)
      vertexIds.set(key, id)
    }
    return id
  }
  for (let r = 0; r < rows; r++) {
    for (let s = 0; s < segs; s++) {
      const value = label(r, s)
      const a = vertex(r, s, value), b = vertex(r + 1, s, value), c = vertex(r + 1, s + 1, value), d = vertex(r, s + 1, value)
      for (const tri of [[a, d, b], [b, d, c]]) {
        // Keep the winding counter-clockwise seen from outside (geometric normal agrees with the analytic one).
        const [p0, p1, p2] = tri.map((i) => positions.slice(i * 3, i * 3 + 3))
        const e1 = p1.map((v, k) => v - p0[k]), e2 = p2.map((v, k) => v - p0[k])
        const cross = [e1[1] * e2[2] - e1[2] * e2[1], e1[2] * e2[0] - e1[0] * e2[2], e1[0] * e2[1] - e1[1] * e2[0]]
        const nrm = normals.slice(tri[0] * 3, tri[0] * 3 + 3)
        const facing = cross[0] * nrm[0] + cross[1] * nrm[1] + cross[2] * nrm[2]
        indices.push(...(facing < 0 ? [tri[0], tri[2], tri[1]] : tri))
        faceLabels.push(value)
      }
    }
  }
  return { positions, normals, uvs, featureIds, indices, faceLabels }
}

function regionCounts(faceLabels) {
  const counts = {}
  for (const value of faceLabels) counts[value] = (counts[value] ?? 0) + 1
  return counts
}

function bounds(positions) {
  const min = [Infinity, Infinity, Infinity], max = [-Infinity, -Infinity, -Infinity]
  for (let i = 0; i < positions.length; i += 3) {
    for (let k = 0; k < 3; k++) {
      min[k] = Math.min(min[k], positions[i + k])
      max[k] = Math.max(max[k], positions[i + k])
    }
  }
  return { min: min.map(round6), max: max.map(round6) }
}

const round6 = (v) => Math.round(v * 1e6) / 1e6

function nodeExtras(node, regions) {
  if (node.kind === 'base') {
    return { id: node.id, kind: 'base', parent: null, regions: Object.keys(regions).map(Number).sort((a, b) => a - b).map((value) => ({ value, category: CATEGORY_BY_VALUE[value] ?? 'unknown' })) }
  }
  return { id: node.id, category: node.category, group: node.group, kind: node.kind, parent: 'body', attach_to: node.attach_to, seams: node.seams }
}

/**
 * Build `model_parts.glb` (or a single-node per-part GLB with `only: '<part id>'`).
 * Returns `{buffer, nodes: [{id, name, kind, faces, vertices, regions: {value: faceCount}, faceLabels, bboxMin, bboxMax,
 * centroid}], faces, vertices, bboxMin, bboxMax}` (`faceLabels` = taxonomy value per face, in index order).
 */
export function buildPartsGlb({ analysisId = 'pa_000000000000', jobId = 'job_000000000000', featureIds = true, layout = 'semantic', only = null } = {}) {
  const chosen = layoutNodes(layout).filter((node) => only === null || node.id === only)
  if (chosen.length === 0) throw new Error(`unknown part ${only}`)
  const chunks = []
  let byteLength = 0
  const bufferViews = [], accessors = [], meshes = [], partNodes = [], info = []
  const addView = (bytes, extra) => {
    bufferViews.push({ buffer: 0, byteOffset: byteLength, byteLength: bytes.length, ...extra })
    chunks.push(bytes)
    byteLength += bytes.length
    return bufferViews.length - 1
  }
  const addAccessor = (accessor) => accessors.push(accessor) - 1
  for (const node of chosen) {
    const patch = buildPatch(node)
    const count = patch.positions.length / 3
    const box = bounds(patch.positions)
    const attributes = {
      POSITION: addAccessor({ bufferView: addView(Buffer.from(new Float32Array(patch.positions).buffer), { target: ARRAY_BUFFER }), componentType: FLOAT, count, type: 'VEC3', min: box.min, max: box.max }),
      NORMAL: addAccessor({ bufferView: addView(Buffer.from(new Float32Array(patch.normals).buffer), { target: ARRAY_BUFFER }), componentType: FLOAT, count, type: 'VEC3' }),
      TEXCOORD_0: addAccessor({ bufferView: addView(Buffer.from(new Float32Array(patch.uvs).buffer), { target: ARRAY_BUFFER }), componentType: FLOAT, count, type: 'VEC2' }),
    }
    if (featureIds) {
      // glTF 2.0 §3.6.2.4: every vertex-attribute element is 4-byte aligned → u16 + 2 bytes padding, byteStride 4.
      const padded = Buffer.alloc(count * 4)
      patch.featureIds.forEach((value, i) => padded.writeUInt16LE(value, i * 4))
      attributes[REGION_ATTRIBUTE] = addAccessor({ bufferView: addView(padded, { byteStride: 4, target: ARRAY_BUFFER }), byteOffset: 0, componentType: UNSIGNED_SHORT, count, type: 'SCALAR' })
    }
    const indices = addAccessor({ bufferView: addView(Buffer.from(new Uint32Array(patch.indices).buffer), { target: ELEMENT_ARRAY_BUFFER }), componentType: UNSIGNED_INT, count: patch.indices.length, type: 'SCALAR' })
    meshes.push({ name: node.id, primitives: [{ attributes, indices, material: 0, mode: 4 }] })
    const regions = regionCounts(patch.faceLabels)
    partNodes.push({ name: `part:${node.id}`, mesh: meshes.length - 1, extras: { img3d: nodeExtras(node, regions) } })
    const centroid = [0, 1, 2].map((k) => round6((box.min[k] + box.max[k]) / 2))
    info.push({ id: node.id, name: `part:${node.id}`, kind: node.kind, faces: patch.indices.length / 3, vertices: count, regions, faceLabels: patch.faceLabels, bboxMin: box.min, bboxMax: box.max, centroid })
  }
  const gltf = {
    asset: { version: '2.0', generator: 'img3d test double (test/frontend/mock/glb_parts.mjs)' },
    scene: 0,
    scenes: [{ name: 'img3d_parts', nodes: [0], extras: { img3d: { schema: 'img3d.parts/1', analysis_id: analysisId, job_id: jobId, region_attribute: REGION_ATTRIBUTE, manifest: 'manifest.json' } } }],
    nodes: [{ name: 'img3d_asset', children: partNodes.map((_, index) => index + 1) }, ...partNodes],
    meshes,
    materials: [{ name: 'material_0', pbrMetallicRoughness: { baseColorFactor: [0.72, 0.66, 0.86, 1], metallicFactor: 0, roughnessFactor: 0.62 }, doubleSided: true }],
    buffers: [{ byteLength }],
    bufferViews,
    accessors,
  }
  const allMin = [0, 1, 2].map((k) => Math.min(...info.map((node) => node.bboxMin[k])))
  const allMax = [0, 1, 2].map((k) => Math.max(...info.map((node) => node.bboxMax[k])))
  return {
    buffer: encodeGlb(gltf, Buffer.concat(chunks)),
    nodes: info,
    faces: info.reduce((sum, node) => sum + node.faces, 0),
    vertices: info.reduce((sum, node) => sum + node.vertices, 0),
    bboxMin: allMin,
    bboxMax: allMax,
  }
}

function encodeGlb(gltf, bin) {
  let json = Buffer.from(JSON.stringify(gltf))
  if (json.length % 4) json = Buffer.concat([json, Buffer.alloc(4 - (json.length % 4), 0x20)])
  let body = bin
  if (body.length % 4) body = Buffer.concat([body, Buffer.alloc(4 - (body.length % 4), 0)])
  const header = Buffer.alloc(12), jsonHeader = Buffer.alloc(8), binHeader = Buffer.alloc(8)
  header.writeUInt32LE(GLB_MAGIC, 0)
  header.writeUInt32LE(2, 4)
  header.writeUInt32LE(12 + 8 + json.length + 8 + body.length, 8)
  jsonHeader.writeUInt32LE(json.length, 0)
  jsonHeader.writeUInt32LE(CHUNK_JSON, 4)
  binHeader.writeUInt32LE(body.length, 0)
  binHeader.writeUInt32LE(CHUNK_BIN, 4)
  return Buffer.concat([header, jsonHeader, json, binHeader, body])
}

// ------------------------------------------------------------------------------------------------
// GLB reader (smoke test / unit tests)
// ------------------------------------------------------------------------------------------------
/** Parse a GLB container → `{json, bin}`; throws on a malformed file. */
export function parseGlb(buffer) {
  if (buffer.length < 20 || buffer.readUInt32LE(0) !== GLB_MAGIC) throw new Error('not a GLB file')
  if (buffer.readUInt32LE(4) !== 2) throw new Error('unsupported GLB version')
  if (buffer.readUInt32LE(8) !== buffer.length) throw new Error('GLB length mismatch')
  let offset = 12
  let json = null
  let bin = null
  while (offset < buffer.length) {
    const length = buffer.readUInt32LE(offset)
    const type = buffer.readUInt32LE(offset + 4)
    const data = buffer.subarray(offset + 8, offset + 8 + length)
    if (type === CHUNK_JSON) json = JSON.parse(data.toString('utf8'))
    else if (type === CHUNK_BIN) bin = data
    offset += 8 + length
  }
  if (!json) throw new Error('GLB has no JSON chunk')
  return { json, bin }
}

const COMPONENTS = { SCALAR: 1, VEC2: 2, VEC3: 3, VEC4: 4 }
const BYTES = { 5121: 1, 5123: 2, 5125: 4, 5126: 4 }

/** Read an accessor as a flat number array, honouring `byteStride` (as GLTFLoader's InterleavedBuffer does). */
export function readAccessor({ json, bin }, index) {
  const accessor = json.accessors[index]
  const view = json.bufferViews[accessor.bufferView]
  const components = COMPONENTS[accessor.type]
  const size = BYTES[accessor.componentType]
  const stride = view.byteStride ?? components * size
  const base = (view.byteOffset ?? 0) + (accessor.byteOffset ?? 0)
  const read = { 5121: (o) => bin.readUInt8(o), 5123: (o) => bin.readUInt16LE(o), 5125: (o) => bin.readUInt32LE(o), 5126: (o) => bin.readFloatLE(o) }[accessor.componentType]
  const out = []
  for (let i = 0; i < accessor.count; i++) for (let c = 0; c < components; c++) out.push(read(base + i * stride + c * size))
  return out
}

// ------------------------------------------------------------------------------------------------
// Procedural images (test data only; `encodePng(width, height, rgba)` is injected)
// ------------------------------------------------------------------------------------------------
const PALETTE = [
  [231, 111, 81], [244, 162, 97], [233, 196, 106], [42, 157, 143], [38, 70, 83], [131, 56, 236],
  [58, 134, 255], [255, 0, 110], [251, 86, 7], [255, 190, 11], [139, 201, 38], [25, 130, 196],
]

export const colorForValue = (value) => (value === 0 ? [90, 90, 90] : PALETTE[value % PALETTE.length])

function paint(width, height, pixel) {
  const rgba = Buffer.alloc(width * height * 4)
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const [r, g, b, a] = pixel(x / width, y / height)
      const i = (y * width + x) * 4
      rgba[i] = r
      rgba[i + 1] = g
      rgba[i + 2] = b
      rgba[i + 3] = a
    }
  }
  return rgba
}

/** A character-ish silhouette (head + body) on a light-gray background; candidate `index` shifts the hue. */
export function candidatePng(encodePng, index, width = 224, height = 288) {
  const tint = PALETTE[(index * 5 + 3) % PALETTE.length]
  return encodePng(width, height, paint(width, height, (u, v) => {
    const head = (u - 0.5) ** 2 / 0.012 + (v - 0.2) ** 2 / 0.01 < 1
    const body = Math.abs(u - 0.5) < 0.16 + v * 0.08 && v > 0.3 && v < 0.92
    if (head) return [236, 200, 170, 255]
    if (body) return [tint[0], tint[1], tint[2], 255]
    return [205, 207, 212, 255]
  }))
}

/** Label overlay: coloured bands per taxonomy value with alpha (segmentation `overlay_url`). */
export function overlayPng(encodePng, values = [30, 3, 20, 21, 40], size = 256) {
  return encodePng(size, size, paint(size, size, (u, v) => {
    const inside = (u - 0.5) ** 2 + (v - 0.5) ** 2 < 0.16
    if (!inside) return [0, 0, 0, 0]
    const [r, g, b] = colorForValue(values[Math.min(values.length - 1, Math.floor(v * values.length))])
    return [r, g, b, 150]
  }))
}

/** 8-bit soft mask (white blob) for a label value; the blob position depends on the value. */
export function maskPng(encodePng, value, size = 128) {
  const cx = 0.25 + ((value * 37) % 50) / 100, cy = 0.25 + ((value * 53) % 50) / 100
  return encodePng(size, size, paint(size, size, (u, v) => {
    const d = Math.hypot(u - cx, v - cy)
    const a = Math.max(0, Math.min(255, Math.round((0.2 - d) * 2550)))
    return [255, 255, 255, a]
  }))
}

/** Label-colour preview render stand-in for a view (`front|back|left|right`). */
export function previewPng(encodePng, view, size = 192) {
  const shift = { front: 0, back: 3, left: 6, right: 9 }[view] ?? 0
  return encodePng(size, size, paint(size, size, (u, v) => {
    const inside = (u - 0.5) ** 2 / 0.05 + (v - 0.5) ** 2 / 0.16 < 1
    if (!inside) return [24, 26, 34, 255]
    const [r, g, b] = PALETTE[(Math.floor(v * 6) + shift) % PALETTE.length]
    return [r, g, b, 255]
  }))
}

/** Part thumbnail stand-in (`thumbs/<id>.webp`): a tinted disc on transparency, colour derived from the part id. */
export function thumbWebp(partId, size = 96) {
  let hash = 0
  for (const ch of partId) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0
  const [r, g, b] = PALETTE[hash % PALETTE.length]
  return twoColorWebp(size, size, (x, y) => ((x + 0.5) / size - 0.5) ** 2 + ((y + 0.5) / size - 0.5) ** 2 < 0.18, [r, g, b, 255], [0, 0, 0, 0])
}

/** Candidate thumbnail stand-in (`…/candidates/{i}/thumbnail`): the candidatePng silhouette in two colours. */
export function candidateThumbWebp(index, width = 168, height = 216) {
  const tint = PALETTE[(index * 5 + 3) % PALETTE.length]
  return twoColorWebp(width, height, (x, y) => {
    const u = (x + 0.5) / width, v = (y + 0.5) / height
    return (u - 0.5) ** 2 / 0.012 + (v - 0.2) ** 2 / 0.01 < 1 || (Math.abs(u - 0.5) < 0.16 + v * 0.08 && v > 0.3 && v < 0.92)
  }, [tint[0], tint[1], tint[2], 255], [205, 207, 212, 255])
}

// ------------------------------------------------------------------------------------------------
// Encoders without a dependency: CRC-32, 16-bit grayscale PNG, two-colour lossless WebP
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

/** CRC-32 (IEEE, as PNG and ZIP use it). */
export function crc32(buffer) {
  let c = 0xffffffff
  for (const byte of buffer) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8)
  return (c ^ 0xffffffff) >>> 0
}

function pngChunk(type, data) {
  const length = Buffer.alloc(4)
  length.writeUInt32BE(data.length)
  const body = Buffer.concat([Buffer.from(type, 'ascii'), data])
  const crc = Buffer.alloc(4)
  crc.writeUInt32BE(crc32(body))
  return Buffer.concat([length, body, crc])
}

/** 16-bit grayscale PNG (colour type 0), `value(x, y)` → 0..65535: the segmentation `labels.png` stand-in. */
export function gray16Png(width, height, value) {
  const stride = width * 2 + 1
  const raw = Buffer.alloc(stride * height)
  for (let y = 0; y < height; y++) for (let x = 0; x < width; x++) raw.writeUInt16BE(value(x, y) & 0xffff, y * stride + 1 + x * 2)
  const ihdr = Buffer.alloc(13)
  ihdr.writeUInt32BE(width, 0)
  ihdr.writeUInt32BE(height, 4)
  ihdr[8] = 16
  ihdr[9] = 0
  const signature = Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
  return Buffer.concat([signature, pngChunk('IHDR', ihdr), pngChunk('IDAT', deflateSync(raw)), pngChunk('IEND', Buffer.alloc(0))])
}

/**
 * Lossless WebP (VP8L) of a two-colour image: `pick(x, y)` true → colour `a`, false → colour `b` (RGBA 0..255).
 * No transforms, no colour cache, one prefix-code group of "simple" codes: a channel whose value is equal in both
 * colours has a one-symbol code (0 bits per pixel), any other channel a two-symbol code (1 bit; the smaller value is
 * code 0). Literal pixels are written green, red, blue, alpha (the VP8L order).
 */
export function twoColorWebp(width, height, pick, a, b) {
  const bytes = []
  let acc = 0
  let count = 0
  const put = (value, bits) => {
    for (let i = 0; i < bits; i++) {
      acc |= ((value >>> i) & 1) << count
      count += 1
      if (count === 8) {
        bytes.push(acc)
        acc = 0
        count = 0
      }
    }
  }
  const simpleCode = (s0, s1 = s0) => {
    put(1, 1) // simple code
    if (s0 === s1) {
      put(0, 1) // one symbol
      if (s0 < 2) {
        put(0, 1)
        put(s0, 1)
      } else {
        put(1, 1)
        put(s0, 8)
      }
      return
    }
    put(1, 1) // two symbols
    put(1, 1) // first symbol uses 8 bits
    put(s0, 8)
    put(s1, 8)
  }
  const channels = [1, 0, 2, 3] // green, red, blue, alpha
  put(0x2f, 8)
  put(width - 1, 14)
  put(height - 1, 14)
  put(a[3] !== 255 || b[3] !== 255 ? 1 : 0, 1)
  put(0, 3) // version
  put(0, 1) // no transform
  put(0, 1) // no colour cache
  put(0, 1) // no meta prefix codes
  for (const c of channels) simpleCode(a[c], b[c])
  simpleCode(0) // distance code (no backward references)
  for (let y = 0; y < height; y++) {
    for (let x = 0; x < width; x++) {
      const pixel = pick(x, y) ? a : b
      for (const c of channels) if (a[c] !== b[c]) put(pixel[c] === Math.max(a[c], b[c]) ? 1 : 0, 1)
    }
  }
  if (count) bytes.push(acc)
  const data = Buffer.from(bytes)
  const pad = data.length % 2
  const header = Buffer.alloc(20)
  header.write('RIFF', 0, 'ascii')
  header.writeUInt32LE(4 + 8 + data.length + pad, 4)
  header.write('WEBPVP8L', 8, 'ascii')
  header.writeUInt32LE(data.length, 16)
  return Buffer.concat([header, data, Buffer.alloc(pad)])
}
