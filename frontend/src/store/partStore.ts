/**
 * Part analyses per job, normalised part sets and the viewer's part view state (UI_DESIGN §6.1; not persisted).
 * Updates arrive from `part_analysis.updated` (WS) and REST (sync, fallback polling). Every upsert runs the shared
 * stale guard (`isStaleRun`); a terminal `completed` analysis loads its set (manifest + `normalizePartResult`).
 * Store edges (§1.4): features, announcer, toast.
 */
import { create } from 'zustand'

import { toApiError } from '../api/client'
import {
  cancelPartAnalysis,
  createPartAnalysis,
  deletePartAnalysis,
  getPartAnalysis,
  getPartManifest,
  listPartAnalyses,
} from '../api/partAnalyses'
import type { PartAnalysisOptions, PartAnalysisOut } from '../api/partAwareTypes.ts'
import { describeError, errorText } from '../lib/errorText.ts'
import type { NormalizeError, PartSetView } from '../lib/partsModel.ts'
import { isStaleRun, isTerminalRunStatus } from '../lib/runSteps.ts'
import { announce } from './announcerStore'
import { useFeaturesStore } from './featuresStore'
import { toast } from './toastStore'

export interface PartJobEntry {
  status: 'idle' | 'loading' | 'ready' | 'none' | 'error'
  /** Newest first. */
  analyses: PartAnalysisOut[]
  latestCompletedId: string | null
  error: string | null
}

export interface PartSetEntry {
  status: 'loading' | 'ready' | 'error'
  set: PartSetView | null
  error: string | null
  /** The set loaded without its manifest details: "Some details could not be read (<error>)." */
  note: string | null
}

/** Assignable to `lib/partsModel.PartSelection` (hidden, isolated, selected, hovered). */
export interface PartViewState {
  jobId: string | null
  show: boolean
  colorize: boolean
  /** 0..1 */
  explode: number
  hidden: string[]
  isolated: string | null
  selected: string | null
  hovered: string | null
}

interface PartState {
  byJob: Record<string, PartJobEntry>
  sets: Record<string, PartSetEntry>
  view: PartViewState
  requestingJobIds: string[]

  /** `GET /jobs/{id}/part-analyses`; then the latest completed set. Skipped when already known unless `force`. */
  loadForJob: (jobId: string, force?: boolean) => Promise<void>
  /** Fallback polling of non-terminal analyses. */
  refreshActive: () => Promise<void>
  /** WS + REST; stale guard; terminal `completed` → `loadSet`. */
  upsertAnalysis: (run: PartAnalysisOut) => void
  /** Manifest + `normalizePartResult` (retried without the manifest when only its details are unreadable). */
  loadSet: (analysisId: string) => Promise<void>
  /** `POST /jobs/{id}/part-analyses`. */
  requestAnalysis: (jobId: string, options: PartAnalysisOptions) => Promise<boolean>
  cancelAnalysis: (id: string) => Promise<void>
  deleteAnalysis: (id: string) => Promise<void>
  setShow: (show: boolean) => void
  setColorize: (colorize: boolean) => void
  setExplode: (explode: number) => void
  toggleHidden: (id: string) => void
  setHidden: (ids: string[]) => void
  isolate: (id: string | null) => void
  select: (id: string | null) => void
  hover: (id: string | null) => void
  /** A different job is viewed: clear hidden/isolated/selected/hovered/explode (colorize is kept). */
  resetView: (jobId: string | null) => void
}

const RETRY_WITHOUT_MANIFEST: ReadonlySet<NormalizeError> = new Set<NormalizeError>([
  'unsupported_manifest',
  'invalid_part_id',
  'duplicate_part_id',
])

const NORMALIZE_ERROR_TEXT: Readonly<Record<NormalizeError, string>> = Object.freeze({
  unsupported_manifest: 'unsupported manifest version',
  duplicate_part_id: 'duplicate part ids',
  invalid_part_id: 'invalid part ids',
  malformed: 'malformed result',
})

const EMPTY_VIEW: Readonly<Omit<PartViewState, 'jobId' | 'colorize'>> = Object.freeze({
  show: false,
  explode: 0,
  hidden: [],
  isolated: null,
  selected: null,
  hovered: null,
})

const byNewest = (a: PartAnalysisOut, b: PartAnalysisOut): number => Date.parse(b.created_at) - Date.parse(a.created_at)

function entryWith(analyses: readonly PartAnalysisOut[]): PartJobEntry {
  const sorted = [...analyses].sort(byNewest)
  return {
    status: sorted.length ? 'ready' : 'none',
    analyses: sorted,
    latestCompletedId: sorted.find((run) => run.status === 'completed' && run.result !== null)?.id ?? null,
    error: null,
  }
}

function findRun(byJob: Record<string, PartJobEntry>, id: string): PartAnalysisOut | null {
  for (const entry of Object.values(byJob)) {
    const run = entry.analyses.find((candidate) => candidate.id === id)
    if (run) return run
  }
  return null
}

const partError = (error: Parameters<typeof errorText>[0]): string => errorText(error, { subject: 'part_analysis' })

/** Job ids with a list request in flight (module scope: not render state). */
const loadingJobs = new Set<string>()

export const usePartStore = create<PartState>()((set, get) => ({
  byJob: {},
  sets: {},
  view: { jobId: null, colorize: false, ...EMPTY_VIEW },
  requestingJobIds: [],

  loadForJob: async (jobId, force = false) => {
    const current = get().byJob[jobId]
    if (loadingJobs.has(jobId)) return
    if (!force && current && current.status !== 'idle' && current.status !== 'error') return
    loadingJobs.add(jobId)
    if (!current) set((state) => ({ byJob: { ...state.byJob, [jobId]: { ...entryWith([]), status: 'loading' } } }))
    try {
      const analyses = await listPartAnalyses(jobId)
      // Keep newer WS records for runs we already know (stale guard per run).
      const known = get().byJob[jobId]?.analyses ?? []
      const merged = analyses.map((run) => {
        const previous = known.find((other) => other.id === run.id)
        return previous && isStaleRun(previous, run) ? previous : run
      })
      const entry = entryWith(merged)
      set((state) => ({ byJob: { ...state.byJob, [jobId]: entry } }))
      const latest = entry.latestCompletedId
      if (latest && get().sets[latest]?.status !== 'ready') await get().loadSet(latest)
    } catch (error) {
      const apiError = await toApiError(error)
      const previous = get().byJob[jobId]
      const entry: PartJobEntry =
        apiError.status === 404
          ? entryWith([])
          : {
              status: 'error',
              analyses: previous?.analyses ?? [],
              latestCompletedId: previous?.latestCompletedId ?? null,
              error: partError(apiError),
            }
      set((state) => ({ byJob: { ...state.byJob, [jobId]: entry } }))
    } finally {
      loadingJobs.delete(jobId)
    }
  },

  refreshActive: async () => {
    const active = Object.values(get().byJob).flatMap((entry) =>
      entry.analyses.filter((run) => !isTerminalRunStatus(run.status)),
    )
    await Promise.all(
      active.map(async (run) => {
        try {
          get().upsertAnalysis(await getPartAnalysis(run.id))
        } catch {
          // The next poll or WS event settles it.
        }
      }),
    )
  },

  upsertAnalysis: (run) => {
    const entry = get().byJob[run.job_id]
    const previous = entry?.analyses.find((other) => other.id === run.id)
    if (previous && isStaleRun(previous, run)) return
    const others = (entry?.analyses ?? []).filter((other) => other.id !== run.id)
    const next = entryWith([run, ...others])
    set((state) => ({ byJob: { ...state.byJob, [run.job_id]: next } }))

    const becameTerminal = isTerminalRunStatus(run.status) && (!previous || !isTerminalRunStatus(previous.status))
    if (!becameTerminal) return
    if (run.status === 'completed' && get().sets[run.id]?.status !== 'ready') void get().loadSet(run.id)
    // Only transitions seen live are announced; workflow-owned analyses are announced by the run store.
    if (run.workflow_id !== null || !previous) return
    if (run.status === 'completed') {
      toast.success('Parts ready', 'The part analysis finished.')
      announce('Parts ready')
    } else if (run.status === 'failed') {
      const text = run.error ? partError(run.error) : 'The part analysis failed.'
      toast.error('Part analysis failed', text)
      announce(`Part analysis failed. ${text}`, 'assertive')
    } else if (run.status === 'cancelled') {
      toast.info('Part analysis cancelled')
    }
  },

  loadSet: async (analysisId) => {
    if (get().sets[analysisId]?.status === 'loading') return
    set((state) => ({ sets: { ...state.sets, [analysisId]: { status: 'loading', set: null, error: null, note: null } } }))
    let run = findRun(get().byJob, analysisId)
    if (!run || !run.result) {
      try {
        run = await getPartAnalysis(analysisId)
      } catch (error) {
        const text = partError(await toApiError(error))
        set((state) => ({ sets: { ...state.sets, [analysisId]: { status: 'error', set: null, error: text, note: null } } }))
        return
      }
    }
    let manifest: unknown = null
    if (run.result) {
      try {
        manifest = await getPartManifest(run.result.manifest_url)
      } catch {
        manifest = null // a usable list without relations; the inspector shows "Details unavailable"
      }
    }
    // The normaliser loads on first use: most sessions never open a part set (keeps it out of the initial chunk).
    const { normalizePartResult } = await import('../lib/partsModel.ts')
    const features = useFeaturesStore.getState().snapshot
    let result = normalizePartResult(run, manifest, features)
    let note: string | null = null
    if (!result.ok && manifest !== null && RETRY_WITHOUT_MANIFEST.has(result.error)) {
      const firstError = result.error
      result = normalizePartResult(run, null, features)
      if (result.ok) note = `Some details could not be read (${NORMALIZE_ERROR_TEXT[firstError]}).`
    }
    const entry: PartSetEntry = result.ok
      ? { status: 'ready', set: result.set, error: null, note }
      : {
          status: 'error',
          set: null,
          error: `The part result could not be read (${NORMALIZE_ERROR_TEXT[result.error]}).`,
          note: null,
        }
    set((state) => ({ sets: { ...state.sets, [analysisId]: entry } }))
  },

  requestAnalysis: async (jobId, options) => {
    if (get().requestingJobIds.includes(jobId)) return false
    set((state) => ({ requestingJobIds: [...state.requestingJobIds, jobId] }))
    try {
      const run = await createPartAnalysis(jobId, options)
      get().upsertAnalysis(run)
      announce('Part analysis queued')
      return true
    } catch (error) {
      const report = describeError(await toApiError(error), { subject: 'part_analysis' })
      if (report.refreshFeatures) void useFeaturesStore.getState().refresh()
      if (!report.expected) toast.error('Could not analyse parts', report.text)
      return false
    } finally {
      set((state) => ({ requestingJobIds: state.requestingJobIds.filter((id) => id !== jobId) }))
    }
  },

  cancelAnalysis: async (id) => {
    try {
      get().upsertAnalysis(await cancelPartAnalysis(id))
    } catch (error) {
      toast.error('Could not cancel', partError(await toApiError(error)))
    }
  },

  deleteAnalysis: async (id) => {
    try {
      await deletePartAnalysis(id)
    } catch (error) {
      toast.error('Could not delete the analysis', partError(await toApiError(error)))
      return
    }
    set((state) => {
      const byJob = { ...state.byJob }
      for (const [jobId, entry] of Object.entries(byJob)) {
        if (entry.analyses.some((run) => run.id === id)) {
          byJob[jobId] = entryWith(entry.analyses.filter((run) => run.id !== id))
        }
      }
      const sets = { ...state.sets }
      delete sets[id]
      return { byJob, sets }
    })
  },

  setShow: (show) => set((state) => (state.view.show === show ? state : { view: { ...state.view, show } })),
  setColorize: (colorize) =>
    set((state) => (state.view.colorize === colorize ? state : { view: { ...state.view, colorize } })),
  setExplode: (value) => {
    const explode = Number.isFinite(value) ? Math.min(1, Math.max(0, value)) : 0
    set((state) => (state.view.explode === explode ? state : { view: { ...state.view, explode } }))
  },
  toggleHidden: (id) =>
    set((state) => {
      const hidden = state.view.hidden.includes(id)
        ? state.view.hidden.filter((other) => other !== id)
        : [...state.view.hidden, id]
      return { view: { ...state.view, hidden } }
    }),
  setHidden: (ids) => set((state) => ({ view: { ...state.view, hidden: [...new Set(ids)] } })),
  isolate: (isolated) =>
    set((state) => (state.view.isolated === isolated ? state : { view: { ...state.view, isolated } })),
  select: (selected) =>
    set((state) => (state.view.selected === selected ? state : { view: { ...state.view, selected } })),
  hover: (hovered) => set((state) => (state.view.hovered === hovered ? state : { view: { ...state.view, hovered } })),
  resetView: (jobId) =>
    set((state) =>
      state.view.jobId === jobId ? state : { view: { jobId, colorize: state.view.colorize, ...EMPTY_VIEW } },
    ),
}))

/** Selectors (return stored references; `null` when unknown). */
export const selectPartJob = (state: PartState, jobId: string | null): PartJobEntry | null =>
  jobId ? (state.byJob[jobId] ?? null) : null

export const selectActiveAnalysis = (state: PartState, jobId: string | null): PartAnalysisOut | null =>
  (jobId ? state.byJob[jobId]?.analyses.find((run) => !isTerminalRunStatus(run.status)) : undefined) ?? null

export const selectLatestSet = (state: PartState, jobId: string | null): PartSetEntry | null => {
  const latest = jobId ? state.byJob[jobId]?.latestCompletedId : null
  return latest ? (state.sets[latest] ?? null) : null
}
