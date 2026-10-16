/** Shared viewer types (kept free of three.js imports so the main bundle stays light). */
import type { ViewerOptions } from '../../store/uiStore'

/** Imperative API exposed by the lazily loaded 3D viewer. */
export interface ViewerApi {
  resetCamera: () => void
  /** PNG data URL of the current frame. */
  screenshot: () => string | null
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
}
