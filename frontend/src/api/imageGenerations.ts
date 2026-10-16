/**
 * Text-to-image runs (`ig_`, part-aware DESIGN §4.4, §6.2): the review-first prompt flow
 * (`POST /image-generations` → `…/candidates/{index}/use` → `POST /jobs` or `POST /workflows`).
 * Candidate images are read through their payload URLs (`api/files.fileUrl`), never built here.
 */
import { apiClient } from './client'
import type {
  ImageGenerationOut,
  ImageGenerationRequest,
  ItemsOut,
  RunStatus,
  UseCandidateOut,
  UseCandidateRequest,
} from './partAwareTypes'

export interface ListImageGenerationsParams {
  /** 1..100 (server default 20). */
  limit?: number
  status?: RunStatus
}

const generationPath = (generationId: string) => `/image-generations/${encodeURIComponent(generationId)}`

/** Queue a run (202). 422 `validation_error`; 503 `feature_disabled` / `capability_unavailable`; 429 `queue_full`. */
export async function createImageGeneration(body: ImageGenerationRequest): Promise<ImageGenerationOut> {
  const response = await apiClient.post<ImageGenerationOut>('/image-generations', body)
  return response.data
}

/** Newest first. */
export async function listImageGenerations(params: ListImageGenerationsParams = {}): Promise<ImageGenerationOut[]> {
  const response = await apiClient.get<ItemsOut<ImageGenerationOut>>('/image-generations', { params })
  return response.data.items
}

export async function getImageGeneration(generationId: string): Promise<ImageGenerationOut> {
  const response = await apiClient.get<ImageGenerationOut>(generationPath(generationId))
  return response.data
}

/** 202; 409 when the run is already terminal. */
export async function cancelImageGeneration(generationId: string): Promise<ImageGenerationOut> {
  const response = await apiClient.post<ImageGenerationOut>(`${generationPath(generationId)}/cancel`)
  return response.data
}

/** 204; 409 while active or referenced by an active workflow. Not called by the v1 UI (retention handles it). */
export async function deleteImageGeneration(generationId: string): Promise<void> {
  await apiClient.delete(generationPath(generationId))
}

/**
 * Import a completed candidate as a reference image (201, same body as an upload). Without `reference_set_id`
 * the server creates a new set. Not named `use…`: React tooling treats such functions as hooks.
 */
export async function postCandidateUse(
  generationId: string,
  index: number,
  body: UseCandidateRequest,
): Promise<UseCandidateOut> {
  const response = await apiClient.post<UseCandidateOut>(
    `${generationPath(generationId)}/candidates/${encodeURIComponent(String(index))}/use`,
    body,
  )
  return response.data
}
