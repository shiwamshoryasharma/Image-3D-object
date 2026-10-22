import { useId } from 'react'

import { useFeatures } from '../../hooks/useFeatures'
import type { InputSource } from '../../lib/forms.ts'
import { effectiveSource, reasonClause } from '../../lib/generatePlan.ts'
import { useInputStore } from '../../store/inputStore'
import { SegmentedControl } from '../common/SegmentedControl'
import styles from './Input.module.css'

/**
 * "Image | Prompt" (UI_DESIGN §5.1) bound to `inputStore.source`. Shows the effective source; the Image segment is
 * never disabled (UI11). While features load the Prompt segment reads "Checking…"; when prompt-to-3D is unavailable
 * it is disabled with the reason, and the stored choice and draft are kept for when the capability returns.
 */
export function InputSourceSwitch() {
  const features = useFeatures()
  const source = useInputStore((s) => s.source)
  const setSource = useInputStore((s) => s.setSource)
  const noteId = useId()

  const effective = effectiveSource(source, features)
  const unavailable = (features.status === 'ready' || features.status === 'legacy') && !features.prompt.available
  const reason = unavailable ? reasonClause(features.prompt) : null
  const note = reason
    ? source === 'prompt'
      ? `Prompt-to-3D is unavailable (${reason}). Using your images.`
      : `Prompt-to-3D is unavailable (${reason}).`
    : null

  return (
    <div className={styles.stack} data-testid="source-switch" aria-describedby={note ? noteId : undefined}>
      <SegmentedControl<InputSource>
        label="Input source"
        value={effective}
        options={[
          { value: 'image', label: 'Image' },
          {
            value: 'prompt',
            label: features.status === 'loading' ? 'Checking…' : 'Prompt',
            disabled: unavailable,
            title: note ?? 'Describe the character or object in words',
          },
        ]}
        onChange={(next) => setSource(next)}
      />
      {note && (
        <p id={noteId} className={styles.note}>
          {note}
        </p>
      )}
    </div>
  )
}
