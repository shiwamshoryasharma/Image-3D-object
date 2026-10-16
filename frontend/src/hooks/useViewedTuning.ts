import type { Job, JobResult, TuningResult, TuningRun } from '../api/types'
import { selectViewedJob, useJobStore } from '../store/jobStore'
import { selectLatestRun, selectTunedRun, useMeshTunerStore, type ModelVariant } from '../store/meshTunerStore'

export type CompletedJob = Job & { result: JobResult }
export type TunedRun = TuningRun & { result: TuningResult }

export interface ViewedTuning {
  /** The viewed job when it is completed (only completed models can be tuned or compared). */
  job: CompletedJob | null
  /** Newest run of that job (any status) — drives the progress checklist and report. */
  latestRun: TuningRun | null
  /** Newest completed run with a tuned model — what the viewer shows as "Tuned". */
  tunedRun: TunedRun | null
  /** Effective viewer variant ("tuned" only when a tuned model exists). */
  variant: ModelVariant
}

/** Tuning state of the model shown in the viewer. */
export function useViewedTuning(): ViewedTuning {
  const viewed = useJobStore(selectViewedJob)
  const job = viewed?.status === 'completed' && viewed.result ? (viewed as CompletedJob) : null
  const jobId = job?.id ?? null
  const latestRun = useMeshTunerStore((s) => selectLatestRun(s, jobId))
  const tuned = useMeshTunerStore((s) => selectTunedRun(s, jobId))
  const preferred = useMeshTunerStore((s) => s.variant)
  const tunedRun = tuned?.result ? (tuned as TunedRun) : null
  return { job, latestRun, tunedRun, variant: preferred === 'tuned' && tunedRun ? 'tuned' : 'original' }
}
