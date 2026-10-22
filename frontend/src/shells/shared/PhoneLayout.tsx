import { FloatingNav } from '../../components/common/FloatingNav'
import { GenerateAction } from '../../components/generation/GenerateAction'
import { BackendBanner } from '../../components/layout/BackendBanner'
import { ViewerPanel } from '../../components/viewer/ViewerPanel'
import { useReveal } from '../../hooks/useReveal'
import { useSoftKeyboard } from '../../hooks/useSoftKeyboard'
import { CompactTopBar } from './CompactTopBar'
import { useShellNav } from './navItems'
import { ProgressPill } from './ProgressPill'
import { SheetHost } from './SheetHost'
import { applySheetReveal } from './sheets'
import styles from './Shell.module.css'

export interface PhoneLayoutProps {
  /** `ShellProps.nextRetryAt`: the backend banner's countdown while the backend is unreachable. */
  nextRetryAt: number | null
}

/**
 * The whole Mobile portrait layout (UI_DESIGN §4.4), reused as Tablet `compact`: top bar, backend banner (shells mount
 * while the backend is unreachable too), the full-bleed viewer as `#main-content` with the progress pill, the floating
 * nav with the Generate FAB (hidden while the soft keyboard is up) and every sheet. Maps reveal targets onto sheets.
 */
export function PhoneLayout({ nextRetryAt }: PhoneLayoutProps) {
  const keyboardOpen = useSoftKeyboard()
  const nav = useShellNav({ kind: 'sheets' })
  useReveal(applySheetReveal)

  return (
    <>
      <CompactTopBar />
      <BackendBanner nextRetryAt={nextRetryAt} />
      <main id="main-content" tabIndex={-1} className={styles.phoneMain}>
        <ViewerPanel />
        <ProgressPill />
      </main>
      <FloatingNav
        label="Main"
        items={nav.items}
        value={nav.value}
        onSelect={nav.onSelect}
        center={<GenerateAction variant="fab" />}
        hidden={keyboardOpen}
      />
      <SheetHost />
    </>
  )
}
