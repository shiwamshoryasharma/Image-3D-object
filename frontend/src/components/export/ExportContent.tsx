import { Download } from 'lucide-react'
import { useId, useState, type ReactNode } from 'react'

import { withQuery } from '../../api/files'
import { downloadArtifact, type ArtifactRef } from '../../actions/downloads'
import { usePartsView } from '../../hooks/usePartsView'
import { formatBytes } from '../../lib/format'
import { selectViewedJob, useJobStore } from '../../store/jobStore'
import { Button } from '../common/Button'
import { ProgressBar } from '../common/ProgressBar'
import { partName } from '../parts/partText'
import styles from './ExportContent.module.css'

/** One download with its own progress: "Preparing…" until the first byte (on-demand files), then the percent. */
function DownloadRow({ label, hint, artifact, extra }: { label: string; hint?: string; artifact: ArtifactRef; extra?: ReactNode }) {
  const [progress, setProgress] = useState<number | null | undefined>(undefined)
  const busy = progress !== undefined

  const download = () => {
    setProgress(null)
    return downloadArtifact(artifact, setProgress).then(() => setProgress(undefined))
  }

  return (
    <li className={styles.row}>
      <span className={styles.label}>
        <span className={styles.name}>{label}</span>
        {(hint || extra) && (
          <span className={styles.hint}>
            {hint}
            {extra}
          </span>
        )}
      </span>
      <Button
        size="sm"
        variant="secondary"
        icon={<Download size={14} />}
        loading={busy}
        aria-label={`Download ${label}`}
        onClick={() => void download()}
      >
        {busy ? (progress === null ? 'Preparing…' : `${Math.round(progress)}%`) : 'Download'}
      </Button>
      {busy && (
        <span className={styles.progress}>
          <ProgressBar value={progress} size="xs" label={`${label} download progress`} />
        </span>
      )}
    </li>
  )
}

export interface ExportContentProps {
  active?: boolean
  headingLevel?: 2 | 3
  compact?: boolean
}

/**
 * Downloads of the viewed model and its latest part analysis (UI_DESIGN §6.7): every URL comes from the payload
 * (UI9); filenames come from the server (the fallbacks are only used without `Content-Disposition`).
 */
export function ExportContent({ headingLevel = 3 }: ExportContentProps) {
  const job = useJobStore(selectViewedJob)
  const { set } = usePartsView()
  const [includeBase, setIncludeBase] = useState(false)
  const headingId = useId()
  const Heading = headingLevel === 2 ? 'h2' : 'h3'

  if (!job || job.status !== 'completed' || !job.result) return null

  const base = `img3d_${job.id}`
  const physical = set ? set.parts.filter((part) => part.downloadUrl) : []
  const masks = set ? set.parts.filter((part) => part.maskUrl) : []

  return (
    <section className={styles.root} aria-labelledby={headingId}>
      <Heading id={headingId} className={styles.heading}>
        Downloads
      </Heading>
      <ul className={styles.rows}>
        <DownloadRow
          label="Original model (GLB)"
          hint={formatBytes(job.result.stats.file_size_bytes)}
          artifact={{ kind: 'job_glb', jobId: job.id }}
        />
        {set && (
          <>
            <DownloadRow
              label="Assembled parts model (GLB)"
              artifact={{ kind: 'parts_glb', url: set.urls.download, fallbackName: `${base}_parts_${set.analysisId}.glb`, sizeHint: null }}
            />
            <DownloadRow
              label="Everything (ZIP)"
              artifact={{
                kind: 'parts_zip',
                url: includeBase ? withQuery(set.urls.zip, { include_base: 'true' }) : set.urls.zip,
                fallbackName: `${base}_parts_${set.analysisId}.zip`,
                sizeHint: null,
              }}
              extra={
                <label className={styles.check}>
                  <input type="checkbox" checked={includeBase} onChange={(event) => setIncludeBase(event.target.checked)} />
                  Include base model
                </label>
              }
            />
            <DownloadRow
              label="Manifest (JSON)"
              artifact={{ kind: 'manifest', url: set.urls.manifest, fallbackName: 'manifest.json', sizeHint: null }}
            />
            {set.urls.overlay && (
              <DownloadRow
                label="Segmentation overlay"
                artifact={{ kind: 'overlay_png', url: set.urls.overlay, fallbackName: 'overlay.png', sizeHint: null }}
              />
            )}
          </>
        )}
      </ul>
      {physical.length > 0 && (
        <details className={styles.details}>
          <summary>Per-part GLBs ({physical.length})</summary>
          <ul className={styles.rows}>
            {physical.map((part) => (
              <DownloadRow
                key={part.id}
                label={partName(part)}
                artifact={{ kind: 'part_glb', url: part.downloadUrl ?? '', fallbackName: `${base}_${part.id}.glb`, sizeHint: null }}
              />
            ))}
          </ul>
        </details>
      )}
      {masks.length > 0 && (
        <details className={styles.details}>
          <summary>Masks ({masks.length})</summary>
          <ul className={styles.rows}>
            {masks.map((part) => (
              <DownloadRow
                key={part.id}
                label={`${partName(part)} mask`}
                artifact={{ kind: 'mask_png', url: part.maskUrl ?? '', fallbackName: `${part.id}_mask.png`, sizeHint: null }}
              />
            ))}
          </ul>
        </details>
      )}
    </section>
  )
}
