/**
 * Workflow, image-generation and segmentation runs of this session (UI_DESIGN §2.7; not persisted).
 * Updates arrive from the WS events (`workflow.updated`, `image_generation.updated`, `segmentation.updated`) and REST
 * (create responses, `loadIndex`, `resyncActive`). Every upsert runs the shared stale guard (`isStaleRun`).
 * `upsertWorkflow` adopts the reconstruction job of the workflow this session started, so the existing progress card,
 * toasts, viewer auto-open and history work unchanged for workflow jobs.
 * Store edges (§1.4): job, part, reference, input, features, announcer, toast.
 */
import { create } from 'zustand'

import { ApiError, toApiError } from '../api/client'
import {
  cancelImageGeneration,
  createImageGeneration,
  getImageGeneration,
  postCandidateUse,
} from '../api/imageGenerations'
import type {
  ImageGenerationOut,
  ImageGenerationRequest,
  SegmentationOut,
  SegmentationRequest,
  UseCandidateOut,
  WorkflowOut,
  WorkflowRequest,
} from '../api/partAwareTypes.ts'
import { cancelSegmentation, createSegmentation, getSegmentation } from '../api/segmentations'
import { cancelWorkflow, createWorkflow, getWorkflow, listWorkflows } from '../api/workflows'
import { describeError, errorText, type ErrorSubject } from '../lib/errorText.ts'
import { isStaleRun, isTerminalRunStatus } from '../lib/runSteps.ts'
import { buildUseCandidateRequest } from '../lib/workflowRequest.ts'
import { announce } from './announcerStore'
import { useFeaturesStore } from './featuresStore'
import { useInputStore } from './inputStore'
import { useJobStore } from './jobStore'
import { usePartStore } from './partStore'
import { toast } from './toastStore'

export type RunKindKey = 'workflow' | 'image_generation' | 'segmentation'

interface RunState {
  workflows: Record<string, WorkflowOut>
  imageGenerations: Record<string, ImageGenerationOut>
  segmentations: Record<string, SegmentationOut>
  /** The workflow this session started and still tracks. */
  activeWorkflowId: string | null
  /** The latest review-first image generation of this session. */
  reviewGenerationId: string | null
  /** The latest segmentation preview (display only; never sent in a workflow, DESIGN §4.7.1). */
  previewSegmentationId: string | null
  /** job id → workflow id (`GET /workflows?limit=100` + events). */
  workflowByJobId: Record<string, string>
  /** Jobs already adopted from workflows (idempotent). */
  adoptedJobIds: string[]
  submitting: boolean
  cancellingIds: string[]

  startWorkflow: (request: WorkflowRequest) => Promise<WorkflowOut | null>
  startImageGeneration: (request: ImageGenerationRequest) => Promise<ImageGenerationOut | null>
  /** `POST …/candidates/{index}/use` (G6). The caller adopts the returned set (`referenceStore.adoptSet`). */
  adoptCandidate: (generationId: string, index: number) => Promise<UseCandidateOut | null>
  startSegmentationPreview: (request: SegmentationRequest) => Promise<SegmentationOut | null>
  cancel: (kind: RunKindKey, id: string) => Promise<void>
  upsertWorkflow: (wf: WorkflowOut) => void
  upsertImageGeneration: (ig: ImageGenerationOut) => void
  upsertSegmentation: (sg: SegmentationOut) => void
  /** `GET /workflows?limit=100`: fills `workflowByJobId` (records only; no adoption, no toasts). */
  loadIndex: () => Promise<void>
  /** GET every non-terminal tracked run (on hello and in fallback polling). */
  resyncActive: () => Promise<void>
}

/** Map a create/cancel error onto the UI (§2.11): inline prompt error, features refresh, dev log, toast. */
function reportError(apiError: ApiError, subject: ErrorSubject, title: string): void {
  const report = describeError(apiError, { subject })
  if (report.field === 'prompt') useInputStore.getState().setPromptError(report.text)
  if (report.refreshFeatures) void useFeaturesStore.getState().refresh()
  if (report.logBody && import.meta.env.DEV) console.warn(`[img3d] ${title}: request rejected`, apiError.details)
  if (!report.expected) toast.error(title, report.text)
}

function indexJob(index: Record<string, string>, wf: WorkflowOut): Record<string, string> {
  const jobId = wf.outputs.job_id
  return jobId && index[jobId] !== wf.id ? { ...index, [jobId]: wf.id } : index
}

const withoutId = (ids: string[], id: string): string[] => (ids.includes(id) ? ids.filter((other) => other !== id) : ids)

export const useRunStore = create<RunState>()((set, get) => ({
  workflows: {},
  imageGenerations: {},
  segmentations: {},
  activeWorkflowId: null,
  reviewGenerationId: null,
  previewSegmentationId: null,
  workflowByJobId: {},
  adoptedJobIds: [],
  submitting: false,
  cancellingIds: [],

  startWorkflow: async (request) => {
    if (get().submitting) return null
    set({ submitting: true })
    try {
      const wf = await createWorkflow(request)
      useInputStore.getState().setPromptError(null)
      set({ activeWorkflowId: wf.id })
      get().upsertWorkflow(wf)
      return wf
    } catch (error) {
      reportError(await toApiError(error), 'workflow', 'Could not start generation')
      return null
    } finally {
      set({ submitting: false })
    }
  },

  startImageGeneration: async (request) => {
    if (get().submitting) return null
    set({ submitting: true })
    try {
      const ig = await createImageGeneration(request)
      useInputStore.getState().setPromptError(null)
      set({ reviewGenerationId: ig.id })
      get().upsertImageGeneration(ig)
      return ig
    } catch (error) {
      reportError(await toApiError(error), 'image_generation', 'Could not start generation')
      return null
    } finally {
      set({ submitting: false })
    }
  },

  adoptCandidate: async (generationId, index) => {
    try {
      return await postCandidateUse(generationId, index, buildUseCandidateRequest())
    } catch (error) {
      const apiError = await toApiError(error)
      const report = describeError(apiError, { subject: 'candidate' })
      if (report.refreshRun) {
        try {
          get().upsertImageGeneration(await getImageGeneration(generationId))
        } catch {
          // The run is gone; the toast below says so.
        }
      }
      reportError(apiError, 'candidate', 'Could not use the image')
      return null
    }
  },

  startSegmentationPreview: async (request) => {
    try {
      const sg = await createSegmentation(request)
      set({ previewSegmentationId: sg.id })
      get().upsertSegmentation(sg)
      return sg
    } catch (error) {
      reportError(await toApiError(error), 'segmentation', 'Could not start the segmentation preview')
      return null
    }
  },

  cancel: async (kind, id) => {
    if (get().cancellingIds.includes(id)) return
    set((state) => ({ cancellingIds: [...state.cancellingIds, id] }))
    try {
      if (kind === 'workflow') get().upsertWorkflow(await cancelWorkflow(id))
      else if (kind === 'image_generation') get().upsertImageGeneration(await cancelImageGeneration(id))
      else get().upsertSegmentation(await cancelSegmentation(id))
    } catch (error) {
      toast.error('Could not cancel', errorText(await toApiError(error), { subject: kind }))
    } finally {
      set((state) => ({ cancellingIds: withoutId(state.cancellingIds, id) }))
    }
  },

  upsertWorkflow: (wf) => {
    // 1. Stale guard; store; index outputs.job_id.
    const previous = get().workflows[wf.id]
    if (previous && isStaleRun(previous, wf)) return
    set((state) => ({
      workflows: { ...state.workflows, [wf.id]: wf },
      workflowByJobId: indexJob(state.workflowByJobId, wf),
      cancellingIds: isTerminalRunStatus(wf.status) ? withoutId(state.cancellingIds, wf.id) : state.cancellingIds,
    }))
    if (wf.id !== get().activeWorkflowId) return

    // 2. Adopt the reconstruction job (progress card, toasts, viewer auto-open, history work unchanged).
    const resource = wf.steps.find((step) => step.id === 'reconstruction')?.resource
    if (resource?.kind === 'job' && !get().adoptedJobIds.includes(resource.id)) {
      set((state) => ({ adoptedJobIds: [...state.adoptedJobIds, resource.id] }))
      void useJobStore.getState().adopt(resource.id)
    }
    // 3. A prompt run's generated reference set does not replace the user's current set: nothing to do.
    // 4. Parts of the workflow's job.
    const { part_analysis_id: analysisId, job_id: jobId } = wf.outputs
    if (analysisId && jobId && previous?.outputs.part_analysis_id !== analysisId) {
      void usePartStore.getState().loadForJob(jobId, true)
    }
    // 5. Terminal transitions seen live.
    if (!previous || isTerminalRunStatus(previous.status) || !isTerminalRunStatus(wf.status)) return
    if (wf.status === 'completed') {
      if (wf.request.parts) {
        toast.success('Parts ready', 'The part-aware model is ready.')
        announce('Parts ready')
      }
    } else if (wf.status === 'failed') {
      const text = wf.error ? errorText(wf.error, { subject: 'workflow' }) : 'The generation failed.'
      toast.error('Generation failed', text)
      announce(`Generation failed. ${text}`, 'assertive')
    } else if (wf.status === 'cancelled') {
      toast.info('Generation cancelled')
      announce('Generation cancelled')
    }
  },

  upsertImageGeneration: (ig) => {
    const previous = get().imageGenerations[ig.id]
    if (previous && isStaleRun(previous, ig)) return
    set((state) => ({
      imageGenerations: { ...state.imageGenerations, [ig.id]: ig },
      cancellingIds: isTerminalRunStatus(ig.status) ? withoutId(state.cancellingIds, ig.id) : state.cancellingIds,
    }))
    // Review generations of this session: announce the live terminal transition.
    if (ig.id !== get().reviewGenerationId || !previous || isTerminalRunStatus(previous.status)) return
    if (ig.status === 'completed') announce('Images ready')
    else if (ig.status === 'failed') {
      const text = ig.error ? errorText(ig.error, { subject: 'image_generation' }) : 'The image generation failed.'
      toast.error('Image generation failed', text)
      announce(`Image generation failed. ${text}`, 'assertive')
    } else if (ig.status === 'cancelled') announce('Image generation cancelled')
  },

  upsertSegmentation: (sg) => {
    const previous = get().segmentations[sg.id]
    if (previous && isStaleRun(previous, sg)) return
    set((state) => ({
      segmentations: { ...state.segmentations, [sg.id]: sg },
      cancellingIds: isTerminalRunStatus(sg.status) ? withoutId(state.cancellingIds, sg.id) : state.cancellingIds,
    }))
  },

  loadIndex: async () => {
    try {
      const items = await listWorkflows({ limit: 100 })
      set((state) => {
        const workflows = { ...state.workflows }
        let workflowByJobId = state.workflowByJobId
        for (const wf of items) {
          const known = workflows[wf.id]
          if (!known || !isStaleRun(known, wf)) workflows[wf.id] = wf
          workflowByJobId = indexJob(workflowByJobId, workflows[wf.id])
        }
        return { workflows, workflowByJobId }
      })
    } catch {
      // Index only (badges, "Regenerate from prompt"); retried on the next sync.
    }
  },

  resyncActive: async () => {
    const { workflows, imageGenerations, segmentations } = get()
    const open = <T extends { id: string; status: WorkflowOut['status'] }>(records: Record<string, T>): string[] =>
      Object.values(records)
        .filter((record) => !isTerminalRunStatus(record.status))
        .map((record) => record.id)
    const settle = async <T,>(load: () => Promise<T>, apply: (value: T) => void): Promise<void> => {
      try {
        apply(await load())
      } catch {
        // The next poll, hello or WS event settles it.
      }
    }
    await Promise.all([
      ...open(workflows).map((id) => settle(() => getWorkflow(id), get().upsertWorkflow)),
      ...open(imageGenerations).map((id) => settle(() => getImageGeneration(id), get().upsertImageGeneration)),
      ...open(segmentations).map((id) => settle(() => getSegmentation(id), get().upsertSegmentation)),
    ])
  },
}))

export const selectActiveWorkflow = (state: RunState): WorkflowOut | null =>
  state.activeWorkflowId ? (state.workflows[state.activeWorkflowId] ?? null) : null

export const selectReviewGeneration = (state: RunState): ImageGenerationOut | null =>
  state.reviewGenerationId ? (state.imageGenerations[state.reviewGenerationId] ?? null) : null

export const selectPreviewSegmentation = (state: RunState): SegmentationOut | null =>
  state.previewSegmentationId ? (state.segmentations[state.previewSegmentationId] ?? null) : null
