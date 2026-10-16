/**
 * Part commands (UI_DESIGN §6.2, §6.8). Layer (§1.4): stores, api, lib; no React.
 */
import type { ReferenceImage } from '../api/types'
import { buildPartAnalysisOptions, buildReferenceImageSegmentationSource, buildSegmentationRequest } from '../lib/workflowRequest.ts'
import { useFeaturesStore } from '../store/featuresStore'
import { useJobStore } from '../store/jobStore'
import { usePartOptionsStore } from '../store/partOptionsStore'
import { usePartStore } from '../store/partStore'
import { useReferenceStore } from '../store/referenceStore'
import { useRunStore } from '../store/runStore'
import { toast } from '../store/toastStore'

/** "Analyse parts" / "Analyse again with current options" / Node "Run from here" on the viewed, completed job (G8/G8a). */
export async function analyzePartsForViewedJob(): Promise<boolean> {
  const { viewedJobId, jobs } = useJobStore.getState()
  const job = viewedJobId ? jobs[viewedJobId] : undefined
  if (!job || job.status !== 'completed') {
    toast.warning('No finished model', 'Open a finished model in the viewer first.')
    return false
  }
  const features = useFeaturesStore.getState().snapshot
  const options = buildPartAnalysisOptions(usePartOptionsStore.getState().form, { features })
  return usePartStore.getState().requestAnalysis(job.id, options)
}

/** Segmentation preview of the front (primary) reference image with the current part options (display only). */
export async function previewSegmentation(image?: ReferenceImage): Promise<boolean> {
  const set = useReferenceStore.getState().set
  const target = image ?? set?.images.find((candidate) => candidate.id === set.primary_image_id) ?? set?.images[0]
  if (!set || !target) {
    toast.warning('Add a reference image', 'Upload an image to preview its segmentation.')
    return false
  }
  const features = useFeaturesStore.getState().snapshot
  const request = buildSegmentationRequest(
    buildReferenceImageSegmentationSource(set.id, target.id),
    usePartOptionsStore.getState().form,
    { features },
  )
  return (await useRunStore.getState().startSegmentationPreview(request)) !== null
}
