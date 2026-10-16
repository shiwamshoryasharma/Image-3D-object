import type { Job, JobStage } from '../api/types'

export interface StageInfo {
  stage: JobStage
  index: number
  label: string
}

/** Pipeline stages shown in the progress list (contract §7.4 stage table, indices 1..8). */
export const PIPELINE_STAGES: readonly StageInfo[] = [
  { stage: 'preprocessing', index: 1, label: 'Preparing image' },
  { stage: 'conditioning', index: 2, label: 'Encoding image' },
  { stage: 'sparse_structure', index: 3, label: 'Building structure' },
  { stage: 'shape', index: 4, label: 'Generating geometry' },
  { stage: 'texture', index: 5, label: 'Generating texture' },
  { stage: 'decoding', index: 6, label: 'Decoding mesh' },
  { stage: 'exporting', index: 7, label: 'Exporting GLB' },
  { stage: 'finalizing', index: 8, label: 'Finalizing' },
]

export type StageState = 'done' | 'active' | 'pending' | 'failed' | 'skipped'

/** Visual state of a pipeline stage for a given job. */
export function stageState(job: Job, info: StageInfo): StageState {
  const current = job.progress.stage_index
  if (job.status === 'completed') return 'done'
  if (info.index < current) return 'done'
  if (info.index === current) {
    if (job.status === 'failed') return 'failed'
    if (job.status === 'cancelled') return 'skipped'
    return job.status === 'running' ? 'active' : 'pending'
  }
  return job.status === 'failed' || job.status === 'cancelled' ? 'skipped' : 'pending'
}

export const STATUS_LABEL: Record<Job['status'], string> = {
  queued: 'Queued',
  running: 'Running',
  completed: 'Completed',
  failed: 'Failed',
  cancelled: 'Cancelled',
}
