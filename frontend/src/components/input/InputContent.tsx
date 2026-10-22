import { Suspense } from 'react'

import { useFeatures } from '../../hooks/useFeatures'
import { effectiveSource } from '../../lib/generatePlan.ts'
import { useInputStore } from '../../store/inputStore'
import { Skeleton } from '../common/Skeleton'
import { LazyPromptContent, LazyPromptReviewContent } from '../lazyFeatures'
import { ReferenceContent } from '../references/ReferenceContent'
import styles from './Input.module.css'
import { InputSourceSwitch } from './InputSourceSwitch'

export interface InputContentProps {
  /** Owns the reference paste listener while true (only one mounted input at a time). */
  active?: boolean
  headingLevel?: 2 | 3
  compact?: boolean
}

/**
 * Create ▸ Input for Mobile and Tablet (UI_DESIGN §5.1): the source switch, then the reference editor or the prompt
 * form + review candidates for the effective source. The prompt parts come from the feature chunk.
 */
export function InputContent({ active = true }: InputContentProps) {
  const features = useFeatures()
  const source = useInputStore((s) => s.source)
  const effective = effectiveSource(source, features)

  return (
    <div className={styles.root}>
      <InputSourceSwitch />
      {effective === 'image' ? (
        <ReferenceContent active={active} />
      ) : (
        <Suspense
          fallback={
            <div aria-busy="true">
              <Skeleton shape="block" height={160} />
            </div>
          }
        >
          <LazyPromptContent />
          <LazyPromptReviewContent />
        </Suspense>
      )}
    </div>
  )
}
