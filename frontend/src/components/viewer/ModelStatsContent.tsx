import { ChartColumn, Download } from 'lucide-react'
import { Suspense, useState } from 'react'

import { downloadArtifact } from '../../actions/downloads'
import { useTunerVisible } from '../../hooks/useTunerVisible'
import { useViewedTuning, type CompletedJob, type TunedRun } from '../../hooks/useViewedTuning'
import { formatBytes, formatDimensions, formatDuration, formatInteger } from '../../lib/format'
import { selectViewedJob, useJobStore } from '../../store/jobStore'
import { Button } from '../common/Button'
import { EmptyState } from '../common/EmptyState'
import { ProgressBar } from '../common/ProgressBar'
import { LazyTunedDownloadButton } from '../tunerLock/lazyTuner'
import styles from './ModelStatsCard.module.css'

function DownloadButton({ job }: { job: CompletedJob }) {
  const [progress, setProgress] = useState<number | null | undefined>(undefined)
  const downloading = progress !== undefined

  // Promise chaining (no try/finally) keeps this component compatible with the React Compiler. Errors toast
  // "Download failed" inside `downloadArtifact` (UI_DESIGN §6.7).
  const download = () => {
    setProgress(null)
    return downloadArtifact({ kind: 'job_glb', jobId: job.id }, setProgress).then(() => setProgress(undefined))
  }

  return (
    <div className={styles.download}>
      <Button
        variant="primary"
        fullWidth
        icon={<Download size={16} />}
        loading={downloading}
        onClick={() => void download()}
      >
        {downloading ? `Downloading${progress !== null && progress !== undefined ? ` ${Math.round(progress)}%` : '…'}` : 'Download GLB'}
        <span className={styles.size}>{formatBytes(job.result.stats.file_size_bytes)}</span>
      </Button>
      {downloading && <ProgressBar value={progress ?? null} size="xs" label="Download progress" />}
    </div>
  )
}

function metricAfter(run: TunedRun, keys: readonly string[]): number | null {
  for (const key of keys) {
    const value = run.metrics.find((metric) => metric.key === key)?.after
    if (value !== null && value !== undefined) return value
  }
  return null
}

/** Statistics of the Auto Tune result (shown while the viewer displays the tuned model; Default only, §3.9). */
function TunedStatsBody({ run }: { run: TunedRun }) {
  const stats = run.result.stats
  const duration =
    run.started_at && run.finished_at ? (Date.parse(run.finished_at) - Date.parse(run.started_at)) / 1000 : null
  const validation = run.validation
  const rows: Array<[string, string]> = [
    ['Vertices', formatInteger(stats?.vertices ?? metricAfter(run, ['vertices']))],
    ['Triangles', formatInteger(stats?.faces ?? metricAfter(run, ['triangles', 'faces']))],
    ['Materials', formatInteger(stats?.materials ?? metricAfter(run, ['materials']))],
    ['Textures', formatInteger(stats?.textures ?? metricAfter(run, ['textures']))],
    ['Texture size', stats?.texture_size ? `${stats.texture_size}×${stats.texture_size}` : '—'],
    ['Dimensions', formatDimensions(stats?.dimensions)],
    ['File size', formatBytes(stats?.file_size_bytes)],
    ['Tuning time', formatDuration(duration)],
    ['Preset', run.options.preset.charAt(0).toUpperCase() + run.options.preset.slice(1)],
    ['Blender', run.blender?.version ?? '—'],
    ['Validation', validation ? (validation.passed ? 'Passed' : 'Needs review') : '—'],
    ['Warnings', String(run.warnings.length)],
  ]

  return (
    <div className={styles.body}>
      <dl className={styles.grid}>
        {rows.map(([label, value]) => (
          <div key={label} className={styles.row}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {/* A GET download of an existing tuned result: viewing past results is working functionality (§3.9). */}
      <Suspense fallback={null}>
        <LazyTunedDownloadButton run={run} />
      </Suspense>
    </div>
  )
}

export interface ModelStatsContentProps {
  active?: boolean
  headingLevel?: 2 | 3
  compact?: boolean
}

/**
 * Mesh statistics + generation metadata of the job in the viewer, with GLB export (today's `ModelStatsCard` body).
 * The tuned statistics render only where `useTunerVisible()` (the Default shell, §3.9) and the viewer shows the
 * tuned variant; elsewhere the original model's statistics are shown.
 */
export function ModelStatsContent(_props: ModelStatsContentProps) {
  const job = useJobStore(selectViewedJob)
  const { tunedRun, variant } = useViewedTuning()
  const tunerVisible = useTunerVisible()

  if (!job || job.status !== 'completed' || !job.result) {
    return (
      <EmptyState
        compact
        icon={<ChartColumn size={18} />}
        title="No model selected"
        description="Statistics and the GLB download appear here for the model in the viewer."
      />
    )
  }

  const completed = job as CompletedJob
  if (tunerVisible && variant === 'tuned' && tunedRun) return <TunedStatsBody run={tunedRun} />
  const { stats, timings } = completed.result
  const resolutionText =
    completed.result.effective_resolution !== completed.result.resolution
      ? `${completed.result.resolution} (effective ${completed.result.effective_resolution})`
      : String(completed.result.resolution)

  const rows: Array<[string, string]> = [
    ['Vertices', formatInteger(stats.vertices)],
    ['Triangles', formatInteger(stats.faces)],
    ['Materials', formatInteger(stats.materials)],
    ['Textures', formatInteger(stats.textures)],
    ['Texture size', stats.texture_size ? `${stats.texture_size}×${stats.texture_size}` : '—'],
    ['Dimensions', formatDimensions(stats.dimensions)],
    ['File size', formatBytes(stats.file_size_bytes)],
    ['Generation time', formatDuration(timings.total ?? null)],
    ['Seed', String(completed.result.seed)],
    ['Resolution', resolutionText],
    ['Polygon target', formatInteger(completed.result.polygon_target)],
    ['UVs', stats.has_uv ? 'Yes' : 'No'],
  ]

  return (
    <div className={styles.body}>
      <dl className={styles.grid}>
        {rows.map(([label, value]) => (
          <div key={label} className={styles.row}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      <DownloadButton job={completed} />
    </div>
  )
}
