import { ChartColumn, Download, SlidersHorizontal, Undo2 } from 'lucide-react'
import { useState } from 'react'

import { downloadJobGlb } from '../../api/jobs'
import { useViewedTuning, type CompletedJob, type TunedRun } from '../../hooks/useViewedTuning'
import { saveBlob } from '../../lib/download'
import { formatBytes, formatDimensions, formatDuration, formatInteger } from '../../lib/format'
import { selectViewedJob, useJobStore } from '../../store/jobStore'
import { useMeshTunerStore } from '../../store/meshTunerStore'
import { formFromJobSettings, useSettingsStore } from '../../store/settingsStore'
import { useSystemStore } from '../../store/systemStore'
import { toast } from '../../store/toastStore'
import { Button } from '../common/Button'
import { EmptyState } from '../common/EmptyState'
import { Panel } from '../common/Panel'
import { ProgressBar } from '../common/ProgressBar'
import { TunedDownloadButton } from '../meshTuner/TunedDownloadButton'
import styles from './ModelStatsCard.module.css'

function DownloadButton({ job }: { job: CompletedJob }) {
  const [progress, setProgress] = useState<number | null | undefined>(undefined)
  const downloading = progress !== undefined

  // Promise chaining (no try/finally) keeps this component compatible with the React Compiler.
  const download = () => {
    setProgress(null)
    return downloadJobGlb(job, { onProgress: setProgress })
      .then(({ blob, filename }) => saveBlob(blob, filename))
      .catch((error: unknown) => {
        toast.error('Download failed', error instanceof Error ? error.message : 'Could not download the GLB.')
      })
      .then(() => setProgress(undefined))
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

/** Statistics of the Auto Tune result (shown while the viewer displays the tuned model). */
function TunedStats({ job, run }: { job: CompletedJob; run: TunedRun }) {
  const setVariant = useMeshTunerStore((s) => s.setVariant)
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
    <Panel
      id="model-stats"
      title="Model · Tuned"
      icon={<ChartColumn size={16} />}
      subtitle={`${job.id} · ${run.id}`}
      actions={
        <Button
          size="sm"
          variant="ghost"
          icon={<Undo2 size={14} />}
          title="Show the original model in the viewer"
          onClick={() => setVariant('original')}
        >
          Original
        </Button>
      }
    >
      <div className={styles.body}>
        <dl className={styles.grid}>
          {rows.map(([label, value]) => (
            <div key={label} className={styles.row}>
              <dt>{label}</dt>
              <dd>{value}</dd>
            </div>
          ))}
        </dl>
        <TunedDownloadButton run={run} />
      </div>
    </Panel>
  )
}

/** Mesh statistics + generation metadata of the job shown in the viewer, with GLB export. */
export function ModelStatsCard() {
  const job = useJobStore(selectViewedJob)
  const replaceForm = useSettingsStore((s) => s.replaceForm)
  const capabilities = useSystemStore((s) => s.system?.engine.capabilities ?? null)
  const { tunedRun, variant } = useViewedTuning()

  if (!job || job.status !== 'completed' || !job.result) {
    return (
      <Panel id="model-stats" title="Model" icon={<ChartColumn size={16} />}>
        <EmptyState
          compact
          icon={<ChartColumn size={18} />}
          title="No model selected"
          description="Statistics and the GLB download appear here for the model in the viewer."
        />
      </Panel>
    )
  }

  const completed = job as CompletedJob
  if (variant === 'tuned' && tunedRun) return <TunedStats job={completed} run={tunedRun} />
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
    <Panel
      id="model-stats"
      title="Model"
      icon={<ChartColumn size={16} />}
      subtitle={job.id}
      actions={
        <Button
          size="sm"
          variant="ghost"
          icon={<SlidersHorizontal size={14} />}
          title="Load this job's settings (including its seed) into the settings panel"
          onClick={() => {
            replaceForm(formFromJobSettings(completed.settings, completed.result.seed), capabilities)
            toast.info('Settings loaded', `Using the settings and seed ${completed.result.seed} from this job.`)
          }}
        >
          Reuse
        </Button>
      }
    >
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
    </Panel>
  )
}
