import { Square } from 'lucide-react'
import { Suspense, useId, useState, type ReactNode } from 'react'

import { Button } from '../../components/common/Button'
import { Sheet } from '../../components/common/Sheet'
import { Skeleton } from '../../components/common/Skeleton'
import { GenerateAction } from '../../components/generation/GenerateAction'
import { ProgressContent } from '../../components/generation/ProgressContent'
import { LazyPromptReviewContent } from '../../components/lazyFeatures'
import { useGenerateAction } from '../../hooks/useGenerateAction'
import { useNavigationStore, type SheetId } from '../../store/navigationStore'
import { GenerateBlockedContent } from './GenerateBlockedContent'
import { SectionHost } from './SectionHost'
import { openShellSheet, resolveSheetSnap, SECTION_SHEETS, SHEET_IDS, SHEET_SPECS, sheetElementId } from './sheets'
import { StatusSheetContent } from './StatusSheetContent'
import styles from './Shell.module.css'

export interface SheetHostProps {
  /** `right` = side sheets (Mobile landscape, Tablet rail); default `bottom`. */
  side?: 'bottom' | 'right'
  /**
   * Mount the section sheets (`create`, `parts`, `library`). `false` where the sections live in a side panel or the
   * shell's own section sheet (Tablet rail / bottom): only `review`, `progress`, `blocked` and `status` mount.
   */
  sections?: boolean
}

/** `ProgressContent` + Cancel with a confirm step (the FAB / rail open this sheet while a run is queued or running). */
function ProgressSheetContent() {
  const state = useGenerateAction()
  const confirmId = useId()
  const [confirmingRun, setConfirmingRun] = useState<string | null>(null)
  const run = state.activeRun
  const cancellable = run !== null && (state.phase === 'queued' || state.phase === 'running' || state.phase === 'cancelling')
  const confirming = run !== null && confirmingRun === run.id && state.phase !== 'cancelling'
  const cancelLabel = run?.status === 'queued' ? 'Cancel queued generation' : 'Cancel generation'

  return (
    <div className={styles.sheetBody}>
      <ProgressContent headingLevel={3} />
      {cancellable && run && (
        <div className={styles.cancelRow}>
          <Button
            variant="danger"
            icon={<Square size={16} />}
            loading={state.phase === 'cancelling'}
            disabled={!state.supportsCancel}
            aria-expanded={confirming}
            aria-controls={confirmId}
            title={state.supportsCancel ? undefined : 'This engine does not support cancellation'}
            onClick={() => setConfirmingRun(confirming ? null : run.id)}
          >
            {state.phase === 'cancelling' ? 'Cancelling…' : `${cancelLabel}…`}
          </Button>
          <div id={confirmId} className={styles.confirm} hidden={!confirming}>
            {confirming && (
              <>
                <p>Stop this run? Its progress is lost.</p>
                <div className={styles.confirmActions}>
                  <Button
                    variant="danger"
                    onClick={() => {
                      setConfirmingRun(null)
                      state.cancel()
                    }}
                  >
                    {cancelLabel}
                  </Button>
                  <Button variant="secondary" onClick={() => setConfirmingRun(null)}>
                    Keep running
                  </Button>
                </div>
              </>
            )}
          </div>
        </div>
      )}
    </div>
  )
}

const fallback = (
  <div aria-busy="true" className={styles.sectionFallback}>
    <Skeleton shape="block" height={160} />
  </div>
)

/** One body per `SheetId` (a `Record`: a new id fails the type check until it is mapped here). */
const SHEET_BODIES: Readonly<Record<SheetId, (open: boolean) => ReactNode>> = {
  create: (open) => <SectionHost section="create" active={open} />,
  review: () => (
    <div className={styles.sheetBody}>
      <Suspense fallback={fallback}>
        <LazyPromptReviewContent headingLevel={3} />
      </Suspense>
    </div>
  ),
  parts: (open) => <SectionHost section="parts" active={open} />,
  library: (open) => <SectionHost section="library" active={open} />,
  progress: () => <ProgressSheetContent />,
  blocked: () => <GenerateBlockedContent />,
  status: () => <StatusSheetContent />,
}

/**
 * Mounts the sheets of UI_DESIGN §4.4 from `navigationStore.sheet` / `sheetSnap` (Mobile, Tablet bottom/compact; the
 * transient sheets also in the Tablet rail). Each sheet's element id is `sheetElementId(id)`, the target of its
 * opener's `aria-controls`. The stored snap is coerced to the sheet's allowed snaps; Create has the sticky
 * `GenerateAction variant="bar"` footer, so Generate stays reachable above the soft keyboard.
 */
export function SheetHost({ side = 'bottom', sections = true }: SheetHostProps) {
  const sheet = useNavigationStore((s) => s.sheet)
  const snap = useNavigationStore((s) => s.sheetSnap)
  const setSheetSnap = useNavigationStore((s) => s.setSheetSnap)

  return (
    <>
      {SHEET_IDS.filter((id) => sections || !SECTION_SHEETS.has(id)).map((id) => {
        const spec = SHEET_SPECS[id]
        const open = sheet === id
        return (
          <Sheet
            key={id}
            id={sheetElementId(id)}
            title={spec.title}
            open={open}
            onClose={() => {
              if (useNavigationStore.getState().sheet === id) openShellSheet(null)
            }}
            modal={spec.modal}
            side={side}
            snaps={spec.snaps}
            snap={resolveSheetSnap(id, snap)}
            onSnapChange={setSheetSnap}
            footer={id === 'create' ? <GenerateAction variant="bar" /> : undefined}
          >
            {SHEET_BODIES[id](open)}
          </Sheet>
        )
      })}
    </>
  )
}
