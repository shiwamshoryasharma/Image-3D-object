import { Flame } from 'lucide-react'

import { warmUpModel } from '../../actions/models'
import { useFeatures } from '../../hooks/useFeatures'
import { useFeaturesStore } from '../../store/featuresStore'
import { Button } from '../common/Button'
import { ProgressRing } from '../common/ProgressRing'
import styles from './Input.module.css'

const minutes = (seconds: number | null): number => Math.max(1, Math.ceil((seconds ?? 60) / 60))

/**
 * Text-to-image cold start (UI_DESIGN §5.4), driven by `features.prompt.defaultModelState`; state changes arrive
 * through `features.updated` (no client timer). `loaded` hides it; unavailable states are the form's reason.
 */
export function ColdStartNotice() {
  const { prompt } = useFeatures()
  const modelId = prompt.defaultModelId
  const warming = useFeaturesStore((s) => (modelId ? s.modelActionIds.includes(modelId) : false))
  const state = prompt.defaultModelState

  if (!prompt.available || !modelId) return null
  if (state === 'installed') {
    return (
      <div className={styles.cold} role="note">
        <p className={styles.coldRow}>
          <Flame size={14} aria-hidden />
          <span>
            The text-to-image model is not in memory. The first image takes about {minutes(prompt.coldStartS)} min; later
            ones take seconds.
          </span>
        </p>
        <div>
          <Button size="sm" variant="secondary" loading={warming} onClick={() => void warmUpModel(modelId)}>
            Warm up now
          </Button>
        </div>
      </div>
    )
  }
  if (state === 'loading') {
    return (
      <div className={styles.cold} role="note">
        <p className={styles.coldRow}>
          <ProgressRing value={null} size={16} label="Loading the text-to-image model" />
          <span>Loading the text-to-image model… (about {minutes(prompt.coldStartS)} min the first time)</span>
        </p>
      </div>
    )
  }
  return null
}
