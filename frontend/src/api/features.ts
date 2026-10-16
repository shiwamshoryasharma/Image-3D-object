/** Discovery endpoints (part-aware DESIGN §6.1): `/features` and the read-only model registry. */
import { ApiError, apiClient } from './client'
import type { FeaturesOut, ItemsOut, ModelKind, ModelOut } from './partAwareTypes'

/** `legacy` = the backend predates the part-aware API (`GET /features` → 404): image-to-3D only. */
export type FeaturesResult = { kind: 'ok'; data: FeaturesOut } | { kind: 'legacy' }

export interface ListModelsParams {
  kind?: ModelKind
  /** Include `hidden_by_default` models (future 3D entries); default false. */
  include_hidden?: boolean
}

const modelPath = (modelId: string) => `/models/${encodeURIComponent(modelId)}`

/**
 * Capability snapshot. A 404 means a legacy backend and resolves to `{kind: 'legacy'}` instead of throwing;
 * every other failure throws {@link ApiError} (a 401 also runs the client's session-expired hook).
 */
export async function getFeatures(): Promise<FeaturesResult> {
  try {
    const response = await apiClient.get<FeaturesOut>('/features')
    return { kind: 'ok', data: response.data }
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return { kind: 'legacy' }
    throw error
  }
}

export async function listModels(params: ListModelsParams = {}): Promise<ModelOut[]> {
  const response = await apiClient.get<ItemsOut<ModelOut>>('/models', { params })
  return response.data.items
}

export async function getModel(modelId: string): Promise<ModelOut> {
  const response = await apiClient.get<ModelOut>(modelPath(modelId))
  return response.data
}

/** Warm-up (202, state `loading`). 409 already loading or in use; 503 `model_unavailable` / `feature_disabled`. */
export async function loadModel(modelId: string): Promise<ModelOut> {
  const response = await apiClient.post<ModelOut>(`${modelPath(modelId)}/load`)
  return response.data
}

/** Release the model's memory (202). 409 while the running run uses it. */
export async function unloadModel(modelId: string): Promise<ModelOut> {
  const response = await apiClient.post<ModelOut>(`${modelPath(modelId)}/unload`)
  return response.data
}
