/**
 * three.js side of the parts view (UI_DESIGN.md §6.6): binding a `PartSetView` onto the loaded parts GLB, one draw
 * index per region host (hide / isolate), highlight / select / colorize overlays, explode, picking clones and the
 * part bounding boxes for `focusPart`. Lives in the lazy viewer chunk only.
 *
 * Node matching: `object.userData.img3d.id` (glTF node extras), else `object.userData.name === nodeName` (GLTFLoader
 * sanitises `object.name`, which is never used). `_FEATURE_ID_0` is an `InterleavedBufferAttribute` (byteStride 4):
 * read once per host with `getX`, never through `.array`.
 *
 * Buffer ownership: a host keeps ONE geometry; its original index stays on the CPU (subsets, pick clones, outlines)
 * and a dynamic copy is installed as the draw index. Region overlays and pick clones share the host's `position`
 * attribute and drop it (`deleteAttribute`) before their own geometry is disposed, so the host's GL buffers survive.
 */
import {
  Box3,
  BufferAttribute,
  BufferGeometry,
  Color,
  DoubleSide,
  DynamicDrawUsage,
  EdgesGeometry,
  Group,
  LineBasicMaterial,
  LineSegments,
  Mesh,
  MeshBasicMaterial,
  Vector3,
  type Camera,
  type InterleavedBufferAttribute,
  type Object3D,
  type Raycaster,
  type Vector2,
} from 'three'

import {
  PART_COLOR_COUNT,
  displayStates,
  findRegionAttribute,
  partColorIndices,
  type PartSelection,
  type PartSetView,
} from '../../lib/partsModel'
import {
  LruCache,
  explodeOffset,
  faceLabels,
  filterFaces,
  keepLabel,
  subsetsByLabel,
  visibilityKey,
} from './partsIndex'
import { PART_OVERLAY_FLAG } from './sceneUtils'
import type { PartsIndexReport } from './viewerRuntime'
import type { ViewerPartsBinding } from './viewerTypes'

type Vec3 = readonly [number, number, number]

/** Selected physical parts above this face count get no outline (overlay only). */
const OUTLINE_MAX_FACES = 300_000
const OUTLINE_THRESHOLD_DEG = 30

// ------------------------------------------------------------------------------------------------
// Binding
// ------------------------------------------------------------------------------------------------

const bindings = new WeakMap<PartSetView, ViewerPartsBinding>()
const colorIndexCache = new WeakMap<PartSetView, Readonly<Record<string, number>>>()

/** Viewer binding of a part set (cached per set). Regions without a label value get `value: -1` (never matched). */
export function bindingFor(set: PartSetView): ViewerPartsBinding {
  const cached = bindings.get(set)
  if (cached) return cached
  const baseId = set.parts.find((part) => part.kind === 'base')?.id ?? ''
  const physical: { partId: string; nodeName: string }[] = []
  const regions: { partId: string; value: number; hostId: string; shared: boolean }[] = []
  for (const part of set.parts) {
    if (part.kind === 'base' || part.kind === 'physical') {
      physical.push({ partId: part.id, nodeName: part.nodeName ?? `part:${part.id}` })
    } else {
      regions.push({
        partId: part.id,
        value: part.regionValue ?? -1,
        hostId: part.hostId ?? baseId,
        shared: part.highlight === 'shared_label',
      })
    }
  }
  const binding: ViewerPartsBinding = { physical, regions }
  bindings.set(set, binding)
  return binding
}

function colorIndicesFor(set: PartSetView): Readonly<Record<string, number>> {
  let cached = colorIndexCache.get(set)
  if (!cached) {
    cached = partColorIndices(set)
    colorIndexCache.set(set, cached)
  }
  return cached
}

// ------------------------------------------------------------------------------------------------
// Hosts (one draw index per region host)
// ------------------------------------------------------------------------------------------------

export interface HostState {
  readonly mesh: Mesh
  /** Full original index (CPU only: subsets, pick clone, outline). */
  readonly original: Uint32Array
  /** Original index attribute; never uploaded (pick clone + outline source). */
  readonly originalAttr: BufferAttribute
  /** Dynamic copy installed as the host's index. */
  readonly draw: BufferAttribute
  /** One region label per face; `null` = the mesh has no region attribute. */
  readonly labels: Uint16Array | null
  /** Visibility key of what `draw` holds (`''` = every face). */
  key: string
  /** Whether a face label is drawn; `null` = every face. */
  visibleLabel: ((label: number) => boolean) | null
  readonly subsets: LruCache<string, Uint32Array>
  /** Faces per region value (built in one pass on first use; bounded by one copy of the index). */
  valueSubsets: Map<number, Uint32Array> | null
}

const hostStates = new WeakMap<Mesh, HostState>()

/** Copy of a (possibly interleaved, byteStride 4) per-vertex label attribute, read with `getX`. */
export function readFeatureIds(attr: BufferAttribute | InterleavedBufferAttribute): Uint16Array {
  const out = new Uint16Array(attr.count)
  for (let i = 0; i < attr.count; i++) out[i] = attr.getX(i)
  return out
}

/** Host state of a mesh (idempotent; cached per mesh, so a remount of the cached GLTF reuses it). */
export function hostStateFor(mesh: Mesh, regionAttribute: string): HostState {
  const cached = hostStates.get(mesh)
  if (cached) return cached
  const geometry = mesh.geometry
  const position = geometry.getAttribute('position')
  let originalAttr = geometry.index
  if (!originalAttr) {
    const sequential = new Uint32Array(position ? position.count : 0)
    for (let i = 0; i < sequential.length; i++) sequential[i] = i
    originalAttr = new BufferAttribute(sequential, 1)
  }
  const source = originalAttr.array
  const original = source instanceof Uint32Array ? source : Uint32Array.from(source as ArrayLike<number>)
  const name = findRegionAttribute(Object.keys(geometry.attributes), regionAttribute)
  const labels = name ? faceLabels(original, readFeatureIds(geometry.getAttribute(name))) : null
  const draw = new BufferAttribute(Uint32Array.from(original), 1)
  draw.setUsage(DynamicDrawUsage)
  const state: HostState = {
    mesh,
    original,
    originalAttr,
    draw,
    labels,
    key: '',
    visibleLabel: null,
    subsets: new LruCache(8),
    valueSubsets: null,
  }
  hostStates.set(mesh, state)
  return state
}

/** Install the dynamic draw index (idempotent; run before the parts model's first frame). */
export function prepareHost(host: HostState): void {
  if (host.mesh.geometry.index !== host.draw) host.mesh.geometry.setIndex(host.draw)
}

function writeDraw(host: HostState, faces: Uint32Array): void {
  const draw = host.draw
  ;(draw.array as Uint32Array).set(faces)
  draw.clearUpdateRanges()
  draw.addUpdateRange(0, faces.length)
  draw.needsUpdate = true
  host.mesh.geometry.setDrawRange(0, faces === host.original ? Infinity : faces.length)
}

/** Draw only the faces whose labels pass (hidden values / isolated label); cached per visibility key. */
export function setHostFaces(host: HostState, hiddenValues: readonly number[], only: number | null): void {
  const key = host.labels ? visibilityKey(hiddenValues, only) : ''
  if (key === host.key) return
  host.key = key
  prepareHost(host)
  if (key === '' || !host.labels) {
    host.visibleLabel = null
    writeDraw(host, host.original)
    return
  }
  const hidden = new Set(hiddenValues)
  const keep = (label: number): boolean => keepLabel(label, hidden, only)
  let faces = host.subsets.get(key)
  if (!faces) {
    faces = filterFaces(host.original, host.labels, keep)
    host.subsets.set(key, faces)
  }
  host.visibleLabel = keep
  writeDraw(host, faces)
}

function valueSubset(host: HostState, value: number, allValues: Iterable<number>): Uint32Array | null {
  if (!host.labels) return null
  if (!host.valueSubsets) host.valueSubsets = subsetsByLabel(host.original, host.labels, allValues)
  return host.valueSubsets.get(value) ?? null
}

// ------------------------------------------------------------------------------------------------
// Index
// ------------------------------------------------------------------------------------------------

interface RegionEntry {
  readonly hostId: string
  readonly value: number
  readonly shared: boolean
}

export interface PartsIndex {
  readonly set: PartSetView
  readonly root: Object3D
  /** Matched base / physical parts → their mesh node. */
  readonly nodes: ReadonlyMap<string, Mesh>
  readonly partOfMesh: ReadonlyMap<Mesh, string>
  /** Hosts of at least one matched region whose mesh carries the region attribute. */
  readonly hosts: ReadonlyMap<string, HostState>
  readonly hostOfMesh: ReadonlyMap<Mesh, { hostId: string; state: HostState }>
  /** Matched, labelled regions / deferred parts. */
  readonly regions: ReadonlyMap<string, RegionEntry>
  /** host id → label value → the region id that picking reports (`highlight: 'ok'` only). */
  readonly regionAt: ReadonlyMap<string, ReadonlyMap<number, string>>
  /** Region values per host (for the one-pass subsets). */
  readonly hostValues: ReadonlyMap<string, readonly number[]>
  readonly centroids: ReadonlyMap<string, Vec3>
  readonly assetCentroid: Vec3
  /** Region bounding boxes in host-local space (computed once per part). */
  readonly regionBoxes: Map<string, Box3>
  readonly report: PartsIndexReport
}

const originalPositions = new WeakMap<Object3D, Vector3>()

function originalPosition(object: Object3D): Vector3 {
  let origin = originalPositions.get(object)
  if (!origin) {
    origin = object.position.clone()
    originalPositions.set(object, origin)
  }
  return origin
}

function meshOf(object: Object3D): Mesh | null {
  if ((object as Mesh).isMesh) return object as Mesh
  let found: Mesh | null = null
  object.traverse((child) => {
    if (!found && (child as Mesh).isMesh && !child.userData[PART_OVERLAY_FLAG]) found = child as Mesh
  })
  return found
}

function localCentroid(mesh: Mesh): Vec3 {
  const geometry = mesh.geometry
  if (!geometry.boundingBox) geometry.computeBoundingBox()
  const center = (geometry.boundingBox as Box3).getCenter(new Vector3()).add(originalPosition(mesh))
  return [center.x, center.y, center.z]
}

/** One traversal: node matching, region hosts (labels per face), centroids and the report for `viewerRuntime`. */
export function buildPartsIndex(root: Object3D, set: PartSetView, regionAttribute: string): PartsIndex {
  const binding = bindingFor(set)
  const byId = new Map<string, Object3D>()
  const byName = new Map<string, Object3D>()
  root.traverse((object) => {
    if (object.userData[PART_OVERLAY_FLAG]) return
    const img3d: unknown = object.userData.img3d
    const id = typeof img3d === 'object' && img3d !== null ? (img3d as { id?: unknown }).id : undefined
    if (typeof id === 'string' && !byId.has(id)) byId.set(id, object)
    const name: unknown = object.userData.name
    if (typeof name === 'string' && !byName.has(name)) byName.set(name, object)
  })

  const unmatched: string[] = []
  const regionUnavailable: string[] = []
  const nodes = new Map<string, Mesh>()
  const partOfMesh = new Map<Mesh, string>()
  for (const { partId, nodeName } of binding.physical) {
    const object = byId.get(partId) ?? byName.get(nodeName)
    const mesh = object ? meshOf(object) : null
    if (!mesh || partOfMesh.has(mesh)) {
      unmatched.push(partId)
      continue
    }
    nodes.set(partId, mesh)
    partOfMesh.set(mesh, partId)
  }

  const hosts = new Map<string, HostState>()
  const hostOfMesh = new Map<Mesh, { hostId: string; state: HostState }>()
  const regions = new Map<string, RegionEntry>()
  const regionAt = new Map<string, Map<number, string>>()
  const hostValues = new Map<string, number[]>()
  let checkedHosts = 0
  let hostsWithLabels = 0
  const checked = new Set<string>()
  for (const region of binding.regions) {
    const hostMesh = nodes.get(region.hostId)
    if (!hostMesh || region.value < 0) {
      unmatched.push(region.partId)
      continue
    }
    if (!checked.has(region.hostId)) {
      checked.add(region.hostId)
      checkedHosts++
      const state = hostStateFor(hostMesh, regionAttribute)
      if (state.labels) {
        hostsWithLabels++
        hosts.set(region.hostId, state)
        hostOfMesh.set(hostMesh, { hostId: region.hostId, state })
      }
    }
    if (!hosts.has(region.hostId)) {
      regionUnavailable.push(region.partId)
      continue
    }
    regions.set(region.partId, { hostId: region.hostId, value: region.value, shared: region.shared })
    const values = hostValues.get(region.hostId) ?? []
    values.push(region.value)
    hostValues.set(region.hostId, values)
    if (!region.shared) {
      const byValue = regionAt.get(region.hostId) ?? new Map<number, string>()
      if (!byValue.has(region.value)) byValue.set(region.value, region.partId)
      regionAt.set(region.hostId, byValue)
    }
  }

  const centroids = new Map<string, Vec3>()
  const assetBox = new Box3()
  for (const [partId, mesh] of nodes) {
    const centroid = set.byId[partId]?.centroid ?? localCentroid(mesh)
    centroids.set(partId, centroid)
    const geometry = mesh.geometry
    if (!geometry.boundingBox) geometry.computeBoundingBox()
    assetBox.union((geometry.boundingBox as Box3).clone().translate(originalPosition(mesh)))
  }
  const base = set.parts.find((part) => part.kind === 'base')
  const assetCenter = assetBox.isEmpty() ? new Vector3() : assetBox.getCenter(new Vector3())
  const assetCentroid: Vec3 = base?.centroid ?? [assetCenter.x, assetCenter.y, assetCenter.z]

  return {
    set,
    root,
    nodes,
    partOfMesh,
    hosts,
    hostOfMesh,
    regions,
    regionAt,
    hostValues,
    centroids,
    assetCentroid,
    regionBoxes: new Map(),
    report: {
      analysisId: set.analysisId,
      unmatched,
      regionUnavailable,
      hasRegionAttribute: checkedHosts === 0 ? null : hostsWithLabels === checkedHosts,
    },
  }
}

/** Install every host's draw index (layout effect, before the first frame of the parts model). */
export function prepareParts(index: PartsIndex): void {
  for (const host of index.hosts.values()) prepareHost(host)
}

/** Back to the unmodified scene: every node visible, full draw index, original positions (the cached GLTF stays valid). */
export function restoreParts(index: PartsIndex): void {
  for (const mesh of index.nodes.values()) {
    mesh.visible = true
    const origin = originalPositions.get(mesh)
    if (origin) mesh.position.copy(origin)
  }
  for (const host of index.hosts.values()) setHostFaces(host, [], null)
}

// ------------------------------------------------------------------------------------------------
// Visibility, explode
// ------------------------------------------------------------------------------------------------

function isolatedTarget(index: PartsIndex, isolated: string | null): { node: string | null; region: RegionEntry | null } {
  if (isolated === null) return { node: null, region: null }
  if (index.nodes.has(isolated)) return { node: isolated, region: null }
  const region = index.regions.get(isolated)
  return { node: null, region: region && !region.shared ? region : null }
}

/** Hide / isolate: `object.visible` for nodes, the draw index for regions (wireframe, clay and normals follow). */
export function applyVisibility(index: PartsIndex, sel: PartSelection): void {
  const hidden = new Set(sel.hidden)
  const iso = isolatedTarget(index, sel.isolated)
  for (const [partId, mesh] of index.nodes) {
    let visible = !hidden.has(partId)
    if (iso.node !== null) visible &&= partId === iso.node
    else if (iso.region) visible &&= partId === iso.region.hostId
    mesh.visible = visible
  }
  for (const [hostId, host] of index.hosts) {
    const hiddenValues: number[] = []
    for (const [regionId, region] of index.regions) {
      if (region.hostId === hostId && !region.shared && hidden.has(regionId)) hiddenValues.push(region.value)
    }
    setHostFaces(host, hiddenValues, iso.region && iso.region.hostId === hostId ? iso.region.value : null)
  }
}

/** Physical nodes move by `explodeOffset`; regions never move; `amount = 0` restores the stored positions exactly. */
export function applyExplode(index: PartsIndex, amount: number): void {
  for (const [partId, mesh] of index.nodes) {
    if (index.set.byId[partId]?.kind !== 'physical') continue
    const origin = originalPosition(mesh)
    if (amount === 0) {
      mesh.position.copy(origin)
      continue
    }
    const centroid = index.centroids.get(partId) ?? index.assetCentroid
    const [x, y, z] = explodeOffset(centroid, index.assetCentroid, amount)
    mesh.position.set(origin.x + x, origin.y + y, origin.z + z)
  }
}

// ------------------------------------------------------------------------------------------------
// Overlays (highlight / select / colorize / outline)
// ------------------------------------------------------------------------------------------------

export interface PartsMaterials {
  readonly hover: MeshBasicMaterial
  readonly select: MeshBasicMaterial
  readonly outline: LineBasicMaterial
  /** `colors[n]` = `--part-<n>` (1..12) for physical / base parts. */
  readonly colors: readonly MeshBasicMaterial[]
  /** Same colours, drawn above the host colour (regions). */
  readonly regionColors: readonly MeshBasicMaterial[]
  dispose: () => void
}

const FALLBACK_PART_COLORS = ['#9aa3b5', '#f87171', '#fb923c', '#facc15', '#a3e635', '#4ade80', '#2dd4bf', '#38bdf8', '#818cf8', '#a78bfa', '#e879f9', '#f472b6', '#d6a77a']

function cssColor(name: string, fallback: string): Color {
  const color = new Color(fallback)
  if (typeof document === 'undefined') return color
  const value = getComputedStyle(document.documentElement).getPropertyValue(name).trim()
  if (/^(#[0-9a-f]{3,8}|rgba?\(|hsla?\()/i.test(value)) color.setStyle(value)
  return color
}

function overlayMaterial(color: Color, opacity: number, offset: number): MeshBasicMaterial {
  return new MeshBasicMaterial({
    color,
    transparent: true,
    opacity,
    depthTest: true,
    depthWrite: false,
    polygonOffset: true,
    polygonOffsetFactor: -1,
    polygonOffsetUnits: -offset,
    side: DoubleSide,
    toneMapped: false,
  })
}

/** Overlay materials from the `--part-*` tokens (read once per parts model). */
export function createPartsMaterials(): PartsMaterials {
  const select = cssColor('--part-select', '#b9a8ff')
  const hover = overlayMaterial(cssColor('--part-highlight', '#fbbf24'), 0.35, 4)
  const selectMaterial = overlayMaterial(select, 0.35, 4)
  const outline = new LineBasicMaterial({ color: select, transparent: true, opacity: 0.9, depthTest: true, toneMapped: false })
  const palette = FALLBACK_PART_COLORS.map((fallback, n) => (n === 0 ? new Color(fallback) : cssColor(`--part-${n}`, fallback)))
  const colors = palette.slice(0, PART_COLOR_COUNT + 1).map((color) => overlayMaterial(color, 0.85, 1))
  const regionColors = palette.slice(0, PART_COLOR_COUNT + 1).map((color) => overlayMaterial(color, 0.85, 2))
  return {
    hover,
    select: selectMaterial,
    outline,
    colors,
    regionColors,
    dispose: () => {
      for (const material of [hover, selectMaterial, outline, ...colors, ...regionColors]) material.dispose()
    },
  }
}

interface OverlayEntry {
  readonly object: Mesh | LineSegments
  /** Geometry created for this overlay (shares the host's `position`); `null` = the node's own geometry. */
  readonly owned: BufferGeometry | null
}

export interface OverlayState {
  readonly entries: Map<string, OverlayEntry>
  /** Outline edges per part (built on the first selection, disposed with the state). */
  readonly outlines: Map<string, EdgesGeometry>
}

export function createOverlayState(): OverlayState {
  return { entries: new Map(), outlines: new Map() }
}

const noRaycast = (): void => undefined

function flagOverlay(object: Mesh | LineSegments, renderOrder: number): void {
  object.userData[PART_OVERLAY_FLAG] = true
  object.raycast = noRaycast
  object.renderOrder = renderOrder
}

function makeOverlay(index: PartsIndex, partId: string, material: MeshBasicMaterial, renderOrder: number): OverlayEntry | null {
  const node = index.nodes.get(partId)
  if (node) {
    const mesh = new Mesh(node.geometry, material)
    flagOverlay(mesh, renderOrder)
    node.add(mesh)
    return { object: mesh, owned: null }
  }
  const region = index.regions.get(partId)
  const host = region ? index.hosts.get(region.hostId) : undefined
  if (!region || !host) return null
  const faces = valueSubset(host, region.value, index.hostValues.get(region.hostId) ?? [region.value])
  if (!faces || faces.length === 0) return null
  const geometry = new BufferGeometry()
  geometry.setAttribute('position', host.mesh.geometry.getAttribute('position'))
  geometry.setIndex(new BufferAttribute(faces, 1))
  const mesh = new Mesh(geometry, material)
  flagOverlay(mesh, renderOrder)
  host.mesh.add(mesh)
  return { object: mesh, owned: geometry }
}

function makeOutline(index: PartsIndex, state: OverlayState, partId: string, material: LineBasicMaterial): OverlayEntry | null {
  const node = index.nodes.get(partId)
  if (!node) return null
  let edges = state.outlines.get(partId)
  if (!edges) {
    const host = index.hostOfMesh.get(node)?.state
    const faces = (host ? host.original.length : (node.geometry.index?.count ?? node.geometry.getAttribute('position').count)) / 3
    if (faces > OUTLINE_MAX_FACES) return null
    if (host) {
      // Edges of every face (the draw index may hold a subset): a temporary geometry over the original index.
      const source = new BufferGeometry()
      source.setAttribute('position', node.geometry.getAttribute('position'))
      source.setIndex(host.originalAttr)
      edges = new EdgesGeometry(source, OUTLINE_THRESHOLD_DEG)
      source.deleteAttribute('position')
      source.setIndex(null)
    } else {
      edges = new EdgesGeometry(node.geometry, OUTLINE_THRESHOLD_DEG)
    }
    state.outlines.set(partId, edges)
  }
  const line = new LineSegments(edges, material)
  flagOverlay(line, 5)
  node.add(line)
  return { object: line, owned: null }
}

/** Remove one overlay; a region overlay first drops the shared `position` so disposing it never frees the host's buffer. */
export function disposePartOverlay(entry: OverlayEntry): void {
  entry.object.removeFromParent()
  if (entry.owned) {
    entry.owned.deleteAttribute('position')
    entry.owned.dispose()
  }
}

/** Diff the wanted overlays (from the display states) against the existing ones. */
export function syncOverlays(
  state: OverlayState,
  index: PartsIndex,
  sel: PartSelection,
  colorize: boolean,
  materials: PartsMaterials,
): void {
  const set = index.set
  const states = displayStates(set, sel)
  const bound = (id: string): boolean => index.nodes.has(id) || index.regions.has(id)
  const want = new Map<string, () => OverlayEntry | null>()
  if (colorize) {
    const colors = colorIndicesFor(set)
    for (const part of set.parts) {
      const display = states[part.id]
      if (part.highlight !== 'ok' || display === 'hidden' || display === 'dimmed' || !bound(part.id)) continue
      const slot = colors[part.id] ?? 0
      const isRegion = index.regions.has(part.id)
      want.set(`c:${part.id}`, () =>
        makeOverlay(index, part.id, (isRegion ? materials.regionColors : materials.colors)[slot], isRegion ? 3 : 2),
      )
    }
  }
  const hovered = sel.hovered
  if (hovered !== null && states[hovered] === 'highlighted' && set.byId[hovered]?.highlight === 'ok' && bound(hovered)) {
    want.set(`h:${hovered}`, () => makeOverlay(index, hovered, materials.hover, 4))
  }
  const selected = sel.selected
  if (selected !== null && states[selected] === 'selected' && set.byId[selected]?.highlight === 'ok' && bound(selected)) {
    want.set(`s:${selected}`, () => makeOverlay(index, selected, materials.select, 4))
    if (set.byId[selected]?.kind === 'physical') {
      want.set(`o:${selected}`, () => makeOutline(index, state, selected, materials.outline))
    }
  }
  for (const [key, entry] of state.entries) {
    if (want.has(key)) continue
    disposePartOverlay(entry)
    state.entries.delete(key)
  }
  for (const [key, make] of want) {
    if (state.entries.has(key)) continue
    const entry = make()
    if (entry) state.entries.set(key, entry)
  }
}

export function disposeOverlays(state: OverlayState): void {
  for (const entry of state.entries.values()) disposePartOverlay(entry)
  state.entries.clear()
  for (const edges of state.outlines.values()) edges.dispose()
  state.outlines.clear()
}

// ------------------------------------------------------------------------------------------------
// Picking clones, focus boxes
// ------------------------------------------------------------------------------------------------

export interface PickTargets {
  /** Wrapped in drei `<Bvh>` once built; the clones are never rendered (`visible = false`). */
  readonly group: Group
  readonly clones: readonly Mesh[]
  readonly sourceOf: ReadonlyMap<Mesh, Mesh>
  readonly material: MeshBasicMaterial
}

/** One pick-only clone per mesh-owning node (geometry filled by `attachPickTargets`). */
export function createPickTargets(index: PartsIndex): PickTargets {
  const group = new Group()
  const material = new MeshBasicMaterial({ side: DoubleSide })
  const clones: Mesh[] = []
  const sourceOf = new Map<Mesh, Mesh>()
  for (const mesh of index.partOfMesh.keys()) {
    const clone = new Mesh(new BufferGeometry(), material)
    clone.visible = false
    clone.matrixAutoUpdate = false
    clone.matrixWorldAutoUpdate = false
    clone.userData[PART_OVERLAY_FLAG] = true
    group.add(clone)
    clones.push(clone)
    sourceOf.set(clone, mesh)
  }
  return { group, clones, sourceOf, material }
}

/** Share the node's `position` and its ORIGINAL full index (never the draw index). Idempotent. */
export function attachPickTargets(index: PartsIndex, targets: PickTargets): void {
  for (const clone of targets.clones) {
    const source = targets.sourceOf.get(clone)
    if (!source) continue
    const host = index.hostOfMesh.get(source)?.state
    clone.geometry.setAttribute('position', source.geometry.getAttribute('position'))
    clone.geometry.setIndex(host ? host.originalAttr : source.geometry.index)
  }
}

/** Drop the shared attributes (the `<Bvh>` cleanup disposes the bounds trees). */
export function detachPickTargets(targets: PickTargets): void {
  for (const clone of targets.clones) {
    clone.geometry.deleteAttribute('position')
    clone.geometry.setIndex(null)
  }
}

/**
 * The part under the pointer: the first hit on a drawn face. A host hit maps its face label to the region with
 * that value (`highlight: 'ok'`), else the host part itself.
 */
export function pickPart(index: PartsIndex, targets: PickTargets, raycaster: Raycaster, ndc: Vector2, camera: Camera): string | null {
  raycaster.setFromCamera(ndc, camera)
  for (const clone of targets.clones) {
    const source = targets.sourceOf.get(clone)
    if (!source) continue
    source.updateWorldMatrix(true, false)
    clone.matrixWorld.copy(source.matrixWorld)
  }
  for (const hit of raycaster.intersectObjects(targets.clones as Mesh[], false)) {
    const source = targets.sourceOf.get(hit.object as Mesh)
    if (!source || !source.visible) continue
    const partId = index.partOfMesh.get(source) ?? null
    const host = index.hostOfMesh.get(source)
    if (host && host.state.labels && hit.faceIndex != null) {
      const label = host.state.labels[hit.faceIndex]
      if (label === undefined || (host.state.visibleLabel && !host.state.visibleLabel(label))) continue
      return index.regionAt.get(host.hostId)?.get(label) ?? partId
    }
    return partId
  }
  return null
}

/** World-space bounding box of a part (local boxes computed once per part), or `null` when it is not in the GLB. */
export function partBox(index: PartsIndex, partId: string, target: Box3): Box3 | null {
  const node = index.nodes.get(partId)
  if (node) {
    if (!node.geometry.boundingBox) node.geometry.computeBoundingBox()
    node.updateWorldMatrix(true, false)
    return target.copy(node.geometry.boundingBox as Box3).applyMatrix4(node.matrixWorld)
  }
  const region = index.regions.get(partId)
  const host = region ? index.hosts.get(region.hostId) : undefined
  if (!region || !host) return null
  let local = index.regionBoxes.get(partId)
  if (!local) {
    local = new Box3()
    const faces = valueSubset(host, region.value, index.hostValues.get(region.hostId) ?? [region.value])
    const position = host.mesh.geometry.getAttribute('position')
    const point = new Vector3()
    if (faces && position) {
      for (let i = 0; i < faces.length; i++) local.expandByPoint(point.fromBufferAttribute(position, faces[i]))
    }
    index.regionBoxes.set(partId, local)
  }
  if (local.isEmpty()) return null
  host.mesh.updateWorldMatrix(true, false)
  return target.copy(local).applyMatrix4(host.mesh.matrixWorld)
}
