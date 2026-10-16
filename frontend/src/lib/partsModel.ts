/**
 * Part results as the UI shows them (UI_DESIGN.md §6.1): one immutable `PartSetView` per completed part analysis,
 * built from the run payload (`result.parts`) and, when it loaded, the manifest `img3d.parts/1`.
 *
 * Normalisation rules (§6.1):
 * 1. Base data = `run.result.parts`; the manifest only adds parent / attach / host / symmetry, centroids, geometry
 *    facts, relations and warnings. Without a manifest the list is still complete (relations empty).
 * 2. `manifest.schema` must be major 1 (`img3d.parts/1…`), else `unsupported_manifest`.
 * 3. Part ids must match DESIGN §2.3 (≤ 80 chars), else `invalid_part_id`; duplicate ids are a hard reject
 *    (`duplicate_part_id`). At most `MAX_PARTS` parts are listed.
 * 4. `physical_deferred` → `deferred`; unknown kinds are skipped with a warning.
 * 5. The region attribute (`_FEATURE_ID_0`) is matched case-insensitively (GLTFLoader lower-cases custom names).
 * 6. URLs are copied from the payload; nothing is constructed (UI9).
 * 7. Regions / deferred parts with label value 0 or a `(host, value)` pair shared with another region / deferred part
 *    get `highlight: 'shared_label'`: the viewer cannot tell their faces apart, so hide / isolate / highlight are off.
 *
 * When the manifest is rejected (`unsupported_manifest`, or bad ids inside it), the caller normalises again with
 * `manifest = null` to keep the list ("Some details could not be read").
 *
 * PURE (UI_DESIGN §1.5): relative `.ts` value imports only; no React, zustand, axios or browser globals.
 */
import type { PartAnalysisOut, RelationOut } from '../api/partAwareTypes.ts'
import type { FeaturesSnapshot } from './featureFlags.ts'
import {
  OCCLUDED_GEOMETRY_NOTE,
  PART_GROUP_FALLBACK_LABELS,
  PART_GROUP_ORDER,
  type PartDisplayGroup,
} from './featureMeta.ts'

export type PartViewKind = 'base' | 'physical' | 'deferred' | 'region'
export type PartSide = 'left' | 'right' | 'center'

export interface PartGeometryFacts {
  /** Manifest `geometry.status` (e.g. `open_surface`). */
  readonly status: string
  readonly openBoundaryLoops: number
  readonly watertight: boolean
}

export interface PartView {
  readonly id: string
  readonly label: string
  readonly category: string
  readonly group: string
  readonly kind: PartViewKind
  readonly side: PartSide | null
  readonly parentId: string | null
  readonly attachToId: string | null
  readonly hostId: string | null
  readonly symmetricWithId: string | null
  /** `part:<id>` for base and physical parts; `null` for regions and deferred parts. */
  readonly nodeName: string | null
  /** `_FEATURE_ID_0` value on the host node (every kind). */
  readonly regionValue: number | null
  readonly faces: number
  readonly confidence: number | null
  readonly centroid: readonly [number, number, number] | null
  readonly modelUrl: string | null
  readonly downloadUrl: string | null
  readonly maskUrl: string | null
  readonly thumbnailUrl: string | null
  /** `/features` `separable_groups[group].note` (or the region group's note). */
  readonly groupNote: string | null
  /** 'Hidden geometry not generated' when `geometry.occluded_geometry_missing`. */
  readonly geometryNote: string | null
  /** Base record only: `geometry.floaters.length` (0 elsewhere and without a manifest). */
  readonly floaterCount: number
  /** Normalisation rule 7. */
  readonly highlight: 'ok' | 'shared_label'
  readonly refinement: Readonly<{ status: string; locked: boolean; eligible: boolean; needs: readonly string[] }>
  /** Manifest `expected` ("Asked for in the prompt"); `false` without a manifest. */
  readonly expected: boolean
  /** Manifest geometry facts for the inspector; `null` without a manifest. */
  readonly geometry: PartGeometryFacts | null
}

export interface PartSetWarning {
  readonly code: string
  readonly message: string
  readonly partId: string | null
}

export interface PartSetView {
  readonly analysisId: string
  readonly jobId: string
  readonly status: 'completed' | 'partial'
  readonly regionAttribute: string
  /** Payload order (`result.parts`); use `displayOrder` / `groupsForDisplay` for the list order. */
  readonly parts: readonly PartView[]
  readonly byId: Readonly<Record<string, PartView>>
  readonly relations: readonly RelationOut[]
  readonly warnings: readonly PartSetWarning[]
  readonly counts: Readonly<{ parts: number; physical: number; deferred: number; regions: number }>
  readonly quality: Readonly<{
    cameraFitIou: number | null
    unlabelledFaceRatio: number
    partnerFilledRatio: number
    cutPartnerConsistency: number | null
  }>
  readonly urls: Readonly<{ assembled: string; download: string; zip: string; manifest: string; overlay: string | null }>
  /** The manifest was read; `false` → the inspector shows "Details unavailable". */
  readonly hasManifest: boolean
  /** Manifest `expectations.missing`: classes asked for in the prompt that were not found. */
  readonly missingExpected: readonly string[]
}

export type NormalizeError = 'unsupported_manifest' | 'duplicate_part_id' | 'invalid_part_id' | 'malformed'
export type NormalizeResult = { ok: true; set: PartSetView } | { ok: false; error: NormalizeError }

/** DESIGN §2.3 part id rule. */
export const PART_ID_PATTERN = /^[a-z][a-z0-9_]*(\.[a-z0-9_]+){0,5}$/
export const PART_ID_MAX_CHARS = 80
/** Upper bound of listed parts (DESIGN `limits.max_parts` is 256; the extra room tolerates future backends). */
export const MAX_PARTS = 512
/** Number of `--part-<n>` colours. */
export const PART_COLOR_COUNT = 12

const MANIFEST_SCHEMA = /^img3d\.parts\/1(?:$|[^0-9])/
const SIDES: readonly PartSide[] = ['left', 'right', 'center']

type Json = Record<string, unknown>

const isObject = (value: unknown): value is Json => typeof value === 'object' && value !== null && !Array.isArray(value)
const str = (value: unknown): string | null => (typeof value === 'string' ? value : null)
const nonEmpty = (value: unknown): string | null => (typeof value === 'string' && value !== '' ? value : null)
const finite = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)
const count = (value: unknown): number => {
  const n = finite(value)
  return n !== null && n >= 0 ? Math.floor(n) : 0
}
const side = (value: unknown): PartSide | null =>
  typeof value === 'string' && (SIDES as readonly string[]).includes(value) ? (value as PartSide) : null
const strings = (value: unknown): string[] => (Array.isArray(value) ? value.filter((v): v is string => typeof v === 'string') : [])

export function isValidPartId(id: unknown): id is string {
  return typeof id === 'string' && id.length <= PART_ID_MAX_CHARS && PART_ID_PATTERN.test(id)
}

function viewKind(kind: unknown): PartViewKind | null {
  switch (kind) {
    case 'base':
    case 'physical':
    case 'region':
      return kind
    case 'physical_deferred':
      return 'deferred'
    default:
      return null
  }
}

function centroidOf(value: unknown): readonly [number, number, number] | null {
  if (!Array.isArray(value) || value.length !== 3) return null
  const [x, y, z] = value.map(finite)
  return x !== null && y !== null && z !== null ? [x, y, z] : null
}

/** Manifest relation entries with the fields the UI reads; malformed entries are dropped. */
function readRelations(value: unknown): RelationOut[] {
  const out: RelationOut[] = []
  for (const item of Array.isArray(value) ? value : []) {
    if (!isObject(item)) continue
    const id = str(item.id)
    const type = str(item.type)
    const a = str(item.a)
    const b = str(item.b)
    if (id === null || type === null || a === null || b === null) continue
    out.push({
      id,
      type,
      a,
      b,
      via: str(item.via),
      metrics: isObject(item.metrics) ? { ...item.metrics } : {},
      source: str(item.source) ?? '',
      confidence: finite(item.confidence) ?? 0,
    })
  }
  return out
}

function readWarnings(value: unknown, partKey: 'part_id' | null): PartSetWarning[] {
  const out: PartSetWarning[] = []
  for (const item of Array.isArray(value) ? value : []) {
    if (!isObject(item)) continue
    const code = str(item.code)
    if (code === null) continue
    out.push({ code, message: str(item.message) ?? '', partId: partKey ? str(item[partKey]) : null })
  }
  return out
}

/** Manifest part records by id; an invalid or duplicate id rejects the whole manifest. */
function indexManifestParts(value: unknown): { ok: true; byId: Map<string, Json> } | { ok: false; error: NormalizeError } {
  const byId = new Map<string, Json>()
  for (const item of Array.isArray(value) ? value : []) {
    if (!isObject(item)) continue
    if (!isValidPartId(item.id)) return { ok: false, error: 'invalid_part_id' }
    if (byId.has(item.id)) return { ok: false, error: 'duplicate_part_id' }
    byId.set(item.id, item)
  }
  return { ok: true, byId }
}

function groupNoteOf(group: string, f: FeaturesSnapshot): string | null {
  const info = f.parts.separableGroups.find((g) => g.id === group) ?? f.parts.regionGroups.find((g) => g.id === group)
  return info?.note ?? null
}

/** Rule 7: ids of region / deferred parts whose surface label cannot be told apart from another part's. */
function sharedLabelIds(parts: readonly { id: string; kind: PartViewKind; hostId: string | null; regionValue: number | null }[]): Set<string> {
  const byKey = new Map<string, string[]>()
  const shared = new Set<string>()
  for (const p of parts) {
    if (p.kind !== 'region' && p.kind !== 'deferred') continue
    if (p.regionValue === null) continue
    if (p.regionValue === 0) {
      shared.add(p.id)
      continue
    }
    const key = `${p.hostId ?? ''}\u0000${p.regionValue}`
    const ids = byKey.get(key)
    if (ids) ids.push(p.id)
    else byKey.set(key, [p.id])
  }
  for (const ids of byKey.values()) if (ids.length > 1) for (const id of ids) shared.add(id)
  return shared
}

/**
 * Run payload (+ manifest JSON, or `null` when it is not loaded / failed to load) → the part set view.
 * Never throws; the result object is not frozen (stores keep it as an immutable reference).
 */
export function normalizePartResult(run: PartAnalysisOut, manifest: unknown | null, f: FeaturesSnapshot): NormalizeResult {
  const runJson: unknown = run
  if (!isObject(runJson) || !isObject(runJson.result)) return { ok: false, error: 'malformed' }
  const result = runJson.result
  const summaries: unknown = result.parts
  if (!Array.isArray(summaries)) return { ok: false, error: 'malformed' }

  let records = new Map<string, Json>()
  let manifestJson: Json | null = null
  if (manifest !== null && manifest !== undefined) {
    if (!isObject(manifest) || typeof manifest.schema !== 'string' || !MANIFEST_SCHEMA.test(manifest.schema)) {
      return { ok: false, error: 'unsupported_manifest' }
    }
    const indexed = indexManifestParts(manifest.parts)
    if (!indexed.ok) return indexed
    records = indexed.byId
    manifestJson = manifest
  }

  const warnings: PartSetWarning[] = []
  const seen = new Set<string>()
  const drafts: Omit<PartView, 'highlight'>[] = []
  for (const item of summaries as unknown[]) {
    if (!isObject(item)) return { ok: false, error: 'malformed' }
    const id = item.id
    if (!isValidPartId(id)) return { ok: false, error: 'invalid_part_id' }
    if (seen.has(id)) return { ok: false, error: 'duplicate_part_id' }
    seen.add(id)
    const kind = viewKind(item.kind)
    if (kind === null) {
      warnings.push({ code: 'unknown_part_kind', message: `Part "${id}" has an unknown kind and is not listed.`, partId: id })
      continue
    }
    if (drafts.length >= MAX_PARTS) continue

    const record = records.get(id) ?? null
    const geometry = record && isObject(record.geometry) ? record.geometry : null
    const stats = record && isObject(record.stats) ? record.stats : null
    const region = record && isObject(record.region) ? record.region : null
    const refinement = isObject(item.refinement) ? item.refinement : {}
    const parent = record ? nonEmpty(record.parent) : null
    const group = str(item.group) ?? (record ? str(record.group) : null) ?? 'other'
    const mesh = kind === 'base' || kind === 'physical'

    drafts.push({
      id,
      label: nonEmpty(item.label) ?? (record ? nonEmpty(record.label) : null) ?? id,
      category: str(item.category) ?? (record ? str(record.category) : null) ?? id,
      group,
      kind,
      side: side(item.side) ?? (record ? side(record.side) : null),
      parentId: parent === id ? null : parent, // the base record names itself as parent
      attachToId: record ? nonEmpty(record.attach_to) : null,
      hostId: record ? nonEmpty(record.host) : null,
      symmetricWithId: record ? nonEmpty(record.symmetric_with) : null,
      nodeName: mesh ? nonEmpty(item.node_name) ?? (record ? nonEmpty(record.node_name) : null) : null,
      regionValue: finite(item.region_value) ?? (region ? finite(region.value) : null),
      faces: count(item.faces),
      confidence: finite(item.confidence),
      centroid: stats ? centroidOf(stats.centroid) : null,
      modelUrl: nonEmpty(item.model_url),
      downloadUrl: nonEmpty(item.download_url),
      maskUrl: nonEmpty(item.mask_url),
      thumbnailUrl: nonEmpty(item.thumbnail_url),
      groupNote: groupNoteOf(group, f),
      geometryNote: geometry && geometry.occluded_geometry_missing === true ? OCCLUDED_GEOMETRY_NOTE : null,
      // A missing `floaters` key counts as an empty list (DESIGN §3.10.1).
      floaterCount: kind === 'base' && geometry && Array.isArray(geometry.floaters) ? geometry.floaters.length : 0,
      refinement: {
        status: str(refinement.status) ?? 'untouched',
        locked: refinement.locked !== false,
        eligible: refinement.eligible === true,
        needs: strings(refinement.needs),
      },
      expected: record ? record.expected === true : false,
      geometry: geometry
        ? {
            status: str(geometry.status) ?? 'unknown',
            openBoundaryLoops: count(geometry.open_boundary_loops),
            watertight: geometry.watertight === true,
          }
        : null,
    })
  }
  if (summaries.length > MAX_PARTS) {
    warnings.push({ code: 'parts_truncated', message: `Only the first ${MAX_PARTS} parts are listed.`, partId: null })
  }

  const shared = sharedLabelIds(drafts)
  const parts: PartView[] = drafts.map((d) => ({ ...d, highlight: shared.has(d.id) ? 'shared_label' : 'ok' }))
  const byId: Record<string, PartView> = {}
  for (const p of parts) byId[p.id] = p

  // Manifest warnings carry the part id; run warnings (step-scoped) are added when their code is not listed yet.
  const runWarnings = readWarnings(runJson.warnings, null)
  const manifestWarnings = manifestJson ? readWarnings(manifestJson.warnings, 'part_id') : []
  const listed = new Set(manifestWarnings.map((w) => w.code))
  const allWarnings = [...manifestWarnings, ...runWarnings.filter((w) => !listed.has(w.code)), ...warnings]

  const regionAttribute = nonEmpty(result.region_attribute) ?? (manifestJson ? nonEmpty(manifestJson.region_attribute) : null) ?? ''
  const expectations = manifestJson && isObject(manifestJson.expectations) ? manifestJson.expectations : null
  const status = result.status === 'partial' ? 'partial' : 'completed'

  return {
    ok: true,
    set: {
      analysisId: str(runJson.id) ?? '',
      jobId: str(runJson.job_id) ?? (manifestJson ? str(manifestJson.job_id) : null) ?? '',
      status,
      regionAttribute,
      parts,
      byId,
      relations: manifestJson ? readRelations(manifestJson.relations) : [],
      warnings: allWarnings,
      counts: {
        parts: finite(result.part_count) ?? parts.filter((p) => p.kind === 'physical' || p.kind === 'deferred').length,
        physical: finite(result.physical_count) ?? parts.filter((p) => p.kind === 'physical').length,
        deferred: finite(result.deferred_count) ?? parts.filter((p) => p.kind === 'deferred').length,
        regions: finite(result.region_count) ?? parts.filter((p) => p.kind === 'region').length,
      },
      quality: {
        cameraFitIou: finite(result.camera_fit_iou),
        unlabelledFaceRatio: finite(result.unlabelled_face_ratio) ?? 0,
        partnerFilledRatio: finite(result.partner_filled_ratio) ?? 0,
        cutPartnerConsistency: finite(result.cut_partner_consistency),
      },
      urls: {
        assembled: str(result.assembled_url) ?? '',
        download: str(result.download_url) ?? '',
        zip: str(result.zip_url) ?? '',
        manifest: str(result.manifest_url) ?? '',
        overlay: nonEmpty(result.overlay_url),
      },
      hasManifest: manifestJson !== null,
      missingExpected: expectations ? strings(expectations.missing) : [],
    },
  }
}

/**
 * The geometry attribute that carries the region labels: `names` are the attribute names of a loaded geometry
 * (GLTFLoader lower-cases custom names, `_FEATURE_ID_0` → `_feature_id_0`), matched case-insensitively.
 */
export function findRegionAttribute(names: Iterable<string>, regionAttribute: string): string | null {
  if (!regionAttribute) return null
  const wanted = regionAttribute.toLowerCase()
  for (const name of names) if (name.toLowerCase() === wanted) return name
  return null
}

// ------------------------------------------------------------------------------------------------
// Display helpers
// ------------------------------------------------------------------------------------------------

const GROUP_RANK: ReadonlyMap<string, number> = new Map(PART_GROUP_ORDER.map((group, index) => [group, index]))

/** List group of a part: `base` for the base record, its own group when known, else `other`. */
export function displayGroupOf(p: PartView): PartDisplayGroup {
  if (p.kind === 'base') return 'base'
  return p.group !== 'base' && GROUP_RANK.has(p.group) ? (p.group as PartDisplayGroup) : 'other'
}

/** Parts in list order: group order of UI_DESIGN §6.4, payload order inside a group. */
export function displayOrder(s: PartSetView): readonly PartView[] {
  const rank = (p: PartView): number => GROUP_RANK.get(displayGroupOf(p)) ?? GROUP_RANK.size
  return s.parts
    .map((part, index) => ({ part, index }))
    .sort((a, b) => rank(a.part) - rank(b.part) || a.index - b.index)
    .map((entry) => entry.part)
}

export interface PartGroupView {
  readonly group: PartDisplayGroup
  readonly label: string
  readonly parts: readonly PartView[]
}

/** Collapsible list groups in display order (empty groups omitted); labels come from `/features` when it has them. */
export function groupsForDisplay(s: PartSetView, f: FeaturesSnapshot): readonly PartGroupView[] {
  const buckets = new Map<PartDisplayGroup, PartView[]>()
  for (const part of displayOrder(s)) {
    const group = displayGroupOf(part)
    const bucket = buckets.get(group)
    if (bucket) bucket.push(part)
    else buckets.set(group, [part])
  }
  const labelOf = (group: PartDisplayGroup): string => {
    if (group === 'base' || group === 'other') return PART_GROUP_FALLBACK_LABELS[group]
    const info = f.parts.separableGroups.find((g) => g.id === group) ?? f.parts.regionGroups.find((g) => g.id === group)
    return info?.label || PART_GROUP_FALLBACK_LABELS[group]
  }
  return [...buckets].map(([group, parts]) => ({ group, label: labelOf(group), parts }))
}

/** Depth-first tree by `parentId` (roots: no parent or an unknown parent); siblings keep payload order. */
export function hierarchy(s: PartSetView): readonly { part: PartView; depth: number }[] {
  const children = new Map<string, PartView[]>()
  const roots: PartView[] = []
  for (const part of s.parts) {
    const parent = part.parentId
    if (parent !== null && parent !== part.id && Object.hasOwn(s.byId, parent)) {
      const list = children.get(parent)
      if (list) list.push(part)
      else children.set(parent, [part])
    } else {
      roots.push(part)
    }
  }
  const out: { part: PartView; depth: number }[] = []
  const visited = new Set<string>()
  const visit = (part: PartView, depth: number): void => {
    if (visited.has(part.id)) return
    visited.add(part.id)
    out.push({ part, depth })
    for (const child of children.get(part.id) ?? []) visit(child, depth + 1)
  }
  for (const root of roots) visit(root, 0)
  // Parts on a parent cycle have no root; list them at the top level instead of dropping them.
  for (const part of s.parts) visit(part, 0)
  return out
}

export interface PartRelationView {
  readonly type: string
  readonly otherId: string
  /** `out`: this part is `a` ("hair attached_to face"); `in`: this part is `b`. */
  readonly direction: 'out' | 'in'
}

/** Relations that involve `id`, in manifest order. */
export function relationsOf(s: PartSetView, id: string): readonly PartRelationView[] {
  const out: PartRelationView[] = []
  for (const r of s.relations) {
    if (r.a === id) out.push({ type: r.type, otherId: r.b, direction: 'out' })
    else if (r.b === id) out.push({ type: r.type, otherId: r.a, direction: 'in' })
  }
  return out
}

export type PartDisplayState = 'normal' | 'dimmed' | 'highlighted' | 'selected' | 'hidden'

/** The part view fields of `store/partStore.ts → PartViewState` that decide display states. */
export interface PartSelection {
  readonly hidden: readonly string[]
  readonly isolated: string | null
  readonly selected: string | null
  readonly hovered: string | null
}

/**
 * Display state per part id (first match): `hidden` = hidden by the user; `dimmed` = outside the isolated part (not
 * drawn by the viewer; the list greys the row but keeps its own toggle state); `selected`; `highlighted` = hovered;
 * otherwise `normal`. An isolated id that is not in the set isolates nothing.
 */
export function displayStates(s: PartSetView, v: PartSelection): Readonly<Record<string, PartDisplayState>> {
  const hidden = new Set(v.hidden)
  const isolated = v.isolated !== null && Object.hasOwn(s.byId, v.isolated) ? v.isolated : null
  const out: Record<string, PartDisplayState> = {}
  for (const { id } of s.parts) {
    if (hidden.has(id)) out[id] = 'hidden'
    else if (isolated !== null && id !== isolated) out[id] = 'dimmed'
    else if (id === v.selected) out[id] = 'selected'
    else if (id === v.hovered) out[id] = 'highlighted'
    else out[id] = 'normal'
  }
  return out
}

/** Stable colour slot 1..12 (`--part-<n>`) by display order; 0 for an id that is not in the set. */
export function partColorIndex(s: PartSetView, id: string): number {
  const index = displayOrder(s).findIndex((p) => p.id === id)
  return index < 0 ? 0 : (index % PART_COLOR_COUNT) + 1
}

/** `partColorIndex` for every part at once (one sort instead of one per row; cache it per set). */
export function partColorIndices(s: PartSetView): Readonly<Record<string, number>> {
  const out: Record<string, number> = {}
  displayOrder(s).forEach((p, index) => {
    out[p.id] = (index % PART_COLOR_COUNT) + 1
  })
  return out
}
