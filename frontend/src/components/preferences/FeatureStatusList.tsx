import { CircleCheck, CircleOff, CirclePause, RotateCw } from 'lucide-react'

import { useFeatures } from '../../hooks/useFeatures'
import type { CapabilityView } from '../../lib/featureFlags.ts'
import { PAUSED_FEATURE_TEXT, SEG_PROFILE_LABELS } from '../../lib/featureMeta.ts'
import { useFeaturesStore } from '../../store/featuresStore'
import { Button } from '../common/Button'
import styles from './preferences.module.css'

type RowState = 'available' | 'unavailable' | 'paused'

interface Row {
  label: string
  state: RowState
  reason: string | null
  detail?: string | null
}

const STATE_TEXT: Readonly<Record<RowState, string>> = Object.freeze({
  available: 'Available',
  unavailable: 'Unavailable',
  paused: PAUSED_FEATURE_TEXT,
})

function capabilityRow(label: string, view: CapabilityView, detail?: string | null): Row {
  return { label, state: view.available ? 'available' : 'unavailable', reason: view.available ? null : view.reason, detail }
}

function listed(items: readonly { label: string; available: boolean }[]): string | null {
  if (items.length === 0) return null
  return items.map((item) => (item.available ? item.label : `${item.label} (unavailable)`)).join(', ')
}

function StateIcon({ state }: { state: RowState }) {
  if (state === 'available') return <CircleCheck size={15} aria-hidden className={styles.iconOk} />
  if (state === 'paused') return <CirclePause size={15} aria-hidden className={styles.iconPaused} />
  return <CircleOff size={15} aria-hidden className={styles.iconOff} />
}

/** Read-only capability list from `/features` (UI_DESIGN §3.8): state text + reason for every feature. */
export function FeatureStatusList() {
  const f = useFeatures()
  const refresh = useFeaturesStore((s) => s.refresh)

  if (f.status === 'loading') return <p className={styles.hint}>Checking which features this backend offers…</p>
  if (f.status === 'legacy') {
    return (
      <p className={styles.hint}>
        This backend predates the part-aware features. Image-to-3D and Auto Tune work as before.
      </p>
    )
  }
  if (f.status === 'error') {
    return (
      <div className={styles.inlineRow}>
        <p className={styles.hint}>Could not read the backend features.</p>
        <Button size="sm" variant="subtle" icon={<RotateCw size={14} />} onClick={() => void refresh()}>
          Retry
        </Button>
      </div>
    )
  }

  const rows: Row[] = [
    capabilityRow('Image-to-3D', f.imageTo3d),
    capabilityRow('Prompt-to-3D', f.prompt),
    capabilityRow(
      'Segmentation',
      f.segmentation,
      listed(f.segmentation.profiles.map((p) => ({ label: SEG_PROFILE_LABELS[p.id], available: p.available }))),
    ),
    capabilityRow('Part extraction', f.parts, listed(f.parts.strategies)),
    capabilityRow('Workflows', f.workflow),
    {
      label: 'Auto Mesh Tuner',
      state: f.meshTuning.enabled ? 'available' : 'paused',
      reason: f.meshTuning.enabled ? null : f.meshTuning.reason,
      detail: f.meshTuning.enabled ? null : 'Shown locked in the Default UI; tuned results stay viewable.',
    },
    {
      label: 'Part refinement',
      state: f.partRefinement.enabled ? 'available' : 'paused',
      reason: f.partRefinement.enabled ? null : f.partRefinement.reason,
    },
  ]

  return (
    <ul className={styles.statusList}>
      {rows.map((row) => (
        <li key={row.label} className={styles.statusRow}>
          <span className={styles.statusName}>{row.label}</span>
          <span className={styles.statusState} data-state={row.state}>
            <StateIcon state={row.state} />
            {STATE_TEXT[row.state]}
          </span>
          {row.reason && <span className={styles.statusReason}>{row.reason}</span>}
          {row.detail && <span className={styles.statusDetail}>{row.detail}</span>}
        </li>
      ))}
    </ul>
  )
}
