/**
 * Part analysis runs (`pa_`, part-aware DESIGN §4.6, §6.4): part decomposition of a finished job.
 * Result files (assembled/per-part GLBs, ZIP, masks, thumbnails, previews) are read through their payload URLs
 * (`api/files.fileUrl` / `downloadFile`), never built here.
 */
import { ApiError, absolutePathConfig, apiClient } from './client'
import type { ItemsOut, PartAnalysisOptions, PartAnalysisOut } from './partAwareTypes'

/** A manifest buffer (`face_labels`, `face_part`, …) with its `X-Img3d-Dtype` / `X-Img3d-Count` headers. */
export interface PartBufferData {
  data: ArrayBuffer
  dtype: string | null
  count: number | null
}

const jobAnalysesPath = (jobId: string) => `/jobs/${encodeURIComponent(jobId)}/part-analyses`
const analysisPath = (analysisId: string) => `/part-analyses/${encodeURIComponent(analysisId)}`

/** `GET …/latest` answers 404 `not_found` with `details.reason: "none"` when the job has no completed analysis. */
function isNoCompletedAnalysis(error: unknown): boolean {
  if (!(error instanceof ApiError) || error.status !== 404) return false
  const details = error.details
  return typeof details === 'object' && details !== null && (details as { reason?: unknown }).reason === 'none'
}

/**
 * Queue an analysis of a completed job (202). Without `options` no body is sent and the server defaults apply.
 * 404 job; 409 job not completed / `model.glb` missing / bad `segmentation_id`; 422; 429; 503.
 */
export async function createPartAnalysis(jobId: string, options?: PartAnalysisOptions): Promise<PartAnalysisOut> {
  const response = await apiClient.post<PartAnalysisOut>(jobAnalysesPath(jobId), options)
  return response.data
}

/** Analyses of a job, newest first. */
export async function listPartAnalyses(jobId: string): Promise<PartAnalysisOut[]> {
  const response = await apiClient.get<ItemsOut<PartAnalysisOut>>(jobAnalysesPath(jobId))
  return response.data.items
}

/**
 * The job's newest **completed** analysis, or `null` when it has none. Any other failure (including a 404 for an
 * unknown job) throws {@link ApiError}.
 */
export async function getLatestPartAnalysis(jobId: string): Promise<PartAnalysisOut | null> {
  try {
    const response = await apiClient.get<PartAnalysisOut>(`${jobAnalysesPath(jobId)}/latest`)
    return response.data
  } catch (error) {
    if (isNoCompletedAnalysis(error)) return null
    throw error
  }
}

export async function getPartAnalysis(analysisId: string): Promise<PartAnalysisOut> {
  const response = await apiClient.get<PartAnalysisOut>(analysisPath(analysisId))
  return response.data
}

/** 202; 409 when the run is already terminal. */
export async function cancelPartAnalysis(analysisId: string): Promise<PartAnalysisOut> {
  const response = await apiClient.post<PartAnalysisOut>(`${analysisPath(analysisId)}/cancel`)
  return response.data
}

/** 204 (also purges the server's export cache); 409 while active. */
export async function deletePartAnalysis(analysisId: string): Promise<void> {
  await apiClient.delete(analysisPath(analysisId))
}

/**
 * The raw `img3d.parts/1` manifest at the payload `result.manifest_url` (F route, header auth). Returned unparsed:
 * `lib/partsModel` validates and normalises it.
 */
export async function getPartManifest(manifestUrl: string): Promise<unknown> {
  const response = await apiClient.get<unknown>(manifestUrl, absolutePathConfig)
  return response.data
}

/**
 * One manifest buffer (`application/octet-stream`). Not called by the v1 UI. Fetched as a Blob so a JSON error
 * body still becomes a proper {@link ApiError}.
 */
export async function getPartAnalysisBuffer(
  analysisId: string,
  bufferId: string,
  signal?: AbortSignal,
): Promise<PartBufferData> {
  const response = await apiClient.get<Blob>(`${analysisPath(analysisId)}/buffers/${encodeURIComponent(bufferId)}`, {
    responseType: 'blob',
    timeout: 0,
    signal,
  })
  const dtype = response.headers['x-img3d-dtype']
  const countHeader = response.headers['x-img3d-count']
  const count = typeof countHeader === 'string' && /^\d+$/.test(countHeader.trim()) ? Number(countHeader) : null
  return {
    data: await response.data.arrayBuffer(),
    dtype: typeof dtype === 'string' && dtype ? dtype : null,
    count,
  }
}

/** Tail (≤ 256 KB) of a failed run's log. Not called by the v1 UI. */
export async function getPartAnalysisLog(analysisId: string): Promise<string> {
  const response = await apiClient.get<Blob>(`${analysisPath(analysisId)}/log`, { responseType: 'blob' })
  return response.data.text()
}
