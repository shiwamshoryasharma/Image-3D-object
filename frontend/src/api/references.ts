/** Reference-set endpoints (§7.3). */
import type { AxiosProgressEvent } from 'axios'

import { apiClient, resolveApiUrl } from './client'
import type {
  CreateReferenceSetRequest,
  ReferenceImage,
  ReferenceSet,
  ReferenceSetList,
  ReferenceSlot,
  UpdateReferenceImageRequest,
  UploadReferenceImageResponse,
} from './types'

const setPath = (setId: string) => `/reference-sets/${encodeURIComponent(setId)}`
const imagePath = (setId: string, imageId: string) => `${setPath(setId)}/images/${encodeURIComponent(imageId)}`

export async function createReferenceSet(body: CreateReferenceSetRequest = {}): Promise<ReferenceSet> {
  const response = await apiClient.post<ReferenceSet>('/reference-sets', body)
  return response.data
}

export async function listReferenceSets(): Promise<ReferenceSet[]> {
  const response = await apiClient.get<ReferenceSetList>('/reference-sets')
  return response.data.items
}

export async function getReferenceSet(setId: string): Promise<ReferenceSet> {
  const response = await apiClient.get<ReferenceSet>(setPath(setId))
  return response.data
}

export async function deleteReferenceSet(setId: string): Promise<void> {
  await apiClient.delete(setPath(setId))
}

export interface UploadReferenceImageInput {
  file: File
  slot: ReferenceSlot
  label?: string | null
  makePrimary?: boolean
}

export interface UploadOptions {
  /** Upload progress in percent (0..100). */
  onProgress?: (percent: number) => void
  signal?: AbortSignal
}

export async function uploadReferenceImage(
  setId: string,
  input: UploadReferenceImageInput,
  options: UploadOptions = {},
): Promise<UploadReferenceImageResponse> {
  const form = new FormData()
  form.append('file', input.file, input.file.name)
  form.append('slot', input.slot)
  if (input.label) form.append('label', input.label)
  if (input.makePrimary !== undefined) form.append('make_primary', input.makePrimary ? 'true' : 'false')

  const response = await apiClient.post<UploadReferenceImageResponse>(`${setPath(setId)}/images`, form, {
    signal: options.signal,
    timeout: 120_000,
    onUploadProgress: (event: AxiosProgressEvent) => {
      if (!options.onProgress) return
      const total = event.total ?? input.file.size
      if (total > 0) options.onProgress(Math.min(100, (event.loaded / total) * 100))
    },
  })
  return response.data
}

export async function updateReferenceImage(
  setId: string,
  imageId: string,
  body: UpdateReferenceImageRequest,
): Promise<ReferenceSet> {
  const response = await apiClient.patch<ReferenceSet>(imagePath(setId, imageId), body)
  return response.data
}

export async function deleteReferenceImage(setId: string, imageId: string): Promise<ReferenceSet> {
  const response = await apiClient.delete<ReferenceSet>(imagePath(setId, imageId))
  return response.data
}

export async function setPrimaryImage(setId: string, imageId: string): Promise<ReferenceSet> {
  const response = await apiClient.put<ReferenceSet>(`${setPath(setId)}/primary`, { image_id: imageId })
  return response.data
}

/** Thumbnail URL usable in `<img>` (token-aware). */
export function referenceThumbnailUrl(image: ReferenceImage): string {
  return resolveApiUrl(image.urls.thumbnail)
}

/** Original file URL usable in `<img>`/`<a>` (token-aware). */
export function referenceFileUrl(image: ReferenceImage): string {
  return resolveApiUrl(image.urls.file)
}
