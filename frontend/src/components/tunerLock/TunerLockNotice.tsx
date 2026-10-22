import { Lock } from 'lucide-react'

import { useTunerLockNotice } from '../../hooks/useTunerLocked'
import styles from './TunerLockFrame.module.css'

/**
 * The lock strip on top of the locked Auto Tune card (UI_DESIGN §3.9): `role="note"` with the reason (exactly
 * "Paused until you approve the part-aware milestone" once features are ready), plus a second line outside the note.
 */
export function TunerLockNotice({ id }: { id: string }) {
  const notice = useTunerLockNotice()
  return (
    <div className={styles.notice}>
      <p id={id} role="note" className={styles.noticeText}>
        <Lock size={14} aria-hidden className={styles.noticeIcon} />
        <span>{notice}</span>
      </p>
      <p className={styles.noticeSub}>Existing tuned results stay viewable.</p>
    </div>
  )
}
