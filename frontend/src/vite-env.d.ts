/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Optional absolute origin of the backend (e.g. `http://127.0.0.1:8000`). Empty → same origin (dev proxy). */
  readonly VITE_API_BASE_URL?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
