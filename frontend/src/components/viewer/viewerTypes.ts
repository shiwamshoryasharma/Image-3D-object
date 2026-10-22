/** Shared viewer types (kept free of three.js imports so the main bundle stays light). */
import type { ViewerOptions } from '../../store/uiStore'

/** Imperative API exposed by the lazily loaded 3D viewer. */
export interface ViewerApi {
  resetCamera: () => void
  /** PNG data URL of the current frame. */
  screenshot: () => string | null
  /**
   * Fit the camera to one part (UI_DESIGN §6.6). Same as `useViewerRuntime.getState().focusPart(partId)`: the
   * request is served once the parts model is indexed (it mounts the parts model if needed).
   */
  focusPart?: (partId: string) => void
}

/** How the parts of one `PartSetView` map onto the parts GLB (`partsScene.bindingFor`, cached per set). */
export interface ViewerPartsBinding {
  /** Base + physical (mesh-owning) parts. */
  physical: readonly { partId: string; nodeName: string }[]
  /** Regions + deferred parts; `shared` = highlight `'shared_label'` (no hide / isolate / highlight). */
  regions: readonly { partId: string; value: number; hostId: string; shared: boolean }[]
}

/**
 * The part set shown by the viewer: strings that change only when another part set is shown. Everything
 * interactive (show, colorize, explode, hidden, isolated, selected, hovered) is read by `PartsModel` itself.
 */
export interface ViewerPartsProps {
  analysisId: string
  /** Token-aware URL of the assembled parts GLB (`fileUrl(set.urls.assembled)`). */
  url: string
  regionAttribute: string
}

export interface ModelViewerProps {
  /** Token-aware GLB URL. */
  url: string
  /**
   * Second model mounted in the same canvas (the Auto Tune result), positioned with the original's layout
   * offset so both line up. `null` = not mounted.
   */
  secondaryUrl?: string | null
  /** Show the secondary model instead of the original (once it has loaded). */
  showSecondary?: boolean
  options: ViewerOptions
  /** Render loop runs only while visible. */
  active: boolean
  onApi: (api: ViewerApi | null) => void
  /** Parts model (third model role, UI_DESIGN §6.6). `null` = not mounted. */
  parts?: ViewerPartsProps | null
}
