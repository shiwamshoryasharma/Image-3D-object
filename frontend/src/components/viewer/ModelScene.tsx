/**
 * Everything rendered inside the R3F canvas: lights, local studio environment (no network HDRs), the GLB
 * model, grid, contact shadows, orbit controls and the imperative viewer API (fit / reset / screenshot / focusPart).
 * The parts model (UI_DESIGN §6.6) is a third role: it replaces the original on screen only while the parts view is
 * on and its GLB is ready; this component re-renders only when that changes (never on hover).
 */
import { ContactShadows, Environment, Grid, Lightformer, OrbitControls } from '@react-three/drei'
import { useLoader, useThree } from '@react-three/fiber'
import { Component, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from 'react'
import type { PerspectiveCamera } from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'

import { usePartStore } from '../../store/partStore'
import type { ViewerOptions, ViewMode } from '../../store/uiStore'
import { PartsModel } from './PartsModel'
import {
  applyViewMode,
  cancelDisposal,
  configureLoader,
  fitCameraToLayout,
  computeLayout,
  createViewMaterials,
  disposeScene,
  restoreOriginalMaterials,
  scheduleDisposal,
  setWireframeOverlay,
  type ModelLayout,
  type OrbitControlsLike,
} from './sceneUtils'
import { useLowMemory, useViewerRuntime } from './viewerRuntime'
import type { ViewerApi, ViewerPartsProps } from './viewerTypes'

function onLoadProgress(event: ProgressEvent): void {
  useViewerRuntime.getState().setBytes(event.loaded, event.lengthComputable ? event.total : 0)
}

/** Byte progress of the secondary (tuned) model is tracked separately so the two loaders never mix. */
function onSecondaryLoadProgress(event: ProgressEvent): void {
  useViewerRuntime.getState().setSecondaryBytes(event.loaded, event.lengthComputable ? event.total : 0)
}

const sameLayout = (a: ModelLayout, b: ModelLayout): boolean =>
  a.radius === b.radius && a.offset.every((v, i) => v === b.offset[i]) && a.size.every((v, i) => v === b.size[i])

interface ModelProps {
  url: string
  viewMode: ViewMode
  wireframe: boolean
  visible: boolean
  /** The primary model owns the layout (camera fit); the secondary one reuses the primary's offset. */
  role: 'primary' | 'secondary'
  onLayout?: (layout: ModelLayout) => void
  /** Position override (the primary model's layout offset) so the tuned model lines up with the original. */
  offset?: [number, number, number] | null
}

function Model({ url, viewMode, wireframe, visible, role, onLayout, offset }: ModelProps) {
  const gltf = useLoader(GLTFLoader, url, configureLoader, role === 'primary' ? onLoadProgress : onSecondaryLoadProgress)
  const scene = gltf.scene
  const layout = useMemo(() => computeLayout(scene), [scene])
  const materials = useMemo(() => createViewMaterials(), [])

  useLayoutEffect(() => {
    if (role === 'secondary') {
      useViewerRuntime.getState().setSecondaryReady()
      return
    }
    onLayout?.(layout)
    useViewerRuntime.getState().setReady()
  }, [layout, onLayout, role])

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
        useLoader.clear(GLTFLoader, url)
      })
    }
  }, [scene, url, materials])

  return (
    <group position={offset ?? layout.offset} visible={visible}>
      <primitive object={scene} />
    </group>
  )
}

/** Suspense fallback of the parts model: marks it loading only while it really suspends. */
function PartsLoading() {
  useLayoutEffect(() => {
    useViewerRuntime.getState().startParts()
  }, [])
  return null
}

interface ModelErrorBoundaryProps {
  children: ReactNode
  /** Secondary / parts models report to their own status so a failed one never hides the original. */
  role?: 'primary' | 'secondary' | 'parts'
}

/** Error boundary inside the canvas: reports loader errors to the DOM overlay instead of unmounting the canvas. */
class ModelErrorBoundary extends Component<ModelErrorBoundaryProps, { error: Error | null }> {
  state: { error: Error | null } = { error: null }

  static getDerivedStateFromError(error: unknown) {
    return { error: error instanceof Error ? error : new Error(String(error)) }
  }

  componentDidCatch(error: unknown) {
    const raw = error instanceof Error ? error.message : String(error)
    // Loader errors include the request URL; never surface the auth token.
    const message = raw.replace(/([?&])token=[^&\s"']+/g, '$1token=***')
    if (this.props.role === 'secondary') useViewerRuntime.getState().setSecondaryError(message || 'Failed to load the tuned model')
    else if (this.props.role === 'parts') useViewerRuntime.getState().setPartsError(message || 'Failed to load the parts model')
    else useViewerRuntime.getState().setError(message || 'Failed to load the model')
  }

  render() {
    return this.state.error ? null : this.props.children
  }
}

/** Neutral studio lighting rendered once into a cube map from local light-formers (no HDR download). */
function StudioEnvironment() {
  return (
    <Environment resolution={256} frames={1} background={false} environmentIntensity={0.9}>
      <color attach="background" args={['#1b1f29']} />
      <Lightformer form="rect" intensity={3} position={[0, 4, 3]} scale={[6, 3, 1]} target={[0, 0, 0]} />
      <Lightformer form="rect" intensity={1.4} position={[-4, 1.5, -1]} scale={[3, 6, 1]} target={[0, 0, 0]} />
      <Lightformer form="rect" intensity={1.8} position={[4, 1.5, -2]} scale={[3, 6, 1]} target={[0, 0, 0]} />
      <Lightformer form="ring" intensity={1.2} position={[0, 3, -5]} scale={3} target={[0, 0, 0]} />
    </Environment>
  )
}

interface ControllerProps {
  layout: ModelLayout | null
  onApi: (api: ViewerApi | null) => void
}

/** Fits the camera to the model and exposes reset/screenshot through `onApi`. */
function ViewerController({ layout, onApi }: ControllerProps) {
  const gl = useThree((state) => state.gl)
  const scene = useThree((state) => state.scene)
  const camera = useThree((state) => state.camera)
  const controls = useThree((state) => state.controls) as unknown as OrbitControlsLike | null

  const fit = useCallback(() => {
    if (layout) fitCameraToLayout(camera as PerspectiveCamera, controls, layout)
  }, [layout, camera, controls])

  // Fit whenever a new model (layout) arrives or the controls become available.
  useEffect(() => {
    fit()
  }, [fit])

  useEffect(() => {
    onApi({
      resetCamera: fit,
      screenshot: () => {
        gl.render(scene, camera)
        return gl.domElement.toDataURL('image/png')
      },
      focusPart: (partId) => useViewerRuntime.getState().focusPart(partId),
    })
    return () => onApi(null)
  }, [fit, gl, scene, camera, onApi])

  return null
}

export interface ModelSceneProps {
  url: string
  /** Tuned model mounted alongside the original (preloaded, hidden until shown). */
  secondaryUrl: string | null
  showSecondary: boolean
  options: ViewerOptions
  retryKey: number
  /** Changes when the secondary model is retried on its own. */
  secondaryRetryKey: number
  onApi: (api: ViewerApi | null) => void
  /** Parts model; `null` = not mounted. */
  parts: ViewerPartsProps | null
  /** Changes when the parts model is retried on its own. */
  partsRetryKey: number
}

export function ModelScene({
  url,
  secondaryUrl,
  showSecondary,
  options,
  retryKey,
  secondaryRetryKey,
  onApi,
  parts,
  partsRetryKey,
}: ModelSceneProps) {
  const [layout, setLayout] = useState<ModelLayout | null>(null)
  const secondaryReady = useViewerRuntime((s) => s.secondaryStatus === 'ready')
  const partsReady = useViewerRuntime((s) => s.partsStatus === 'ready')
  const partsShow = usePartStore((s) => s.view.show)
  const lowMemory = useLowMemory()
  const extent = layout ? Math.max(layout.size[0], layout.size[2], layout.size[1] * 0.5) : 1
  const resetKey = `${url}#${retryKey}`
  const secondaryKey = `${secondaryUrl}#${retryKey}.${secondaryRetryKey}`
  const partsKey = `${parts?.url}#${retryKey}.${partsRetryKey}`
  // Keep the original on screen until the tuned / parts model is ready (no blank frame while it downloads).
  const showingParts = parts !== null && partsShow && partsReady
  const showingSecondary = !!secondaryUrl && showSecondary && secondaryReady && !showingParts
  // Low memory (UI_DESIGN §6.6): the original is unmounted while the parts model shows.
  const primaryMounted = !(lowMemory && showingParts)
  // A remounted original reports an equal layout: keep the old object so the camera is not refitted.
  const onLayout = useCallback((next: ModelLayout) => setLayout((prev) => (prev && sameLayout(prev, next) ? prev : next)), [])

  return (
    <>
      <ambientLight intensity={options.environment ? 0.12 : 0.5} />
      <directionalLight position={[3, 5, 4]} intensity={options.environment ? 0.9 : 1.8} />
      <directionalLight position={[-4, 2.5, -3]} intensity={options.environment ? 0.35 : 0.8} />
      {options.environment && <StudioEnvironment />}

      {primaryMounted && (
        <ModelErrorBoundary key={`model:${resetKey}`}>
          <Suspense fallback={null}>
            <Model
              key={resetKey}
              url={url}
              role="primary"
              visible={!showingSecondary && !showingParts}
              viewMode={options.viewMode}
              wireframe={options.wireframe}
              onLayout={onLayout}
            />
          </Suspense>
        </ModelErrorBoundary>
      )}
      {secondaryUrl && (
        <ModelErrorBoundary key={`secondary:${secondaryKey}`} role="secondary">
          <Suspense fallback={null}>
            <Model
              key={secondaryKey}
              url={secondaryUrl}
              role="secondary"
              visible={showingSecondary}
              offset={layout?.offset ?? null}
              viewMode={options.viewMode}
              wireframe={options.wireframe}
            />
          </Suspense>
        </ModelErrorBoundary>
      )}
      {parts && layout && (
        <ModelErrorBoundary key={`parts:${partsKey}`} role="parts">
          <Suspense fallback={<PartsLoading />}>
            <PartsModel
              key={partsKey}
              analysisId={parts.analysisId}
              url={parts.url}
              regionAttribute={parts.regionAttribute}
              offset={layout.offset}
              viewMode={options.viewMode}
              wireframe={options.wireframe}
            />
          </Suspense>
        </ModelErrorBoundary>
      )}

      {layout && options.grid && (
        <Grid
          position={[0, -0.0005, 0]}
          args={[10, 10]}
          cellSize={extent / 10}
          cellThickness={0.6}
          cellColor="#2a3040"
          sectionSize={extent / 2}
          sectionThickness={1}
          sectionColor="#3d4560"
          fadeDistance={extent * 8}
          fadeStrength={1.5}
          infiniteGrid
          followCamera={false}
        />
      )}
      {layout && options.shadows && (
        <ContactShadows
          key={`shadows:${resetKey}:${showingParts ? 'parts' : showingSecondary ? 'tuned' : 'original'}`}
          position={[0, 0, 0]}
          scale={extent * 3}
          opacity={0.55}
          blur={2.4}
          far={Math.max(layout.size[1], 0.1)}
          resolution={512}
          frames={1}
          color="#000000"
        />
      )}

      <OrbitControls makeDefault enableDamping dampingFactor={0.08} autoRotate={options.autoRotate} autoRotateSpeed={1.2} />
      <ViewerController layout={layout} onApi={onApi} />
    </>
  )
}
