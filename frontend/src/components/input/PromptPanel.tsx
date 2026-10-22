import { GalleryHorizontal, Sparkles } from 'lucide-react'

import { selectReviewGeneration, useRunStore } from '../../store/runStore'
import { Panel } from '../common/Panel'
import styles from './Input.module.css'
import { PromptContent } from './PromptContent'
import { PromptReviewContent } from './PromptReviewContent'

/**
 * Default-mode prompt card (UI_DESIGN §5.2, §4.2): shown instead of `ReferencePanel` while the effective source is
 * the prompt. The review candidates sit in their own `Panel id="prompt-review"` (the reveal target of the
 * review-first flow) once a review generation exists.
 */
export function PromptPanel() {
  const hasReview = useRunStore((s) => selectReviewGeneration(s) !== null)
  return (
    <div className={styles.additions} data-ui-addition="prompt">
      <Panel id="prompt" title="Prompt" icon={<Sparkles size={16} />} subtitle="Text to image, then 3D">
        <PromptContent />
      </Panel>
      {hasReview && (
        <Panel id="prompt-review" title="Generated images" icon={<GalleryHorizontal size={16} />} subtitle="Pick one to build in 3D">
          <PromptReviewContent />
        </Panel>
      )}
    </div>
  )
}
