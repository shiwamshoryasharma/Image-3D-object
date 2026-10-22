/**
 * Parts model (UI_DESIGN.md §6.6): the assembled parts GLB as a third model role next to the original and the tuned
 * model, positioned with the original's layout offset (same unit-AABB frame, DESIGN §3.2 A4).
 *
 * Props are static strings; the part view state is read here with primitive / stored-reference selectors and applied
 * imperatively in layout effects, so a hover re-renders only this component (never `ModelScene`). Picking uses DOM
 * listeners + one raycast per frame at most (no R3F pointer props, none while orbiting); hover picking starts once the
 * BVH over the pick clones is built (one idle callback after the parts model is first shown).
 */
import { Bvh } from '@react-three/drei'
import { useFrame, useLoader, useThree } from '@react-three/fiber'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Box3, Raycaster, Vector2, type PerspectiveCamera } from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

import { viewportStore } from '../../hooks/useViewport'
import { usePartStore } from '../../store/partStore'
import type { ViewMode } from '../../store/uiStore'
import {
  applyExplode,
  applyVisibility,
  attachPickTargets,
  buildPartsIndex,
  createOverlayState,
  createPartsMaterials,
  createPickTargets,
  detachPickTargets,
  disposeOverlays,
  partBox,
  pickPart,
  prepareParts,
  restoreParts,
  syncOverlays,
  type OverlayState,
  type PickTargets,
} from './partsScene'
import {
  applyViewMode,
  cancelDisposal,
  configureLoader,
  createViewMaterials,
  disposeScene,
  fitCameraToBox,
  restoreOriginalMaterials,
  scheduleDisposal,
  setWireframeOverlay,
  type OrbitControlsLike,
} from './sceneUtils'
import { useViewerRuntime } from './viewerRuntime'
import type { ViewerPartsProps } from './viewerTypes'

function onPartsProgress(event: ProgressEvent): void {
  useViewerRuntime.getState().setPartsBytes(event.loaded, event.lengthComputable ? event.total : 0)
}

/** Pointer state written by DOM listeners, read by the frame loop (never render state). */
interface PointerState {
  readonly ndc: Vector2
  dirty: boolean
  down: { x: number; y: number } | null
  click: boolean
  orbiting: boolean
  /** The current `hovered` was set by the canvas (cleared on pointer leave). */
  canvasHover: boolean
  bvhReady: boolean
  /** Dev counter: raycasts while orbiting (expected 0). */
  orbitRaycasts: number
}

interface ControlsEvents {
  addEventListener: (type: string, listener: () => void) => void
  removeEventListener: (type: string, listener: () => void) => void
}

const CLICK_SLOP_PX = 4
/** One viewer canvas at a time (UI5): a shared raycaster is enough. */
const raycaster = new Raycaster()

function pointerOf(ref: { current: PointerState | null }): PointerState {
  ref.current ??= {
    ndc: new Vector2(),
    dirty: false,
    down: null,
    click: false,
    orbiting: false,
    canvasHover: false,
    bvhReady: false,
    orbitRaycasts: 0,
  }
  return ref.current
}

function whenIdle(callback: () => void): () => void {
  if (typeof window.requestIdleCallback === 'function') {
    const id = window.requestIdleCallback(callback)
    return () => window.cancelIdleCallback(id)
  }
  const id = window.setTimeout(callback, 300)
  return () => window.clearTimeout(id)
}

export interface PartsModelProps extends ViewerPartsProps {
  offset: [number, number, number]
  viewMode: ViewMode
  wireframe: boolean
}

export function PartsModel({ analysisId, url, regionAttribute, offset, viewMode, wireframe }: PartsModelProps) {
  const gltf = useLoader(GLTFLoader, url, configureLoader, onPartsProgress)
  const scene = gltf.scene
  const set = usePartStore((s) => s.sets[analysisId]?.set ?? null)
  const show = usePartStore((s) => s.view.show)
  const colorize = usePartStore((s) => s.view.colorize)
  const explode = usePartStore((s) => s.view.explode)
  const hidden = usePartStore((s) => s.view.hidden)
  const isolated = usePartStore((s) => s.view.isolated)
  const selected = usePartStore((s) => s.view.selected)
  const hovered = usePartStore((s) => s.view.hovered)
  const focusRequest = useViewerRuntime((s) => s.focusRequest)
  const gl = useThree((s) => s.gl)
  const camera = useThree((s) => s.camera)
  const controls = useThree((s) => s.controls)

  const materials = useMemo(() => createViewMaterials(), [])
  const partMaterials = useMemo(() => createPartsMaterials(), [])
  const index = useMemo(() => (set ? buildPartsIndex(scene, set, regionAttribute) : null), [scene, set, regionAttribute])
  const targets = useMemo(() => (index ? createPickTargets(index) : null), [index])
  const [bvhTargets, setBvhTargets] = useState<PickTargets | null>(null)
  const overlaysRef = useRef<OverlayState | null>(null)
  const pointerRef = useRef<PointerState | null>(null)
  const bvhStartRef = useRef(0)
  const bvhOn = targets !== null && bvhTargets === targets

  useLayoutEffect(() => {
    useViewerRuntime.getState().setPartsReady()
  }, [scene])

  // Draw indexes installed before the first frame; everything restored on unmount (the cached GLTF stays valid).
  useLayoutEffect(() => {
    if (!index) return
    prepareParts(index)
    const overlays = createOverlayState()
    overlaysRef.current = overlays
    useViewerRuntime.getState().setPartsIndex(index.report)
    return () => {
      disposeOverlays(overlays)
      if (overlaysRef.current === overlays) overlaysRef.current = null
      restoreParts(index)
      useViewerRuntime.getState().setPartsIndex(null)
    }
  }, [index])

  useLayoutEffect(() => {
    if (!index) return
    const selection = { hidden, isolated, selected, hovered }
    applyVisibility(index, selection)
    const overlays = overlaysRef.current
    if (overlays) syncOverlays(overlays, index, selection, colorize, partMaterials)
  }, [index, hidden, isolated, selected, hovered, colorize, partMaterials])

  useLayoutEffect(() => {
    if (index) applyExplode(index, explode)
  }, [index, explode])

  useEffect(() => {
    applyViewMode(scene, viewMode, materials)
  }, [scene, viewMode, materials])

  useEffect(() => {
    setWireframeOverlay(scene, wireframe, materials.wire)
  }, [scene, wireframe, materials])

  useEffect(() => {
    cancelDisposal(url)
    return () => {
      scheduleDisposal(url, () => {
        setWireframeOverlay(scene, false, materials.wire)
        restoreOriginalMaterials(scene)
        disposeScene(scene)
        materials.dispose()
        partMaterials.dispose()
        useLoader.clear(GLTFLoader, url)
      })
    }
  }, [scene, url, materials, partMaterials])

  useEffect(() => {
    if (!index || !targets) return
    attachPickTargets(index, targets)
    return () => detachPickTargets(targets)
  }, [index, targets])

  // The BVH is built one idle callback after the parts model is first shown.
  useEffect(() => {
    if (!show || !targets || bvhTargets === targets) return
    return whenIdle(() => {
      bvhStartRef.current = performance.now()
      setBvhTargets(targets)
    })
  }, [show, targets, bvhTargets])

  // Child effects (the <Bvh> build) run first, so this measures the build.
  useEffect(() => {
    if (!bvhOn) return
    const pointer = pointerOf(pointerRef)
    pointer.bvhReady = true
    console.info('[parts] bvh built', Math.round(performance.now() - bvhStartRef.current), 'ms')
    return () => {
      pointer.bvhReady = false
    }
  }, [bvhOn])

  useEffect(() => {
    if (!focusRequest || !index) return
    const box = partBox(index, focusRequest.partId, new Box3())
    if (box) fitCameraToBox(camera as PerspectiveCamera, controls as unknown as OrbitControlsLike | null, box)
    useViewerRuntime.getState().clearFocusRequest(focusRequest.seq)
  }, [focusRequest, index, camera, controls])

  // DOM listeners only write refs; the frame loop raycasts.
  useEffect(() => {
    const element = gl.domElement
    const pointer = pointerOf(pointerRef)
    const setNdc = (event: PointerEvent): void => {
      const rect = element.getBoundingClientRect()
      if (rect.width <= 0 || rect.height <= 0) return
      pointer.ndc.set(((event.clientX - rect.left) / rect.width) * 2 - 1, -((event.clientY - rect.top) / rect.height) * 2 + 1)
    }
    const onMove = (event: PointerEvent): void => {
      setNdc(event)
      pointer.dirty = true
    }
    const onDown = (event: PointerEvent): void => {
      pointer.down = event.button === 0 ? { x: event.clientX, y: event.clientY } : null
    }
    const onUp = (event: PointerEvent): void => {
      const down = pointer.down
      pointer.down = null
      if (!down || Math.hypot(event.clientX - down.x, event.clientY - down.y) > CLICK_SLOP_PX) return
      setNdc(event)
      pointer.click = true
    }
    const onLeave = (): void => {
      pointer.dirty = false
      if (!pointer.canvasHover) return
      pointer.canvasHover = false
      if (usePartStore.getState().view.hovered !== null) usePartStore.getState().hover(null)
    }
    element.addEventListener('pointermove', onMove)
    element.addEventListener('pointerdown', onDown)
    element.addEventListener('pointerup', onUp)
    element.addEventListener('pointerleave', onLeave)
    return () => {
      element.removeEventListener('pointermove', onMove)
      element.removeEventListener('pointerdown', onDown)
      element.removeEventListener('pointerup', onUp)
      element.removeEventListener('pointerleave', onLeave)
    }
  }, [gl])

  useEffect(() => {
    const events = controls as unknown as ControlsEvents | null
    if (!events || typeof events.addEventListener !== 'function') return
    const pointer = pointerOf(pointerRef)
    const onStart = (): void => {
      pointer.orbiting = true
      pointer.orbitRaycasts = 0
    }
    const onEnd = (): void => {
      pointer.orbiting = false
      if (import.meta.env.DEV) console.debug('[parts] raycasts during orbit:', pointer.orbitRaycasts)
    }
    events.addEventListener('start', onStart)
    events.addEventListener('end', onEnd)
    return () => {
      events.removeEventListener('start', onStart)
      events.removeEventListener('end', onEnd)
    }
  }, [controls])

  useFrame((state) => {
    const pointer = pointerRef.current
    if (!pointer) return
    if (!index || !targets || !show) {
      // Nothing is pickable while the parts view is off: drop pointer input instead of replaying it later.
      pointer.click = false
      pointer.dirty = false
      return
    }
    if (pointer.click) {
      pointer.click = false
      if (pointer.orbiting) pointer.orbitRaycasts++
      usePartStore.getState().select(pickPart(index, targets, raycaster, pointer.ndc, state.camera))
      return
    }
    if (!pointer.dirty || pointer.orbiting || !pointer.bvhReady || !viewportStore.getSnapshot().hover) return
    pointer.dirty = false
    const id = pickPart(index, targets, raycaster, pointer.ndc, state.camera)
    pointer.canvasHover = id !== null
    if (usePartStore.getState().view.hovered !== id) usePartStore.getState().hover(id)
  })

  return (
    <>
      <group position={offset} visible={show}>
        <primitive object={scene} />
      </group>
      {bvhOn && targets && (
        <Bvh indirect firstHitOnly={false} strategy={0}>
          <primitive object={targets.group} />
        </Bvh>
      )}
    </>
  )
}
