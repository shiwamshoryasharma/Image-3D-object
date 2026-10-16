/**
 * Auto Mesh Tuner: Blender runtime status (detection / selection / official portable download), tuning runs per
 * job, the persisted options form and the viewer's original/tuned toggle.
 * Updates arrive from the WebSocket (`blender.updated`, `tuning.updated`) and REST (source of truth on reconnect).
 */
import { create } from 'zustand'
import { createJSONStorage, persist } from 'zustand/middleware'

import { ApiError, toApiError } from '../api/client'
import {
  cancelBlenderDownload,
  detectBlender,
  getBlenderRuntime,
  resolveBlenderRelease,
  selectBlenderRuntime,
  startBlenderDownload,
} from '../api/blender'
import { cancelTuning, getTuning, listTunings, startTuning } from '../api/tunings'
import {
  TERMINAL_STATUSES,
  TUNING_PRESETS,
  type BlenderRelease,
  type BlenderRuntimeMode,
  type BlenderRuntimeStatus,
  type TuningOptionValues,
  type TuningPreset,
  type TuningRun,
} from '../api/types'
import {
  PRESET_DEFAULTS,
  isDownloadActive,
  sanitizeOverrides,
  toTuningRequest,
  type TuningOverrideKey,
  type TuningOverrides,
} from '../lib/meshTuner'
import { STORAGE_KEYS, readString, writeString } from '../lib/storage'
import { useJobStore } from './jobStore'
import { toast } from './toastStore'

type LoadStatus = 'idle' | 'loading' | 'ready' | 'error'

export type ModelVariant = 'original' | 'tuned'

/** Which control a runtime action error belongs to: mode switch, pinning an install, re-detect, download, Auto Tune. */
export type RuntimeActionSource = 'mode' | 'pin' | 'detect' | 'download' | 'tune'

export interface RuntimeActionError {
  source: RuntimeActionSource
  message: string
}

interface MeshTunerState {
  // Blender runtime ---------------------------------------------------------------------------
  runtime: BlenderRuntimeStatus | null
  runtimeStatus: LoadStatus
  runtimeError: ApiError | null
  /**
   * Last runtime action failure, shown inline next to the control that triggered it inside the setup dialog
   * (toasts render below the modal's top layer, so they are only used while the dialog is closed).
   */
  actionError: RuntimeActionError | null
  detecting: boolean
  selecting: boolean
  downloadStarting: boolean
  downloadCancelling: boolean
  /** Official release resolved on demand (also mirrored from `runtime.release`). */
  release: BlenderRelease | null
  releaseStatus: LoadStatus
  releaseError: string | null
  setupOpen: boolean
  /** The setup dialog already auto-opened in this session. */
  setupAutoOpened: boolean

  // Tuning runs ---------------------------------------------------------------------------------
  runs: Record<string, TuningRun>
  /** Newest run per job (any status). */
  latestRunByJob: Record<string, string>
  /** Newest completed run with a result per job (what the viewer shows as "Tuned"). */
  latestTunedByJob: Record<string, string>
  runsStatusByJob: Record<string, LoadStatus>
  startingJobIds: string[]
  cancellingRunIds: string[]

  // Form + viewer (form persisted) ----------------------------------------------------------
  preset: TuningPreset
  overrides: TuningOverrides
  advancedOpen: boolean
  variant: ModelVariant

  refreshRuntime: () => Promise<boolean>
  applyRuntime: (runtime: BlenderRuntimeStatus) => void
  detect: () => Promise<void>
  select: (mode: BlenderRuntimeMode, executable?: string | null) => Promise<boolean>
  resolveRelease: (force?: boolean) => Promise<void>
  startDownload: (version?: string | null) => Promise<boolean>
  cancelDownload: () => Promise<void>
  setSetupOpen: (open: boolean) => void

  upsertRun: (run: TuningRun) => void
  loadRunsForJob: (jobId: string, force?: boolean) => Promise<boolean>
  refreshRun: (tuningId: string) => Promise<boolean>
  startTuning: (jobId: string) => Promise<boolean>
  cancelTuning: (tuningId: string) => Promise<void>
  resync: () => Promise<void>

  setPreset: (preset: TuningPreset) => void
  setOption: <K extends TuningOverrideKey>(key: K, value: TuningOptionValues[K]) => void
  resetOverrides: () => void
  setAdvancedOpen: (open: boolean) => void
  setVariant: (variant: ModelVariant) => void
}

// ------------------------------------------------------------------------------------------------
// Helpers
// ------------------------------------------------------------------------------------------------

export const isRunTerminal = (run: Pick<TuningRun, 'status'>): boolean => TERMINAL_STATUSES.has(run.status)

const STATUS_RANK: Record<TuningRun['status'], number> = { queued: 0, running: 1, completed: 2, failed: 2, cancelled: 2 }

/** Older snapshot than the one held (slow REST reply after a newer WS frame): status never moves backwards. */
function isRunStale(previous: TuningRun, next: TuningRun): boolean {
  if (STATUS_RANK[next.status] < STATUS_RANK[previous.status]) return true
  if (isRunTerminal(previous)) return next.status !== previous.status
  if (next.status !== previous.status) return false
  return Date.parse(next.progress.updated_at) < Date.parse(previous.progress.updated_at)
}

/** Runtime snapshot older than ours: a download progress frame or scan that happened before the held one. */
function isRuntimeStale(previous: BlenderRuntimeStatus, next: BlenderRuntimeStatus): boolean {
  const a = previous.download
  const b = next.download
  if (a && b && a.started_at === b.started_at && Date.parse(b.updated_at) < Date.parse(a.updated_at)) return true
  if (previous.last_scan_at && next.last_scan_at && Date.parse(next.last_scan_at) < Date.parse(previous.last_scan_at)) {
    return true
  }
  return false
}

function notifyRuntimeTransition(previous: BlenderRuntimeStatus | null, next: BlenderRuntimeStatus) {
  const before = previous?.download
  const after = next.download
  if (!before || !after || !isDownloadActive(before) || isDownloadActive(after)) return
  const version = after.version ?? before.version ?? ''
  if (after.state === 'completed') {
    toast.success('Blender installed', `Portable Blender ${version} is installed${next.state === 'ready' ? ' and ready' : ''}.`)
  } else if (after.state === 'failed') {
    toast.error('Blender download failed', after.error ?? 'The download could not be completed.')
  } else if (after.state === 'cancelled') {
    toast.info('Blender download cancelled', 'The partial download was kept and can be resumed.')
  }
}

function notifyRunTransition(previous: TuningRun | undefined, next: TuningRun) {
  if (!previous || isRunTerminal(previous) || !isRunTerminal(next)) return
  if (next.status === 'completed') {
    const warnings = next.warnings.length
    toast.success(
      'Auto Tune finished',
      warnings > 0 ? `The tuned model is ready (${warnings} warning${warnings > 1 ? 's' : ''}).` : 'The tuned model is ready.',
    )
  } else if (next.status === 'failed') {
    toast.error('Auto Tune failed', next.error?.message ?? 'The mesh could not be tuned.')
  } else {
    toast.info('Auto Tune cancelled')
  }
}

function newest(runs: Record<string, TuningRun>, jobId: string, filter: (run: TuningRun) => boolean): string | null {
  let best: TuningRun | null = null
  for (const run of Object.values(runs)) {
    if (run.job_id !== jobId || !filter(run)) continue
    if (!best || Date.parse(run.created_at) > Date.parse(best.created_at)) best = run
  }
  return best?.id ?? null
}

const hasTunedResult = (run: TuningRun) => run.status === 'completed' && run.result !== null

/** Recompute both "latest" pointers of one job after its runs changed. */
function latestPointers(state: Pick<MeshTunerState, 'latestRunByJob' | 'latestTunedByJob'>, runs: Record<string, TuningRun>, jobId: string) {
  const latest = newest(runs, jobId, () => true)
  const tuned = newest(runs, jobId, hasTunedResult)
  const latestRunByJob = { ...state.latestRunByJob }
  const latestTunedByJob = { ...state.latestTunedByJob }
  if (latest) latestRunByJob[jobId] = latest
  else delete latestRunByJob[jobId]
  if (tuned) latestTunedByJob[jobId] = tuned
  else delete latestTunedByJob[jobId]
  return { latestRunByJob, latestTunedByJob }
}

function without<T>(list: T[], value: T): T[] {
  return list.includes(value) ? list.filter((item) => item !== value) : list
}

function describeRuntimeError(error: ApiError, fallback: string): string {
  if (error.isUnreachable) return 'The backend is unreachable.'
  return error.message || fallback
}

// ------------------------------------------------------------------------------------------------
// Store
// ------------------------------------------------------------------------------------------------

export const useMeshTunerStore = create<MeshTunerState>()(
  persist(
    (set, get) => {
      /** Record an action error for inline display; toast it only while the setup dialog is closed. */
      const reportActionError = (source: RuntimeActionSource, title: string, message: string) => {
        set({ actionError: { source, message } })
        if (!get().setupOpen) toast.error(title, message)
      }

      return {
        runtime: null,
        runtimeStatus: 'idle',
        runtimeError: null,
        actionError: null,
        detecting: false,
        selecting: false,
        downloadStarting: false,
        downloadCancelling: false,
        release: null,
        releaseStatus: 'idle',
        releaseError: null,
        setupOpen: false,
        setupAutoOpened: false,

        runs: {},
        latestRunByJob: {},
        latestTunedByJob: {},
        runsStatusByJob: {},
        startingJobIds: [],
        cancellingRunIds: [],

        preset: 'balanced',
        overrides: {},
        advancedOpen: false,
        variant: 'original',

        // --- Blender runtime ---------------------------------------------------------------------

        refreshRuntime: async () => {
          if (!get().runtime) set({ runtimeStatus: 'loading' })
          try {
            get().applyRuntime(await getBlenderRuntime())
            return true
          } catch (error) {
            const apiError = await toApiError(error)
            set({ runtimeStatus: get().runtime ? 'ready' : 'error', runtimeError: apiError })
            return false
          }
        },

        applyRuntime: (runtime) => {
          const previous = get().runtime
          if (previous && isRuntimeStale(previous, runtime)) return
          notifyRuntimeTransition(previous, runtime)
          const patch: Partial<MeshTunerState> = { runtime, runtimeStatus: 'ready', runtimeError: null }
          if (runtime.release) patch.release = runtime.release
          // Open the setup dialog once when a compatible Blender is missing, unless this exact prompt was dismissed.
          if (
            runtime.state === 'needs_download' &&
            runtime.message &&
            !get().setupAutoOpened &&
            readString(STORAGE_KEYS.blenderSetupDismissed) !== runtime.message
          ) {
            patch.setupOpen = true
            patch.setupAutoOpened = true
          }
          set(patch)
        },

        detect: async () => {
          if (get().detecting) return
          set({ detecting: true, actionError: null })
          try {
            get().applyRuntime(await detectBlender())
          } catch (error) {
            const apiError = await toApiError(error)
            reportActionError('detect', 'Blender detection failed', describeRuntimeError(apiError, 'Blender detection failed.'))
          } finally {
            set({ detecting: false })
          }
        },

        select: async (mode, executable) => {
          if (get().selecting) return false
          set({ selecting: true, actionError: null })
          try {
            get().applyRuntime(await selectBlenderRuntime(executable ? { mode, executable } : { mode }))
            return true
          } catch (error) {
            const apiError = await toApiError(error)
            reportActionError(
              executable ? 'pin' : 'mode',
              'Could not change the Blender runtime',
              describeRuntimeError(apiError, 'The Blender runtime could not be changed.'),
            )
            return false
          } finally {
            set({ selecting: false })
          }
        },

        resolveRelease: async (force = false) => {
          const { releaseStatus, release } = get()
          if (releaseStatus === 'loading' || (!force && release)) return
          set({ releaseStatus: 'loading', releaseError: null })
          try {
            set({ release: await resolveBlenderRelease(), releaseStatus: 'ready' })
          } catch (error) {
            const apiError = await toApiError(error)
            set({ releaseStatus: 'error', releaseError: describeRuntimeError(apiError, 'Could not resolve the official release.') })
          }
        },

        startDownload: async (version) => {
          if (get().downloadStarting) return false
          set({ downloadStarting: true, actionError: null })
          try {
            get().applyRuntime(await startBlenderDownload(version))
            return true
          } catch (error) {
            const apiError = await toApiError(error)
            const message =
              apiError.status === 409
                ? 'A Blender download is already in progress.'
                : apiError.status === 403
                  ? 'Blender downloads are disabled on this server.'
                  : describeRuntimeError(apiError, 'The download could not be started.')
            reportActionError('download', 'Could not start the Blender download', message)
            if (apiError.status === 409) void get().refreshRuntime()
            return false
          } finally {
            set({ downloadStarting: false })
          }
        },

        cancelDownload: async () => {
          if (get().downloadCancelling) return
          set({ downloadCancelling: true, actionError: null })
          try {
            get().applyRuntime(await cancelBlenderDownload())
          } catch (error) {
            const apiError = await toApiError(error)
            reportActionError('download', 'Could not cancel the download', describeRuntimeError(apiError, 'The download could not be cancelled.'))
          } finally {
            set({ downloadCancelling: false })
          }
        },

        setSetupOpen: (open) => {
          const runtime = get().runtime
          // Closing the prompt remembers this exact message so it does not auto-open again in this browser.
          if (!open && runtime?.state === 'needs_download' && runtime.message) {
            writeString(STORAGE_KEYS.blenderSetupDismissed, runtime.message)
          }
          set({ setupOpen: open, actionError: null })
        },

        // --- Tuning runs ---------------------------------------------------------------------------

        upsertRun: (run) => {
          const previous = get().runs[run.id]
          if (previous && isRunStale(previous, run)) return
          notifyRunTransition(previous, run)
          set((state) => {
            const runs = { ...state.runs, [run.id]: run }
            const pointers = latestPointers(state, runs, run.job_id)
            // A run that just finished for the model in the viewer: show the tuned result right away.
            const justTuned =
              run.status === 'completed' &&
              !!previous &&
              !isRunTerminal(previous) &&
              useJobStore.getState().viewedJobId === run.job_id
            return {
              runs,
              ...pointers,
              variant: justTuned ? 'tuned' : state.variant,
              cancellingRunIds: isRunTerminal(run) ? without(state.cancellingRunIds, run.id) : state.cancellingRunIds,
            }
          })
        },

        loadRunsForJob: async (jobId, force = false) => {
          const status = get().runsStatusByJob[jobId]
          if (status === 'loading' || (!force && status === 'ready')) return true
          set((state) => ({ runsStatusByJob: { ...state.runsStatusByJob, [jobId]: 'loading' } }))
          try {
            const items = await listTunings(jobId)
            items.forEach((run) => get().upsertRun(run))
            set((state) => ({ runsStatusByJob: { ...state.runsStatusByJob, [jobId]: 'ready' } }))
            return true
          } catch (error) {
            const apiError = await toApiError(error)
            // A deleted job simply has no runs.
            const next: LoadStatus = apiError.status === 404 ? 'ready' : 'error'
            set((state) => ({ runsStatusByJob: { ...state.runsStatusByJob, [jobId]: next } }))
            return false
          }
        },

        refreshRun: async (tuningId) => {
          try {
            get().upsertRun(await getTuning(tuningId))
            return true
          } catch (error) {
            const apiError = await toApiError(error)
            if (apiError.status !== 404) return false
            set((state) => {
              const removed = state.runs[tuningId]
              if (!removed) return {}
              const runs = { ...state.runs }
              delete runs[tuningId]
              return { runs, ...latestPointers(state, runs, removed.job_id), cancellingRunIds: without(state.cancellingRunIds, tuningId) }
            })
            return true
          }
        },

        startTuning: async (jobId) => {
          if (get().startingJobIds.includes(jobId)) return false
          set((state) => ({ startingJobIds: [...state.startingJobIds, jobId] }))
          try {
            const { preset, overrides } = get()
            get().upsertRun(await startTuning(jobId, toTuningRequest(preset, overrides)))
            return true
          } catch (error) {
            const apiError = await toApiError(error)
            if (apiError.code === 'blender_unavailable' || apiError.status === 503) {
              // The backend's message is the user prompt (e.g. "Blender 4.2.17 is installed but …").
              toast.warning('Blender is not ready', apiError.message)
              set({ setupOpen: true, actionError: { source: 'tune', message: apiError.message } })
              void get().refreshRuntime()
            } else if (apiError.status === 429) {
              toast.error('Auto Tune queue is full', 'Wait for the running tunings to finish.')
            } else if (apiError.status === 409) {
              toast.error('Cannot tune this model', apiError.message)
            } else {
              toast.error('Could not start Auto Tune', describeRuntimeError(apiError, 'Unexpected error.'))
            }
            return false
          } finally {
            set((state) => ({ startingJobIds: without(state.startingJobIds, jobId) }))
          }
        },

        cancelTuning: async (tuningId) => {
          if (get().cancellingRunIds.includes(tuningId)) return
          set((state) => ({ cancellingRunIds: [...state.cancellingRunIds, tuningId] }))
          try {
            get().upsertRun(await cancelTuning(tuningId))
          } catch (error) {
            const apiError = await toApiError(error)
            set((state) => ({ cancellingRunIds: without(state.cancellingRunIds, tuningId) }))
            if (apiError.status === 409) await get().refreshRun(tuningId)
            else toast.error('Could not cancel Auto Tune', apiError.message)
          }
        },

        resync: async () => {
          const viewedJobId = useJobStore.getState().viewedJobId
          const active = Object.values(get().runs).filter((run) => !isRunTerminal(run))
          await Promise.all([
            get().refreshRuntime(),
            viewedJobId ? get().loadRunsForJob(viewedJobId, true) : Promise.resolve(true),
            ...active.map((run) => get().refreshRun(run.id)),
          ])
        },

        // --- Form + viewer -------------------------------------------------------------------------

        setPreset: (preset) => set({ preset }),

        setOption: (key, value) =>
          set((state) => {
            const overrides = { ...state.overrides }
            // Back to the preset default → no longer an override.
            if (PRESET_DEFAULTS[state.preset][key] === value) delete overrides[key]
            else overrides[key] = value
            return { overrides }
          }),

        resetOverrides: () => set({ overrides: {} }),

        setAdvancedOpen: (advancedOpen) => set({ advancedOpen }),

        setVariant: (variant) => set({ variant }),
      }
    },
    {
      name: STORAGE_KEYS.meshTuner,
      version: 1,
      storage: createJSONStorage(() => localStorage),
      partialize: (state) => ({ preset: state.preset, overrides: state.overrides, advancedOpen: state.advancedOpen }),
      merge: (persisted, current) => {
        const saved = (persisted ?? {}) as Partial<Pick<MeshTunerState, 'preset' | 'overrides' | 'advancedOpen'>>
        const preset = TUNING_PRESETS.includes(saved.preset as TuningPreset) ? (saved.preset as TuningPreset) : current.preset
        return {
          ...current,
          preset,
          overrides: sanitizeOverrides(saved.overrides),
          advancedOpen: saved.advancedOpen === true,
        }
      },
    },
  ),
)

/** Newest run (any status) of a job. Returns a stored reference (safe as a zustand selector result). */
export function selectLatestRun(state: MeshTunerState, jobId: string | null): TuningRun | null {
  if (!jobId) return null
  const id = state.latestRunByJob[jobId]
  return id ? (state.runs[id] ?? null) : null
}

/** Newest completed run with a tuned model for a job. */
export function selectTunedRun(state: MeshTunerState, jobId: string | null): TuningRun | null {
  if (!jobId) return null
  const id = state.latestTunedByJob[jobId]
  return id ? (state.runs[id] ?? null) : null
}

/** Message of the current runtime action error when it belongs to `source` (a primitive, safe as a selector). */
export function selectActionError(state: MeshTunerState, source: RuntimeActionSource): string | null {
  return state.actionError?.source === source ? state.actionError.message : null
}
