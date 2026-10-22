import { AlertTriangle, Info, RotateCw } from 'lucide-react'

import { Button } from '../../components/common/Button'
import { useGenerateAvailability } from '../../hooks/useGenerateAvailability'
import { FEATURE_META } from '../../lib/featureMeta.ts'
import { useNavigationStore } from '../../store/navigationStore'
import { useSystemStore } from '../../store/systemStore'
import { FeatureIcon } from './FeatureIcon'
import styles from './Shell.module.css'

/**
 * Body of the `blocked` sheet (UI_DESIGN §4.1), opened by a blocked FAB / rail Generate: the reason, the note, the
 * engine problem (details + "Retry loading model") and "Open Create". "Open Create" closes this sheet and goes
 * through `requestReveal('create')`, so every shell / posture opens Create its own way.
 */
export function GenerateBlockedContent() {
  const { canGenerate, reason, note, engineProblem } = useGenerateAvailability()
  const loadEngine = useSystemStore((s) => s.loadEngine)
  const loadPending = useSystemStore((s) => s.engineLoadPending)

  const openCreate = () => {
    const nav = useNavigationStore.getState()
    nav.openSheet(null)
    nav.requestReveal('create')
  }

  return (
    <div className={styles.blocked}>
      {reason && (
        <p className={styles.blockedReason} role="status">
          <Info size={16} aria-hidden />
          <span>{reason}</span>
        </p>
      )}
      {!reason && canGenerate && (
        <p className={styles.blockedNote} role="status">
          <Info size={16} aria-hidden />
          <span>Generate is available now.</span>
        </p>
      )}
      {note && (
        <p className={styles.blockedNote}>
          <Info size={16} aria-hidden />
          <span>{note}</span>
        </p>
      )}
      {engineProblem && (
        <div className={styles.blockedProblem}>
          <p className={styles.blockedReason}>
            <AlertTriangle size={16} aria-hidden />
            <span>{engineProblem.summary}</span>
          </p>
          {engineProblem.details && (
            <details className={styles.blockedDetails}>
              <summary>Details &amp; how to fix</summary>
              <pre>{engineProblem.details}</pre>
            </details>
          )}
        </div>
      )}
      <div className={styles.blockedActions}>
        {engineProblem && (
          <Button variant="secondary" icon={<RotateCw size={16} />} loading={loadPending} onClick={() => void loadEngine()}>
            Retry loading model
          </Button>
        )}
        <Button variant="primary" icon={<FeatureIcon id="create" size={16} />} onClick={openCreate}>
          Open {FEATURE_META.create.title}
        </Button>
      </div>
    </div>
  )
}
