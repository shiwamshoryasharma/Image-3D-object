/**
 * Jobs: history cache, the active (tracked) job, the job shown in the viewer, and job actions.
 * Updates arrive from the WebSocket (`job.updated`) and from REST polling (fallback/safety).
 */
import { create } from 'zustand'

import { ApiError, toApiError } from '../api/client'
import { cancelJob, createJob, deleteJob, getJob, listJobs } from '../api/jobs'
import { TERMINAL_STATUSES, type CreateJobRequest, type Job } from '../api/types'
import { STORAGE_KEYS, readString, writeString } from '../lib/storage'
import { useReferenceStore } from './referenceStore'
import { toRequestSettings, useSettingsStore } from './settingsStore'
import { useSystemStore } from './systemStore'
import { toast } from './toastStore'

const HISTORY_LIMIT = 30

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

interface JobState {
  jobs: Record<string, Job>
  /** History order (newest first). */
  order: string[]
  historyStatus: LoadStatus
  historyError: ApiError | null
  /** Job tracked by the progress panel. */
  activeJobId: string | null
  /** Job whose result is shown in the viewer. */
  viewedJobId: string | null
  submitting: boolean
  cancellingIds: string[]
  deletingIds: string[]

  loadHistory: () => Promise<boolean>
  refreshJob: (jobId: string) => Promise<boolean>
  upsertJob: (job: Job) => void
  submit: () => Promise<boolean>
  retry: (job: Job) => Promise<boolean>
  cancel: (jobId: string) => Promise<void>
  remove: (jobId: string) => Promise<void>
  view: (jobId: string | null) => void
}

export const isTerminal = (job: Pick<Job, 'status'>): boolean => TERMINAL_STATUSES.has(job.status)

function sortByCreated(jobs: Record<string, Job>, ids: Iterable<string>): string[] {
  return [...new Set(ids)]
    .filter((id) => jobs[id])
    .sort((a, b) => Date.parse(jobs[b].created_at) - Date.parse(jobs[a].created_at))
}

const STATUS_RANK: Record<Job['status'], number> = { queued: 0, running: 1, completed: 2, failed: 2, cancelled: 2 }

/**
 * True when `next` is older than what we already have (e.g. a slow POST/poll response arriving after a
 * newer WebSocket update). Status never moves backwards; progress never goes back in time.
 */
function isStale(previous: Job, next: Job): boolean {
  if (STATUS_RANK[next.status] < STATUS_RANK[previous.status]) return true
  if (isTerminal(previous)) return next.status !== previous.status
  if (next.status !== previous.status) return false
  return Date.parse(next.progress.updated_at) < Date.parse(previous.progress.updated_at)
}

function persistViewed(jobId: string | null) {
  writeString(STORAGE_KEYS.viewedJobId, jobId)
}

function notifyTransition(previous: Job | undefined, next: Job) {
  if (!previous || isTerminal(previous) || !isTerminal(next)) return
  const shortId = next.id.replace(/^job_/, '').slice(0, 6)
  if (next.status === 'completed') toast.success('3D model ready', `Job ${shortId} finished successfully.`)
  else if (next.status === 'failed') toast.error('Generation failed', next.error?.message ?? `Job ${shortId} failed.`)
  else if (next.status === 'cancelled') toast.info('Generation cancelled', `Job ${shortId} was cancelled.`)
}

async function submitRequest(request: CreateJobRequest): Promise<Job> {
  return createJob(request)
}

function describeSubmitError(error: ApiError): string {
  if (error.status === 429) return 'The generation queue is full. Wait for running jobs to finish.'
  if (error.isUnreachable) return 'The backend is unreachable.'
  return error.message
}

export const useJobStore = create<JobState>()((set, get) => ({
  jobs: {},
  order: [],
  historyStatus: 'idle',
  historyError: null,
  activeJobId: null,
  viewedJobId: null,
  submitting: false,
  cancellingIds: [],
  deletingIds: [],

  loadHistory: async () => {
    set({ historyStatus: get().order.length ? 'ready' : 'loading', historyError: null })
    try {
      const items = await listJobs({ limit: HISTORY_LIMIT })
      // Read the store only after the response: WebSocket updates may have landed while the list was in flight.
      const previous = get().jobs
      const jobs: Record<string, Job> = {}
      for (const job of items) {
        const known = previous[job.id]
        if (known && isStale(known, job)) {
          // The list snapshot is older than what we already have (e.g. a newer `job.updated`); keep ours.
          jobs[job.id] = known
          continue
        }
        notifyTransition(known, job)
        jobs[job.id] = job
      }
      // Keep the active/viewed jobs even if they fell out of the history window (verified below).
      const kept: string[] = []
      for (const id of [get().activeJobId, get().viewedJobId]) {
        if (id && !jobs[id] && previous[id]) {
          jobs[id] = previous[id]
          kept.push(id)
        }
      }
      let { activeJobId, viewedJobId } = get()
      if (!activeJobId) activeJobId = items.find((job) => !isTerminal(jobs[job.id] ?? job))?.id ?? null
      // The tracked job finished while live updates were down: show its result, as `upsertJob` would have.
      const activeBefore = activeJobId ? previous[activeJobId] : undefined
      if (activeJobId && activeBefore && !isTerminal(activeBefore) && jobs[activeJobId]?.status === 'completed') {
        viewedJobId = activeJobId
        persistViewed(activeJobId)
      }
      if (!viewedJobId) {
        const saved = readString(STORAGE_KEYS.viewedJobId)
        if (saved && jobs[saved]?.status === 'completed') viewedJobId = saved
      }
      if (viewedJobId && !jobs[viewedJobId]) viewedJobId = null
      set({
        jobs,
        // Include kept jobs (e.g. one just submitted while this list was in flight) so they stay in the history.
        order: sortByCreated(jobs, [...items.map((job) => job.id), ...kept]),
        historyStatus: 'ready',
        activeJobId,
        viewedJobId,
      })
      // Jobs outside the listed window may have been deleted server-side; refreshJob drops them on 404.
      kept.forEach((id) => void get().refreshJob(id))
      return true
    } catch (error) {
      const apiError = await toApiError(error)
      set({ historyStatus: get().order.length ? 'ready' : 'error', historyError: apiError })
      return false
    }
  },

  refreshJob: async (jobId) => {
    try {
      get().upsertJob(await getJob(jobId))
      return true
    } catch (error) {
      const apiError = await toApiError(error)
      if (apiError.status === 404) {
        set((state) => {
          const jobs = { ...state.jobs }
          delete jobs[jobId]
          return {
            jobs,
            order: state.order.filter((id) => id !== jobId),
            activeJobId: state.activeJobId === jobId ? null : state.activeJobId,
            viewedJobId: state.viewedJobId === jobId ? null : state.viewedJobId,
          }
        })
        return true
      }
      return false
    }
  },

  upsertJob: (job) => {
    const previous = get().jobs[job.id]
    if (previous && isStale(previous, job)) return
    notifyTransition(previous, job)
    set((state) => {
      const jobs = { ...state.jobs, [job.id]: job }
      const order = previous ? state.order : sortByCreated(jobs, [job.id, ...state.order])
      let { activeJobId, viewedJobId } = state
      const activeJob = activeJobId ? jobs[activeJobId] : undefined
      // Track jobs started elsewhere (another tab) when nothing else is running here.
      if (!isTerminal(job) && (!activeJob || isTerminal(activeJob))) activeJobId = job.id
      if (job.status === 'completed' && activeJobId === job.id && previous && !isTerminal(previous)) {
        viewedJobId = job.id
        persistViewed(job.id)
      }
      const cancellingIds = isTerminal(job) ? state.cancellingIds.filter((id) => id !== job.id) : state.cancellingIds
      return { jobs, order, activeJobId, viewedJobId, cancellingIds }
    })
  },

  submit: async () => {
    const references = useReferenceStore.getState().set
    if (!references || references.images.length === 0) {
      toast.warning('Add a reference image', 'Upload at least one image before generating.')
      return false
    }
    const system = useSystemStore.getState().system
    const { form } = useSettingsStore.getState()
    const referenceMode = system?.defaults.reference_mode ?? 'primary_only'
    set({ submitting: true })
    try {
      const job = await submitRequest({
        reference_set_id: references.id,
        primary_image_id: references.primary_image_id,
        settings: toRequestSettings(form, referenceMode),
      })
      set({ activeJobId: job.id })
      get().upsertJob(job)
      return true
    } catch (error) {
      const apiError = await toApiError(error)
      toast.error('Could not start generation', describeSubmitError(apiError))
      return false
    } finally {
      set({ submitting: false })
    }
  },

  retry: async (job) => {
    set({ submitting: true })
    const settings = { ...job.settings }
    try {
      let created: Job
      try {
        created = await submitRequest({
          reference_set_id: job.reference_set_id,
          primary_image_id: job.primary_image_id,
          settings,
        })
      } catch (error) {
        const apiError = await toApiError(error)
        const current = useReferenceStore.getState().set
        // The original reference set is gone: retry with the current set instead.
        if (apiError.status !== 404 || !current || current.images.length === 0) throw apiError
        created = await submitRequest({
          reference_set_id: current.id,
          primary_image_id: current.primary_image_id,
          settings,
        })
        toast.info('Retrying with current references', 'The original reference set no longer exists.')
      }
      set({ activeJobId: created.id })
      get().upsertJob(created)
      return true
    } catch (error) {
      const apiError = await toApiError(error)
      toast.error('Retry failed', describeSubmitError(apiError))
      return false
    } finally {
      set({ submitting: false })
    }
  },

  cancel: async (jobId) => {
    if (get().cancellingIds.includes(jobId)) return
    set((state) => ({ cancellingIds: [...state.cancellingIds, jobId] }))
    try {
      get().upsertJob(await cancelJob(jobId))
    } catch (error) {
      const apiError = await toApiError(error)
      set((state) => ({ cancellingIds: state.cancellingIds.filter((id) => id !== jobId) }))
      if (apiError.status === 409) await get().refreshJob(jobId)
      else toast.error('Could not cancel', apiError.message)
    }
  },

  remove: async (jobId) => {
    set((state) => ({ deletingIds: [...state.deletingIds, jobId] }))
    try {
      await deleteJob(jobId)
      set((state) => {
        const jobs = { ...state.jobs }
        delete jobs[jobId]
        const viewedJobId = state.viewedJobId === jobId ? null : state.viewedJobId
        if (viewedJobId !== state.viewedJobId) persistViewed(null)
        return {
          jobs,
          order: state.order.filter((id) => id !== jobId),
          activeJobId: state.activeJobId === jobId ? null : state.activeJobId,
          viewedJobId,
        }
      })
    } catch (error) {
      const apiError = await toApiError(error)
      toast.error(
        'Could not delete job',
        apiError.status === 409 ? 'Cancel the job before deleting it.' : apiError.message,
      )
    } finally {
      set((state) => ({ deletingIds: state.deletingIds.filter((id) => id !== jobId) }))
    }
  },

  view: (jobId) => {
    persistViewed(jobId)
    set({ viewedJobId: jobId })
  },
}))

export const selectActiveJob = (state: JobState): Job | null =>
  state.activeJobId ? (state.jobs[state.activeJobId] ?? null) : null

export const selectViewedJob = (state: JobState): Job | null =>
  state.viewedJobId ? (state.jobs[state.viewedJobId] ?? null) : null
