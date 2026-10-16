/**
 * File routes of the part-aware API (DESIGN §6.0 "F" auth: header or `?token=`).
 *
 * The UI never builds part, mask, GLB, candidate or overlay paths: it only resolves the URLs found in payloads
 * (`ImageCandidateOut.url`, `PartAnalysisResultOut.assembled_url`, `WorkflowStepOut.artifacts[].url`, …).
 */
import type { AxiosProgressEvent } from 'axios'

import { absolutePathConfig, apiClient, resolveApiUrl } from './client'

/** Token-aware URL of a payload file URL, for consumers that cannot send headers (`<img>`, GLTFLoader, `<a>`). */
export function fileUrl(payloadUrl: string): string {
  return resolveApiUrl(payloadUrl)
}

/**
 * Append query parameters to a payload URL. Only DESIGN-defined parameters are passed by callers
 * (today: `include_base` on `PartAnalysisResultOut.zip_url`).
 */
export function withQuery(url: string, params: Record<string, string>): string {
  const query = Object.entries(params)
    .map(([key, value]) => `${encodeURIComponent(key)}=${encodeURIComponent(value)}`)
    .join('&')
  if (!query) return url
  return `${url}${url.includes('?') ? '&' : '?'}${query}`
}

export interface DownloadFileOptions {
  /** Used when the response has no usable `Content-Disposition` filename. */
  fallbackName: string
  /** Expected size in bytes, used for progress when the response has no `Content-Length`. */
  sizeHint?: number | null
  /** Download progress in percent (0..100), or `null` while the total size is unknown. */
  onProgress?: (percent: number | null) => void
  signal?: AbortSignal
}

/** Filename suggested by a `Content-Disposition` header (`filename="…"` or `filename*=UTF-8''…`). */
function filenameFromDisposition(header: unknown, fallback: string): string {
  const match = /filename\*?=(?:UTF-8'')?"?([^";]+)"?/i.exec(String(header ?? ''))
  if (!match?.[1]) return fallback
  try {
    return decodeURIComponent(match[1])
  } catch {
    return match[1]
  }
}

/**
 * Download any F attachment URL from a payload (assembled/per-part GLB, ZIP bundle, manifest, mask) as a Blob
 * through axios, so the Authorization header is sent. Per-part GLBs may be built on demand server-side, so there
 * is no timeout; cancel with `signal`.
 */
export async function downloadFile(
  payloadUrl: string,
  options: DownloadFileOptions,
): Promise<{ blob: Blob; filename: string }> {
  const { fallbackName, sizeHint, onProgress, signal } = options
  const response = await apiClient.get<Blob>(payloadUrl, {
    ...absolutePathConfig,
    responseType: 'blob',
    timeout: 0,
    signal,
    onDownloadProgress: (event: AxiosProgressEvent) => {
      if (!onProgress) return
      const total = event.total ?? sizeHint ?? 0
      onProgress(total ? Math.min(100, (event.loaded / total) * 100) : null)
    },
  })
  return { blob: response.data, filename: filenameFromDisposition(response.headers['content-disposition'], fallbackName) }
}
