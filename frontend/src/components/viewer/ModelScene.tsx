/**
 * Everything rendered inside the R3F canvas: lights, local studio environment (no network HDRs), the GLB
 * model, grid, contact shadows, orbit controls and the imperative viewer API (fit / reset / screenshot).
 */
import { ContactShadows, Environment, Grid, Lightformer, OrbitControls } from '@react-three/drei'
import { useLoader, useThree } from '@react-three/fiber'
import { Component, Suspense, useCallback, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from 'react'
import type { PerspectiveCamera } from 'three'
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js'
import { MeshoptDecoder } from 'three/examples/jsm/libs/meshopt_decoder.module.js'

import type { ViewerOptions, ViewMode } from '../../store/uiStore'
import {
  applyViewMode,
  fitCameraToLayout,
  computeLayout,
  createViewMaterials,
  disposeScene,
  restoreOriginalMaterials,
  setWireframeOverlay,
  type ModelLayout,
  type OrbitControlsLike,
} from './sceneUtils'
import { useViewerRuntime } from './viewerRuntime'
import type { ViewerApi } from './viewerTypes'

function configureLoader(loader: GLTFLoader): void {
  // Meshopt decoder is bundled (wasm inlined) — no network access. Draco is intentionally not configured.
  loader.setMeshoptDecoder(MeshoptDecoder)
}

function onLoadProgress(event: ProgressEvent): void {
  useViewerRuntime.getState().setBytes(event.loaded, event.lengthComputable ? event.total : 0)
}

/** Byte progress of the secondary (tuned) model is tracked separately so the two loaders never mix. */
function onSecondaryLoadProgress(event: ProgressEvent): void {
  useViewerRuntime.getState().setSecondaryBytes(event.loaded, event.lengthComputable ? event.total : 0)
}

// Deferred disposal survives React StrictMode's mount → unmount → mount simulation in development.
const pendingDisposals = new Map<string, number>()

function scheduleDisposal(url: string, dispose: () => void): void {
  pendingDisposals.set(
    url,
    window.setTimeout(() => {
      pendingDisposals.delete(url)
      dispose()
    }, 250),
  )
}

function cancelDisposal(url: string): void {
  const timer = pendingDisposals.get(url)
  if (timer !== undefined) {
    window.clearTimeout(timer)
    pendingDisposals.delete(url)
  }
}

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

interface ModelErrorBoundaryProps {
  children: ReactNode
  /** Secondary models report to their own status so a failed tuned model never hides the original. */
  role?: 'primary' | 'secondary'
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
}

export function ModelScene({ url, secondaryUrl, showSecondary, options, retryKey, secondaryRetryKey, onApi }: ModelSceneProps) {
  const [layout, setLayout] = useState<ModelLayout | null>(null)
  const secondaryReady = useViewerRuntime((s) => s.secondaryStatus === 'ready')
  const extent = layout ? Math.max(layout.size[0], layout.size[2], layout.size[1] * 0.5) : 1
  const resetKey = `${url}#${retryKey}`
  const secondaryKey = `${secondaryUrl}#${retryKey}.${secondaryRetryKey}`
  // Keep the original on screen until the tuned model is ready (no blank frame while it downloads).
  const showingSecondary = !!secondaryUrl && showSecondary && secondaryReady

  return (
    <>
      <ambientLight intensity={options.environment ? 0.12 : 0.5} />
      <directionalLight position={[3, 5, 4]} intensity={options.environment ? 0.9 : 1.8} />
      <directionalLight position={[-4, 2.5, -3]} intensity={options.environment ? 0.35 : 0.8} />
      {options.environment && <StudioEnvironment />}

      <ModelErrorBoundary key={`model:${resetKey}`}>
        <Suspense fallback={null}>
          <Model
            key={resetKey}
            url={url}
            role="primary"
            visible={!showingSecondary}
            viewMode={options.viewMode}
            wireframe={options.wireframe}
            onLayout={setLayout}
          />
        </Suspense>
      </ModelErrorBoundary>
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
          key={`shadows:${resetKey}:${showingSecondary ? 'tuned' : 'original'}`}
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
