/** Job endpoints (§7.4). */
import type { AxiosProgressEvent } from 'axios'

import { API_PREFIX, absolutePathConfig, apiClient, resolveApiUrl } from './client'
import type { CreateJobRequest, Job, JobList, JobResult, ListJobsParams } from './types'

const jobPath = (jobId: string) => `/jobs/${encodeURIComponent(jobId)}`

export async function createJob(body: CreateJobRequest): Promise<Job> {
  const response = await apiClient.post<Job>('/jobs', body)
  return response.data
}

export async function listJobs(params: ListJobsParams = {}): Promise<Job[]> {
  const response = await apiClient.get<JobList>('/jobs', { params })
  return response.data.items
}

export async function getJob(jobId: string): Promise<Job> {
  const response = await apiClient.get<Job>(jobPath(jobId))
  return response.data
}

export async function cancelJob(jobId: string): Promise<Job> {
  const response = await apiClient.post<Job>(`${jobPath(jobId)}/cancel`)
  return response.data
}

export async function deleteJob(jobId: string): Promise<void> {
  await apiClient.delete(jobPath(jobId))
}

export async function getJobResult(jobId: string): Promise<JobResult> {
  const response = await apiClient.get<JobResult>(`${jobPath(jobId)}/result`)
  return response.data
}

/** Viewer URL for a completed job's GLB (token-aware, for GLTFLoader). */
export function jobModelUrl(result: JobResult): string {
  return resolveApiUrl(result.model_url)
}

/**
 * Preview image URL (processed conditioning image). Prefers `result.preview_url`; otherwise uses the
 * contract endpoint `GET /jobs/{id}/preview`, which returns 404 until the preview has been produced.
 */
export function jobPreviewUrl(job: Pick<Job, 'id' | 'result'>): string {
  return resolveApiUrl(job.result?.preview_url ?? `${API_PREFIX}${jobPath(job.id)}/preview`)
}

export interface DownloadOptions {
  onProgress?: (percent: number | null) => void
  signal?: AbortSignal
}

/**
 * Download the exported GLB as a Blob through axios so the Authorization header is sent.
 * Returns the blob and the filename suggested by the server (`img3d_<job_id>.glb`).
 */
export async function downloadJobGlb(
  job: Pick<Job, 'id'> & { result: JobResult },
  options: DownloadOptions = {},
): Promise<{ blob: Blob; filename: string }> {
  const response = await apiClient.get<Blob>(job.result.download_url, {
    ...absolutePathConfig,
    responseType: 'blob',
    timeout: 0,
    signal: options.signal,
    onDownloadProgress: (event: AxiosProgressEvent) => {
      const total = event.total ?? job.result.stats.file_size_bytes
      options.onProgress?.(total ? Math.min(100, (event.loaded / total) * 100) : null)
    },
  })
  const disposition = String(response.headers['content-disposition'] ?? '')
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)
  const filename = match?.[1] ? decodeURIComponent(match[1]) : `img3d_${job.id}.glb`
  return { blob: response.data, filename }
}
