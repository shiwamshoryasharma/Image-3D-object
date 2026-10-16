/**
 * Backend capabilities (`GET /features`, UI_DESIGN §2.5; not persisted). Status machine:
 *   loading → 200 ready (normalizeFeatures) · 404 legacy (LEGACY_FEATURES) · 401 unchanged (expire flow) ·
 *   other error → keep a previous `ready` snapshot (transient), else `error` (ERROR_FEATURES), retried on the next
 *   poll / hello / sync.
 * `snapshot` is replaced only in `refresh`/`apply`, so selectors returning `s.snapshot.prompt` etc. are stable.
 * The workflow catalog is loaded only by the Node shell (`ensureCatalog`) and on an ETag change once loaded.
 * This store imports no other store (§1.4): callers toast the errors that `loadModel`/`unloadModel` return.
 */
import { create } from 'zustand'

import { ApiError, toApiError } from '../api/client'
import { getFeatures, listModels, loadModel as postLoadModel, unloadModel as postUnloadModel } from '../api/features'
import type { CatalogOut, FeaturesOut, ModelOut } from '../api/partAwareTypes.ts'
import { getWorkflowCatalog } from '../api/workflows'
import {
  ERROR_FEATURES,
  LEGACY_FEATURES,
  LOADING_FEATURES,
  normalizeFeatures,
  type FeaturesSnapshot,
  type FeaturesStatus,
} from '../lib/featureFlags.ts'

export type CatalogStatus = 'idle' | 'loading' | 'ready' | 'unsupported' | 'error'

interface FeaturesState {
  status: FeaturesStatus
  snapshot: FeaturesSnapshot
  error: ApiError | null
  /** `/features.api.workflows`: the workflow routes exist (the catalog can be requested). */
  workflowsApi: boolean
  catalog: CatalogOut | null
  catalogEtag: string | null
  catalogStatus: CatalogStatus
  /** `GET /models?include_hidden=true`, loaded on demand by App settings ▸ Models. */
  allModels: ModelOut[] | null
  /** Model ids with a load/unload request in flight. */
  modelActionIds: string[]

  refresh: () => Promise<boolean>
  apply: (raw: FeaturesOut) => void
  ensureCatalog: () => Promise<void>
  loadAllModels: () => Promise<void>
  /** `POST /models/{id}/load`, then `refresh()`; resolves to the error (callers toast it) or `null`. */
  loadModel: (id: string) => Promise<ApiError | null>
  /** `POST /models/{id}/unload`, then `refresh()`; resolves to the error or `null`. */
  unloadModel: (id: string) => Promise<ApiError | null>
}

let refreshSeq = 0
let refreshInFlight: Promise<boolean> | null = null
let catalogInFlight: Promise<void> | null = null

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === 'object' && value !== null

function workflowsApiOf(raw: unknown): boolean {
  return isRecord(raw) && isRecord(raw.api) && raw.api.workflows === true
}

export const useFeaturesStore = create<FeaturesState>()((set, get) => {
  async function doRefresh(): Promise<boolean> {
    const seq = ++refreshSeq
    try {
      const result = await getFeatures()
      if (seq !== refreshSeq) return true
      if (result.kind === 'legacy') {
        set({ status: 'legacy', snapshot: LEGACY_FEATURES, error: null, workflowsApi: false })
        return true
      }
      get().apply(result.data)
      return true
    } catch (error) {
      const apiError = await toApiError(error)
      if (seq !== refreshSeq) return false
      // 401: the client hook runs the existing session-expiry flow; keep whatever we had.
      if (apiError.status === 401) return false
      if (get().status === 'ready') set({ error: apiError })
      else set({ status: 'error', snapshot: ERROR_FEATURES, error: apiError })
      return false
    }
  }

  async function modelAction(id: string, call: (id: string) => Promise<ModelOut>): Promise<ApiError | null> {
    if (get().modelActionIds.includes(id)) return null
    set((state) => ({ modelActionIds: [...state.modelActionIds, id] }))
    try {
      await call(id)
      await get().refresh()
      return null
    } catch (error) {
      return await toApiError(error)
    } finally {
      set((state) => ({ modelActionIds: state.modelActionIds.filter((other) => other !== id) }))
    }
  }

  return {
    status: 'loading',
    snapshot: LOADING_FEATURES,
    error: null,
    workflowsApi: false,
    catalog: null,
    catalogEtag: null,
    catalogStatus: 'idle',
    allModels: null,
    modelActionIds: [],

    refresh: () => {
      // Coalesce bursts (sync + hello + poll arriving together) into one request.
      if (refreshInFlight) return refreshInFlight
      const pending = doRefresh().finally(() => {
        refreshInFlight = null
      })
      refreshInFlight = pending
      return pending
    },

    apply: (raw) => {
      const previous = get().snapshot
      const snapshot = normalizeFeatures(raw)
      set({ status: snapshot.status, snapshot, error: null, workflowsApi: workflowsApiOf(raw) })
      const etagChanged = snapshot.workflow.catalogEtag !== previous.workflow.catalogEtag
      if (etagChanged && get().catalogStatus !== 'idle') void get().ensureCatalog()
    },

    ensureCatalog: () => {
      const { snapshot, workflowsApi } = get()
      if (snapshot.status !== 'ready' || !workflowsApi) {
        if (snapshot.status === 'legacy' || (snapshot.status === 'ready' && !workflowsApi)) {
          set((state) => (state.catalogStatus === 'unsupported' ? state : { catalogStatus: 'unsupported' }))
        }
        return Promise.resolve()
      }
      if (catalogInFlight) return catalogInFlight
      const run = async (): Promise<void> => {
        set({ catalogStatus: 'loading' })
        try {
          const { catalog, catalogEtag } = get()
          const result = await getWorkflowCatalog(catalog ? catalogEtag : null)
          if (result.kind === 'not_modified') set({ catalogStatus: get().catalog ? 'ready' : 'error' })
          else set({ catalog: result.data, catalogEtag: result.etag, catalogStatus: 'ready' })
        } catch (error) {
          const apiError = await toApiError(error)
          set({ catalogStatus: apiError.status === 404 ? 'unsupported' : 'error' })
        }
      }
      catalogInFlight = run().finally(() => {
        catalogInFlight = null
      })
      return catalogInFlight
    },

    loadAllModels: async () => {
      try {
        const allModels = await listModels({ include_hidden: true })
        set({ allModels })
      } catch (error) {
        set({ error: await toApiError(error) })
      }
    },

    loadModel: (id) => modelAction(id, postLoadModel),
    unloadModel: (id) => modelAction(id, postUnloadModel),
  }
})

/** Selector helpers (stable references: the snapshot is replaced only by refresh/apply). */
export const selectFeatures = (state: FeaturesState): FeaturesSnapshot => state.snapshot
export const selectFeaturesStatus = (state: FeaturesState): FeaturesStatus => state.status
