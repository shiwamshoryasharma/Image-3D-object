/** Blender runtime endpoints (Auto Mesh Tuner DESIGN.md §6): detection, selection, official portable download. */
import { apiClient } from './client'
import type {
  BlenderDownloadRequest,
  BlenderRelease,
  BlenderRuntimeStatus,
  BlenderSelectionRequest,
} from './types'

/** Detection executes every candidate `blender.exe --version` (≈0.1–0.2 s each, 20 s timeout per probe). */
const DETECT_TIMEOUT_MS = 120_000
/** Release resolution reads the official download.blender.org listing + checksum file. */
const RELEASE_TIMEOUT_MS = 60_000

export async function getBlenderRuntime(): Promise<BlenderRuntimeStatus> {
  const response = await apiClient.get<BlenderRuntimeStatus>('/blender/runtime')
  return response.data
}

/** Force a rescan of all Blender installations. */
export async function detectBlender(): Promise<BlenderRuntimeStatus> {
  const response = await apiClient.post<BlenderRuntimeStatus>('/blender/runtime/detect', undefined, {
    timeout: DETECT_TIMEOUT_MS,
  })
  return response.data
}

/**
 * Change the runtime mode, optionally pinning a specific executable ("Change Runtime").
 * 404 `blender_not_found`, 422 `blender_incompatible`.
 */
export async function selectBlenderRuntime(body: BlenderSelectionRequest): Promise<BlenderRuntimeStatus> {
  const response = await apiClient.put<BlenderRuntimeStatus>('/blender/runtime/selection', body, {
    timeout: DETECT_TIMEOUT_MS,
  })
  return response.data
}

/** Resolve the newest official portable release (no download). 502 `blender_release_unresolved`. */
export async function resolveBlenderRelease(): Promise<BlenderRelease> {
  const response = await apiClient.post<BlenderRelease>('/blender/runtime/release', undefined, {
    timeout: RELEASE_TIMEOUT_MS,
  })
  return response.data
}

/**
 * Start the official portable download on the backend (202). Only ever called after an explicit user
 * confirmation; the request itself carries `confirm: true`. 409 in progress, 403 disabled.
 */
export async function startBlenderDownload(version?: string | null): Promise<BlenderRuntimeStatus> {
  const body: BlenderDownloadRequest = version ? { confirm: true, version } : { confirm: true }
  const response = await apiClient.post<BlenderRuntimeStatus>('/blender/runtime/download', body, {
    timeout: RELEASE_TIMEOUT_MS,
  })
  return response.data
}

/** Cancel a running download (202). */
export async function cancelBlenderDownload(): Promise<BlenderRuntimeStatus> {
  const response = await apiClient.delete<BlenderRuntimeStatus>('/blender/runtime/download')
  return response.data
}
