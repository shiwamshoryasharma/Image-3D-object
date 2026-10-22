import { ScanSearch, X } from 'lucide-react'

import { fileUrl } from '../../api/files'
import { useFeatures } from '../../hooks/useFeatures'
import { previewSegmentation } from '../../actions/parts'
import { errorText } from '../../lib/errorText.ts'
import { effectiveSource } from '../../lib/generatePlan.ts'
import { currentStep, runPercent, runStepViews } from '../../lib/runSteps.ts'
import { useInputStore } from '../../store/inputStore'
import { isTerminal } from '../../store/jobStore'
import { useReferenceStore } from '../../store/referenceStore'
import { selectPreviewSegmentation, useRunStore } from '../../store/runStore'
import { Button } from '../common/Button'
import { ProgressBar } from '../common/ProgressBar'
import styles from './Input.module.css'

export interface SegmentationPreviewProps {
  active?: boolean
  headingLevel?: 2 | 3
  compact?: boolean
}

/**
 * Optional segmentation preview of the front image (UI_DESIGN §5.5). Preview only: its `sg_` id is never sent with
 * a generation. Rendered when segmentation is available, the effective source is image and a primary image exists.
 */
export function SegmentationPreview(_props: SegmentationPreviewProps) {
  const features = useFeatures()
  const source = useInputStore((s) => s.source)
  const hasPrimary = useReferenceStore((s) => !!s.set && s.set.images.some((image) => image.id === s.set?.primary_image_id))
  const run = useRunStore(selectPreviewSegmentation)
  const submitting = useRunStore((s) => s.submitting)
  const cancelling = useRunStore((s) => (run ? s.cancellingIds.includes(run.id) : false))
  const cancel = useRunStore((s) => s.cancel)

  if (!features.segmentation.available || effectiveSource(source, features) !== 'image' || !hasPrimary) return null

  const busy = !!run && !isTerminal(run)
  const step = run ? currentStep(runStepViews(run.steps, run.warnings)) : null
  const labelOf = (category: string) => features.taxonomy.classes.find((c) => c.id === category)?.label ?? category
  const result = run?.status === 'completed' ? run.result : null

  return (
    <div className={styles.stack}>
      <div className={styles.actions}>
        <Button
          size="sm"
          variant="secondary"
          icon={<ScanSearch size={14} />}
          loading={submitting && !busy}
          disabled={busy}
          onClick={() => void previewSegmentation()}
        >
          Preview parts on this image
        </Button>
        {busy && run && (
          <Button size="sm" variant="ghost" icon={<X size={14} />} loading={cancelling} onClick={() => void cancel('segmentation', run.id)}>
            Cancel preview
          </Button>
        )}
      </div>
      <p className={styles.note}>Preview only: the generation runs its own segmentation.</p>

      {busy && run && (
        <div className={styles.stack} aria-busy="true">
          <ProgressBar value={runPercent(run.progress)} size="sm" label="Segmentation preview progress" />
          <p className={styles.meta}>{step?.message ?? step?.label ?? 'Queued'}</p>
        </div>
      )}

      {run?.status === 'failed' && run.error && (
        <p className={styles.error} role="alert">
          {errorText(run.error, { subject: 'segmentation' })}
        </p>
      )}

      {result && (
        <div className={styles.stack}>
          <img className={styles.overlay} src={fileUrl(result.overlay_url)} alt="Segmentation overlay of the front image" />
          {result.labels.length > 0 ? (
            <ul className={styles.labels} aria-label="Found labels">
              {result.labels.map((label) => (
                <li key={label.value} className={styles.labelRow}>
                  <span className={styles.labelName}>
                    {labelOf(label.category)}
                    {label.side === 'left' ? ' (L)' : label.side === 'right' ? ' (R)' : ''}
                  </span>
                  <span>{Math.round(label.confidence * 100)} %</span>
                  <span>{label.area_px.toLocaleString()} px</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className={styles.note}>No labels found.</p>
          )}
          {result.expected_terms_missing.length > 0 && (
            <p className={styles.warningNote}>Not found: {result.expected_terms_missing.join(', ')}</p>
          )}
        </div>
      )}
    </div>
  )
}
