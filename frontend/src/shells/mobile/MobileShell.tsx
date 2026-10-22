/**
 * Mobile mode (UI_DESIGN §4.4). Portrait = the shared `PhoneLayout` (top bar, banner, full-bleed viewer, floating nav
 * with the Generate FAB, bottom sheets). Landscape (`nextMobilePosture`, e.g. 812×375, 740×360) = a floating left
 * `NavRail` (sections + Generate + App settings) beside the viewer, with the same sheets opening from the right.
 * Both postures share `navigationStore.sheet`, so a rotation keeps the open sheet.
 */
import type { ShellProps } from '../../app/shellRegistry'
import { NavRail } from '../../components/common/NavRail'
import { GenerateAction } from '../../components/generation/GenerateAction'
import { BackendBanner } from '../../components/layout/BackendBanner'
import { ViewerPanel } from '../../components/viewer/ViewerPanel'
import { useNavPosture } from '../../hooks/useNavPosture'
import { useReveal } from '../../hooks/useReveal'
import { useSoftKeyboard } from '../../hooks/useSoftKeyboard'
import { AppSettingsButton } from '../shared/AppSettingsButton'
import { useShellNav } from '../shared/navItems'
import { PhoneLayout } from '../shared/PhoneLayout'
import { ProgressPill } from '../shared/ProgressPill'
import { SheetHost } from '../shared/SheetHost'
import { applySheetReveal } from '../shared/sheets'
import styles from './MobileShell.module.css'

/** Landscape posture: rail + viewer + right-hand side sheets (same sheet table and reveal map as portrait). */
function LandscapeLayout({ nextRetryAt }: ShellProps) {
  // Keeps `--keyboard-inset` current, so the Create sheet's Generate footer stays above the soft keyboard.
  useSoftKeyboard()
  const nav = useShellNav({ kind: 'sheets' })
  useReveal(applySheetReveal)

  return (
    <>
      <NavRail
        label="Main"
        items={nav.items}
        value={nav.value}
        onSelect={nav.onSelect}
        footer={
          <>
            <GenerateAction variant="rail" />
            <AppSettingsButton />
          </>
        }
      />
      <div className={styles.railBody}>
        <BackendBanner nextRetryAt={nextRetryAt} />
        <main id="main-content" tabIndex={-1} className={styles.railMain}>
          <ViewerPanel />
          <ProgressPill />
        </main>
      </div>
      <SheetHost side="right" />
    </>
  )
}

export function MobileShell({ nextRetryAt }: ShellProps) {
  const posture = useNavPosture('mobile')
  return (
    <div className={styles.root}>
      {posture === 'portrait' ? <PhoneLayout nextRetryAt={nextRetryAt} /> : <LandscapeLayout nextRetryAt={nextRetryAt} />}
    </div>
  )
}
