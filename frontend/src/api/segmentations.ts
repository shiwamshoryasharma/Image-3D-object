/**
 * 2D segmentation runs (`sg_`, part-aware DESIGN §4.5, §6.3): the segmentation preview before reconstruction.
 * The overlay and label masks are read through their payload URLs (`api/files.fileUrl`).
 */
import { apiClient } from './client'
import type { ItemsOut, RunStatus, SegmentationOut, SegmentationRequest } from './partAwareTypes'

export interface ListSegmentationsParams {
  /** 1..100 (server default 20). */
  limit?: number
  status?: RunStatus
  reference_set_id?: string
  job_id?: string
}

const segmentationPath = (segmentationId: string) => `/segmentations/${encodeURIComponent(segmentationId)}`

/** Queue a run (202). 404 set/image/job; 409 job not completed; 429; 503 `feature_disabled` / `capability_unavailable`. */
export async function createSegmentation(body: SegmentationRequest): Promise<SegmentationOut> {
  const response = await apiClient.post<SegmentationOut>('/segmentations', body)
  return response.data
}

/** Newest first. */
export async function listSegmentations(params: ListSegmentationsParams = {}): Promise<SegmentationOut[]> {
  const response = await apiClient.get<ItemsOut<SegmentationOut>>('/segmentations', { params })
  return response.data.items
}

export async function getSegmentation(segmentationId: string): Promise<SegmentationOut> {
  const response = await apiClient.get<SegmentationOut>(segmentationPath(segmentationId))
  return response.data
}

/** 202; 409 when the run is already terminal. */
export async function cancelSegmentation(segmentationId: string): Promise<SegmentationOut> {
  const response = await apiClient.post<SegmentationOut>(`${segmentationPath(segmentationId)}/cancel`)
  return response.data
}

/** 204; 409 while active or used by an active workflow. Not called by the v1 UI (retention handles it). */
export async function deleteSegmentation(segmentationId: string): Promise<void> {
  await apiClient.delete(segmentationPath(segmentationId))
}

/** 16-bit label image (pixel value = taxonomy value; 409 until completed). Not called by the v1 UI. */
export async function getSegmentationLabels(segmentationId: string, signal?: AbortSignal): Promise<Blob> {
  const response = await apiClient.get<Blob>(`${segmentationPath(segmentationId)}/labels`, {
    responseType: 'blob',
    signal,
  })
  return response.data
}

/** The source snapshot the run segmented (PNG). Not called by the v1 UI. */
export async function getSegmentationSource(segmentationId: string, signal?: AbortSignal): Promise<Blob> {
  const response = await apiClient.get<Blob>(`${segmentationPath(segmentationId)}/source`, {
    responseType: 'blob',
    signal,
  })
  return response.data
}
