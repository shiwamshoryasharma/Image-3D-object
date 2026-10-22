import { Check, ExternalLink, ImagePlus, RefreshCw, Sparkles, X } from 'lucide-react'

import type { ImageCandidateOut } from '../../api/partAwareTypes.ts'
import { fileUrl } from '../../api/files'
import { adoptCandidateAndGenerate, adoptCandidateAsReference } from '../../actions/generation'
import { useFeatures } from '../../hooks/useFeatures'
import { errorText } from '../../lib/errorText.ts'
import { READY_FOR_3D_TEXT, warningText } from '../../lib/featureMeta.ts'
import { candidateCount } from '../../lib/generatePlan.ts'
import { currentStep, runPercent, runStepViews } from '../../lib/runSteps.ts'
import { buildGeneration } from '../../lib/workflowRequest.ts'
import { useInputStore } from '../../store/inputStore'
import { isTerminal } from '../../store/jobStore'
import { selectReviewGeneration, useRunStore } from '../../store/runStore'
import { Button } from '../common/Button'
import { ProgressBar } from '../common/ProgressBar'
import { Tag } from '../common/Tag'
import styles from './Input.module.css'

export interface PromptReviewContentProps {
  active?: boolean
  headingLevel?: 2 | 3
  compact?: boolean
}

function CandidateCard({ generationId, candidate, busy }: { generationId: string; candidate: ImageCandidateOut; busy: boolean }) {
  const used = candidate.used_as.length > 0
  return (
    <li className={styles.candidate}>
      <img
        className={styles.thumb}
        src={fileUrl(candidate.thumbnail_url)}
        alt={`Generated image ${candidate.index + 1}`}
        loading="lazy"
      />
      <p className={styles.meta}>
        {candidate.width} × {candidate.height} · seed {candidate.seed}
      </p>
      <div className={styles.chips}>
        {candidate.readiness.ok && candidate.readiness.issues.length === 0 ? (
          <Tag tone="success" icon={<Check size={11} aria-hidden />}>
            {READY_FOR_3D_TEXT}
          </Tag>
        ) : (
          candidate.readiness.issues.map((code) => (
            <Tag key={code} tone="warning">
              {warningText(code)}
            </Tag>
          ))
        )}
        {used && <Tag tone="neutral">Already used</Tag>}
      </div>
      <div className={styles.actions}>
        <Button
          size="sm"
          variant="primary"
          icon={<Sparkles size={14} />}
          disabled={busy}
          onClick={() => void adoptCandidateAndGenerate(generationId, candidate.index)}
        >
          Use &amp; generate 3D
        </Button>
        <Button
          size="sm"
          variant="secondary"
          icon={<ImagePlus size={14} />}
          disabled={busy}
          onClick={() => void adoptCandidateAsReference(generationId, candidate.index)}
        >
          Use as reference
        </Button>
        <a
          className={styles.meta}
          href={fileUrl(candidate.url)}
          target="_blank"
          rel="noopener noreferrer"
          aria-label={`Open image ${candidate.index + 1} full size (new tab)`}
        >
          <ExternalLink size={12} aria-hidden /> Open full size
        </a>
      </div>
    </li>
  )
}

/**
 * Review-first candidates (UI_DESIGN §5.3): live steps while the `ig_` run works, then one card per candidate with
 * its readiness chips and the "Use & generate 3D" / "Use as reference" / "Open full size" actions.
 */
export function PromptReviewContent(_props: PromptReviewContentProps) {
  const run = useRunStore(selectReviewGeneration)
  const submitting = useRunStore((s) => s.submitting)
  const cancelling = useRunStore((s) => (run ? s.cancellingIds.includes(run.id) : false))
  const cancel = useRunStore((s) => s.cancel)
  const startImageGeneration = useRunStore((s) => s.startImageGeneration)
  const form = useInputStore((s) => s.prompt)
  const features = useFeatures()

  if (!run) {
    return <p className={styles.note}>Generated images appear here for review before the 3D model is built.</p>
  }

  const live = !isTerminal(run)
  const steps = runStepViews(run.steps, run.warnings)
  const step = currentStep(steps)
  const candidates = run.result?.candidates ?? []
  const canAgain = !live && form.prompt.trim().length > 0 && features.prompt.available

  const again = () =>
    void startImageGeneration(buildGeneration(form, candidateCount(form, features), { features }))

  return (
    <div className={styles.root} aria-busy={live || undefined}>
      {live && (
        <div className={styles.stack}>
          <div className={styles.reviewHeader}>
            <span className={styles.reviewTitle}>{step?.label ?? 'Queued'}</span>
            <Button size="sm" variant="ghost" icon={<X size={14} />} loading={cancelling} onClick={() => void cancel('image_generation', run.id)}>
              Cancel
            </Button>
          </div>
          <ProgressBar value={runPercent(run.progress)} size="sm" label="Image generation progress" />
          {(step?.message ?? run.progress.message) && <p className={styles.meta}>{step?.message ?? run.progress.message}</p>}
        </div>
      )}

      {run.status === 'failed' && run.error && (
        <p className={styles.error} role="alert">
          {errorText(run.error, { subject: 'image_generation' })}
        </p>
      )}
      {run.status === 'cancelled' && <p className={styles.note}>Image generation cancelled.</p>}

      {candidates.length > 0 && (
        <ul className={styles.candidates} aria-label="Generated images">
          {candidates.map((candidate) => (
            <CandidateCard key={candidate.index} generationId={run.id} candidate={candidate} busy={submitting} />
          ))}
        </ul>
      )}

      {!live && (
        <div className={styles.actions}>
          <Button size="sm" variant="ghost" icon={<RefreshCw size={14} />} disabled={!canAgain} loading={submitting} onClick={again}>
            Generate again
          </Button>
        </div>
      )}
    </div>
  )
}
