import { useId, type ReactNode } from 'react'

import { useTunerLockNotice } from '../../hooks/useTunerLocked'
import { VisuallyHidden } from '../common/VisuallyHidden'
import { TunerLockNotice } from './TunerLockNotice'
import styles from './TunerLockFrame.module.css'

export interface TunerLockFrameProps {
  locked: boolean
  variant: 'card' | 'pill'
  children: ReactNode
}

/**
 * Locks a paused tuner mount point without editing the protected tuner files (UI_DESIGN §3.9): a disabled
 * `<fieldset>` disables every button/input/select/textarea inside it (no click, no keyboard activation, reported as
 * disabled), while `<details>`/`<summary>` and links keep working, so status and past results stay readable.
 * Unlocked (legacy backend, or approved) it renders its children with no wrapper: today's DOM exactly.
 */
export function TunerLockFrame({ locked, variant, children }: TunerLockFrameProps) {
  const noticeId = useId()
  const notice = useTunerLockNotice()
  if (!locked) return children
  if (variant === 'pill') {
    return (
      <fieldset
        className={styles.pillLock}
        disabled
        aria-disabled="true"
        aria-describedby={noticeId}
        data-tuner-lock="locked"
      >
        {children}
        <VisuallyHidden id={noticeId}>Blender setup: {notice}</VisuallyHidden>
      </fieldset>
    )
  }
  return (
    <div className={styles.cardLock} data-tuner-lock="locked">
      <TunerLockNotice id={noticeId} />
      <fieldset className={styles.fieldset} disabled aria-disabled="true" aria-describedby={noticeId}>
        {children}
      </fieldset>
    </div>
  )
}
