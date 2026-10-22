import { Scale } from 'lucide-react'

import type { LicenceNotice } from '../../lib/featureFlags.ts'
import styles from './PartOptionsSection.module.css'

export interface LicenseNoticeProps {
  notice: LicenceNotice
  /** Subject = Realistic human or Profile = Realistic (UI_DESIGN §6.2, Q4). */
  emphasised?: boolean
}

/** Licence name + conditions of a usable segmentation model (from `/features`; nothing hard-coded). */
export function LicenseNotice({ notice, emphasised = false }: LicenseNoticeProps) {
  return (
    <div className={`${styles.notice} ${emphasised ? styles.noticeEmphasised : ''}`} role="note">
      <p className={styles.noticeTitle}>
        <Scale size={13} aria-hidden />
        <span>Licence: {notice.name}</span>
      </p>
      <ul className={styles.noticeList}>
        {notice.conditions.map((condition) => (
          <li key={condition}>{condition}</li>
        ))}
      </ul>
    </div>
  )
}
