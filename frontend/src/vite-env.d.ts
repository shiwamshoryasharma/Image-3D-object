/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Optional absolute origin of the backend (e.g. `http://127.0.0.1:8000`). Empty → same origin (dev proxy). */
  readonly VITE_API_BASE_URL?: string
  /**
   * `'true'` lists the locked refine node group in the Node palette (`lib/buildFlags.ts`, UI_DESIGN §4.3.2). Default off.
   * There is no tuner build flag: the Auto Tune lock follows `/features.mesh_tuning.enabled` at runtime (§3.9).
   */
  readonly VITE_FEATURE_REFINEMENT_PREVIEW?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
