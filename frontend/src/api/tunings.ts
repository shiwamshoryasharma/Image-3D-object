/** Auto Mesh Tuner run endpoints (DESIGN.md §6). */
import type { AxiosProgressEvent } from 'axios'

import { absolutePathConfig, apiClient, resolveApiUrl } from './client'
import type { DownloadOptions } from './jobs'
import type { TuningOptions, TuningPreview, TuningResult, TuningRun, TuningRunList } from './types'

const tuningPath = (tuningId: string) => `/tunings/${encodeURIComponent(tuningId)}`

/** Queue an Auto Tune run for a completed job (202). 409 job not completed, 503 `blender_unavailable`, 429 queue full. */
export async function startTuning(jobId: string, options: TuningOptions): Promise<TuningRun> {
  const response = await apiClient.post<TuningRun>(`/jobs/${encodeURIComponent(jobId)}/tunings`, options)
  return response.data
}

/** Runs of a job, newest first. */
export async function listTunings(jobId: string): Promise<TuningRun[]> {
  const response = await apiClient.get<TuningRunList>(`/jobs/${encodeURIComponent(jobId)}/tunings`)
  return response.data.items
}

export async function getTuning(tuningId: string): Promise<TuningRun> {
  const response = await apiClient.get<TuningRun>(tuningPath(tuningId))
  return response.data
}

export async function cancelTuning(tuningId: string): Promise<TuningRun> {
  const response = await apiClient.post<TuningRun>(`${tuningPath(tuningId)}/cancel`)
  return response.data
}

/** Delete a finished run (409 while active). */
export async function deleteTuning(tuningId: string): Promise<void> {
  await apiClient.delete(tuningPath(tuningId))
}

/** Viewer URL of the tuned GLB (token-aware, unique per run so loader caches never collide). */
export function tunedModelUrl(result: TuningResult): string {
  return resolveApiUrl(result.model_url)
}

/** `<img>` URL of a before/after preview render. */
export function tuningPreviewUrl(preview: TuningPreview): string {
  return resolveApiUrl(preview.url)
}

/** `<a>` URL for the raw report / log (opened in a new tab). */
export function tuningFileUrl(path: string): string {
  return resolveApiUrl(path)
}

/**
 * Download the tuned GLB as a Blob through axios (Authorization header). Returns the blob and the server's
 * suggested filename (`img3d_<job>_tuned_<tn>.glb`).
 */
export async function downloadTunedGlb(
  run: Pick<TuningRun, 'id' | 'job_id'> & { result: TuningResult },
  options: DownloadOptions = {},
): Promise<{ blob: Blob; filename: string }> {
  const response = await apiClient.get<Blob>(run.result.download_url, {
    ...absolutePathConfig,
    responseType: 'blob',
    timeout: 0,
    signal: options.signal,
    onDownloadProgress: (event: AxiosProgressEvent) => {
      const total = event.total ?? run.result.stats?.file_size_bytes ?? 0
      options.onProgress?.(total ? Math.min(100, (event.loaded / total) * 100) : null)
    },
  })
  const disposition = String(response.headers['content-disposition'] ?? '')
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(disposition)
  const filename = match?.[1] ? decodeURIComponent(match[1]) : `img3d_${run.job_id}_tuned_${run.id}.glb`
  return { blob: response.data, filename }
}
