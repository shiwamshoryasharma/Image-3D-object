/**
 * Downloads through payload URLs only (UI_DESIGN §2.3, §6.7, UI9): the frontend never builds artifact paths.
 * Layer (§1.4): stores, api, lib; no React.
 */
import { toApiError } from '../api/client'
import { downloadFile } from '../api/files'
import { downloadJobGlb } from '../api/jobs'
import { errorText } from '../lib/errorText.ts'
import { saveBlob } from '../lib/download'
import { useJobStore } from '../store/jobStore'
import { toast } from '../store/toastStore'

export type ArtifactRef =
  | { kind: 'job_glb'; jobId: string }
  | {
      kind: 'parts_glb' | 'parts_zip' | 'part_glb' | 'mask_png' | 'manifest' | 'overlay_png'
      url: string
      fallbackName: string
      sizeHint: number | null
    }

/** Download an artifact with progress (percent or `null` while unknown); errors toast "Download failed". */
export async function downloadArtifact(
  ref: ArtifactRef,
  onProgress?: (percent: number | null) => void,
  signal?: AbortSignal,
): Promise<boolean> {
  try {
    if (ref.kind === 'job_glb') {
      const job = useJobStore.getState().jobs[ref.jobId]
      if (!job?.result) {
        toast.error('Download failed', 'This model has no result to download.')
        return false
      }
      const { blob, filename } = await downloadJobGlb({ id: job.id, result: job.result }, { onProgress, signal })
      saveBlob(blob, filename)
      return true
    }
    const { blob, filename } = await downloadFile(ref.url, {
      fallbackName: ref.fallbackName,
      sizeHint: ref.sizeHint,
      onProgress,
      signal,
    })
    saveBlob(blob, filename)
    return true
  } catch (error) {
    if (signal?.aborted) return false
    toast.error('Download failed', errorText(await toApiError(error)))
    return false
  }
}
