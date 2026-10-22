/**
 * Tablet mode (UI_DESIGN §4.5). The posture comes from the viewport store (`lib/viewport.nextTabletPosture`: the
 * §4.5.1 table with both hysteresis bands, the typing guard, a 120 ms trailing debounce for width-only changes and
 * an immediate `orientationchange`): `rail` = `RailLayout`, `bottom` = `BottomLayout`, `compact` = `PhoneLayout`.
 * The selected section lives in `navigationStore` and the section sheet's snap here, so a flip keeps both.
 */
import { useEffect, useState } from 'react'

import type { ShellProps } from '../../app/shellRegistry'
import { useNavPosture } from '../../hooks/useNavPosture'
import { viewportStore } from '../../hooks/useViewport'
import type { SheetSnap } from '../../store/navigationStore'
import { PhoneLayout } from '../shared/PhoneLayout'
import { BottomLayout } from './BottomLayout'
import { RailLayout } from './RailLayout'
import { carryNavigation, DEFAULT_SECTION_SNAP } from './tabletNav'
import styles from './TabletShell.module.css'

export function TabletShell({ nextRetryAt }: ShellProps) {
  const posture = useNavPosture('tablet')
  const [sectionSnap, setSectionSnap] = useState<SheetSnap>(DEFAULT_SECTION_SNAP)

  // Hand the open section over between the panel postures and PhoneLayout's sheets whenever the posture changes
  // (in the store listener, before the new layout renders), and once on mount.
  useEffect(() => {
    let last = viewportStore.getSnapshot().posture.tablet
    carryNavigation(last)
    return viewportStore.subscribe(() => {
      const next = viewportStore.getSnapshot().posture.tablet
      if (next === last) return
      last = next
      carryNavigation(next)
    })
  }, [])

  return (
    <div className={styles.root}>
      {posture === 'compact' ? (
        <PhoneLayout nextRetryAt={nextRetryAt} />
      ) : posture === 'rail' ? (
        <RailLayout nextRetryAt={nextRetryAt} />
      ) : (
        <BottomLayout nextRetryAt={nextRetryAt} snap={sectionSnap} onSnapChange={setSectionSnap} />
      )}
    </div>
  )
}
