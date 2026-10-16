/**
 * Part data of the job in the viewer (UI_DESIGN §6.1, §6.3): the job's analyses, the latest completed set, the
 * running analysis and the viewer's part view state. Every field is a stored reference or a primitive.
 */
import type { PartAnalysisOut } from '../api/partAwareTypes.ts'
import type { PartSetView } from '../lib/partsModel.ts'
import { useJobStore } from '../store/jobStore'
import {
  selectActiveAnalysis,
  selectLatestSet,
  selectPartJob,
  usePartStore,
  type PartJobEntry,
  type PartSetEntry,
  type PartViewState,
} from '../store/partStore'

export interface PartsViewData {
  jobId: string | null
  job: PartJobEntry | null
  latest: PartSetEntry | null
  /** `latest.set` when ready. */
  set: PartSetView | null
  active: PartAnalysisOut | null
  view: PartViewState
  /** A `POST /jobs/{id}/part-analyses` for this job is in flight. */
  requesting: boolean
}

export function usePartsView(): PartsViewData {
  const jobId = useJobStore((s) => s.viewedJobId)
  const job = usePartStore((s) => selectPartJob(s, jobId))
  const latest = usePartStore((s) => selectLatestSet(s, jobId))
  const active = usePartStore((s) => selectActiveAnalysis(s, jobId))
  const view = usePartStore((s) => s.view)
  const requesting = usePartStore((s) => (jobId ? s.requestingJobIds.includes(jobId) : false))
  return { jobId, job, latest, set: latest?.status === 'ready' ? latest.set : null, active, view, requesting }
}
