/**
 * three.js helpers for the viewer: layout/fit computation, view-mode material swapping, wireframe overlay
 * and resource disposal. Part overlays (`PART_OVERLAY_FLAG`, owned by `PartsModel`) are skipped by every helper here,
 * so they keep their colours in PBR, clay and normals and never get a wire overlay (UI_DESIGN §6.6).
 */
import {
  Box3,
  Material,
  Mesh,
  MeshBasicMaterial,
  MeshNormalMaterial,
  MeshStandardMaterial,
  Texture,
  Vector3,
  type Object3D,
  type PerspectiveCamera,
} from 'three'
import type { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js'

import type { ViewMode } from '../../store/uiStore'

export function configureLoader(loader: GLTFLoader): void {
  // Meshopt decoder is bundled (wasm inlined) — no network access. Draco is intentionally not configured.
  loader.setMeshoptDecoder(MeshoptDecoder)
}

// Deferred disposal survives React StrictMode's mount → unmount → mount simulation in development.
const pendingDisposals = new Map<string, number>()

export function scheduleDisposal(url: string, dispose: () => void): void {
  pendingDisposals.set(
    url,
    window.setTimeout(() => {
      pendingDisposals.delete(url)
      dispose()
    }, 250),
  )
}

export function cancelDisposal(url: string): void {
  const timer = pendingDisposals.get(url)
  if (timer !== undefined) {
    window.clearTimeout(timer)
    pendingDisposals.delete(url)
  }
}

export interface ModelLayout {
  /** Translation that centers the model on X/Z and puts its base on y = 0. */
  offset: [number, number, number]
  size: [number, number, number]
  /** Bounding-sphere radius. */
  radius: number
}

export function computeLayout(root: Object3D): ModelLayout {
  root.updateMatrixWorld(true)
  const box = new Box3().setFromObject(root)
  if (box.isEmpty()) return { offset: [0, 0, 0], size: [1, 1, 1], radius: 0.87 }
  const size = box.getSize(new Vector3())
  const center = box.getCenter(new Vector3())
  return {
    offset: [-center.x, -box.min.y, -center.z],
    size: [size.x, size.y, size.z],
    radius: Math.max(size.length() / 2, 1e-3),
  }
}

export interface FitTarget {
  position: Vector3
  target: Vector3
  distance: number
}

/** Minimal structural type of drei's OrbitControls instance (three-stdlib). */
export interface OrbitControlsLike {
  target: Vector3
  minDistance: number
  maxDistance: number
  update: () => void
}

/** Frame the model: position the camera, tune clipping planes and orbit limits. */
export function fitCameraToLayout(camera: PerspectiveCamera, controls: OrbitControlsLike | null, layout: ModelLayout): void {
  const { position, target, distance } = computeFit(layout, camera)
  camera.position.copy(position)
  camera.near = Math.max(distance / 200, 1e-3)
  camera.far = distance * 200
  camera.updateProjectionMatrix()
  if (controls) {
    controls.target.copy(target)
    controls.minDistance = layout.radius * 0.15
    controls.maxDistance = distance * 8
    controls.update()
  } else {
    camera.lookAt(target)
  }
}

/** Fit the camera to a world-space box, keeping the current viewing direction (`focusPart`, UI_DESIGN §6.6). */
export function fitCameraToBox(camera: PerspectiveCamera, controls: OrbitControlsLike | null, box: Box3): void {
  if (box.isEmpty()) return
  const center = box.getCenter(new Vector3())
  const radius = Math.max(box.getSize(new Vector3()).length() / 2, 1e-3)
  const verticalFov = (camera.fov * Math.PI) / 180
  const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * Math.max(camera.aspect, 0.1))
  const distance = (radius / Math.sin(Math.min(verticalFov, horizontalFov) / 2)) * 1.3
  const direction = camera.position.clone().sub(controls ? controls.target : center)
  if (direction.lengthSq() < 1e-12) direction.set(0.85, 0.5, 1.15)
  direction.normalize()
  camera.position.copy(center).addScaledVector(direction, distance)
  camera.near = Math.min(camera.near, Math.max(distance / 200, 1e-4))
  camera.updateProjectionMatrix()
  if (controls) {
    controls.target.copy(center)
    controls.minDistance = Math.min(controls.minDistance, radius * 0.5)
    controls.update()
  } else {
    camera.lookAt(center)
  }
}

/** Camera placement that frames a model of the given layout for the camera's aspect ratio. */
export function computeFit(layout: ModelLayout, camera: PerspectiveCamera): FitTarget {
  const target = new Vector3(0, layout.size[1] / 2, 0)
  const verticalFov = (camera.fov * Math.PI) / 180
  const horizontalFov = 2 * Math.atan(Math.tan(verticalFov / 2) * Math.max(camera.aspect, 0.1))
  const fov = Math.min(verticalFov, horizontalFov)
  const distance = (layout.radius / Math.sin(fov / 2)) * 1.3
  const direction = new Vector3(0.85, 0.5, 1.15).normalize()
  return { position: target.clone().addScaledVector(direction, distance), target, distance }
}

// ------------------------------------------------------------------------------------------------
// Materials
// ------------------------------------------------------------------------------------------------

type MaterialSlot = Material | Material[]

export interface ViewMaterials {
  clay: MeshStandardMaterial
  normals: MeshNormalMaterial
  wire: MeshBasicMaterial
  dispose: () => void
}

export function createViewMaterials(): ViewMaterials {
  const clay = new MeshStandardMaterial({
    color: '#c8c2b8',
    roughness: 0.82,
    metalness: 0,
    polygonOffset: true,
    polygonOffsetFactor: 1,
    polygonOffsetUnits: 1,
  })
  const normals = new MeshNormalMaterial({ polygonOffset: true, polygonOffsetFactor: 1, polygonOffsetUnits: 1 })
  const wire = new MeshBasicMaterial({
    color: '#b9a8ff',
    wireframe: true,
    transparent: true,
    opacity: 0.22,
    depthWrite: false,
  })
  return {
    clay,
    normals,
    wire,
    dispose: () => {
      clay.dispose()
      normals.dispose()
      wire.dispose()
    },
  }
}

const originalMaterials = new WeakMap<Mesh, MaterialSlot>()
const WIRE_OVERLAY_FLAG = '__img3dWireOverlay'
/** Marks the highlight / select / colorize overlays and outlines of the parts view (owned and disposed by `PartsModel`). */
export const PART_OVERLAY_FLAG = '__img3dPartOverlay'

const isOverlay = (object: Object3D): boolean => !!object.userData[WIRE_OVERLAY_FLAG] || !!object.userData[PART_OVERLAY_FLAG]

function forEachMesh(root: Object3D, visit: (mesh: Mesh) => void): void {
  root.traverse((object) => {
    if ((object as Mesh).isMesh && !isOverlay(object)) visit(object as Mesh)
  })
}

function asArray(slot: MaterialSlot): Material[] {
  return Array.isArray(slot) ? slot : [slot]
}

/** Swap mesh materials for the selected view mode (originals are kept and restored for `pbr`). */
export function applyViewMode(root: Object3D, mode: ViewMode, materials: ViewMaterials): void {
  forEachMesh(root, (mesh) => {
    if (!originalMaterials.has(mesh)) originalMaterials.set(mesh, mesh.material)
    if (mode !== 'pbr' && !mesh.geometry.attributes.normal) mesh.geometry.computeVertexNormals()
    mesh.material = mode === 'pbr' ? (originalMaterials.get(mesh) as MaterialSlot) : mode === 'clay' ? materials.clay : materials.normals
  })
}

/** Push the original (PBR) surfaces back in depth while the wireframe overlay is visible. */
function setPolygonOffset(root: Object3D, enabled: boolean): void {
  forEachMesh(root, (mesh) => {
    const original = originalMaterials.get(mesh) ?? mesh.material
    for (const material of asArray(original)) {
      if (material.polygonOffset === enabled) continue
      material.polygonOffset = enabled
      material.polygonOffsetFactor = enabled ? 1 : 0
      material.polygonOffsetUnits = enabled ? 1 : 0
      material.needsUpdate = true
    }
  })
}

/** Add/remove a wireframe overlay mesh (sharing geometry) on every mesh. */
export function setWireframeOverlay(root: Object3D, enabled: boolean, material: MeshBasicMaterial): void {
  const existing: Object3D[] = []
  root.traverse((object) => {
    if (object.userData[WIRE_OVERLAY_FLAG]) existing.push(object)
  })
  existing.forEach((overlay) => overlay.removeFromParent())
  setPolygonOffset(root, enabled)
  if (!enabled) return
  const targets: Mesh[] = []
  forEachMesh(root, (mesh) => targets.push(mesh))
  for (const mesh of targets) {
    const overlay = new Mesh(mesh.geometry, material)
    overlay.userData[WIRE_OVERLAY_FLAG] = true
    overlay.raycast = () => undefined
    overlay.renderOrder = 1
    mesh.add(overlay)
  }
}

/** Restore original materials (so the cached GLTF can be shown again in PBR mode). */
export function restoreOriginalMaterials(root: Object3D): void {
  forEachMesh(root, (mesh) => {
    const original = originalMaterials.get(mesh)
    if (original) mesh.material = original
  })
}

/** Dispose geometries, materials and textures of a loaded scene. */
export function disposeScene(root: Object3D): void {
  const textures = new Set<Texture>()
  const materials = new Set<Material>()
  root.traverse((object) => {
    const mesh = object as Mesh
    if (!mesh.isMesh || object.userData[PART_OVERLAY_FLAG]) return
    if (!object.userData[WIRE_OVERLAY_FLAG]) mesh.geometry?.dispose()
    const original = originalMaterials.get(mesh) ?? mesh.material
    for (const material of asArray(original)) materials.add(material)
  })
  for (const material of materials) {
    for (const value of Object.values(material)) {
      if (value instanceof Texture) textures.add(value)
    }
    material.dispose()
  }
  textures.forEach((texture) => {
    const image: unknown = texture.image
    texture.dispose()
    // GLTFLoader uses ImageBitmaps in Chromium; release their memory explicitly.
    if (typeof ImageBitmap !== 'undefined' && image instanceof ImageBitmap) image.close()
  })
}
