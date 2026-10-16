import { AlertTriangle, Info, RotateCw, Sparkles, Square } from 'lucide-react'
import { useEffect, useEffectEvent } from 'react'

import { useGenerateAvailability } from '../../hooks/useGenerateAvailability'
import { selectActiveJob, useJobStore } from '../../store/jobStore'
import { useSystemStore } from '../../store/systemStore'
import { useUiStore } from '../../store/uiStore'
import { Button } from '../common/Button'
import styles from './GenerateCard.module.css'

const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.userAgent)

export interface GenerateCardProps {
  /** Register the global Ctrl/Cmd+Enter shortcut (only one instance should). */
  shortcut?: boolean
  /** Switch to the viewer tab after submitting (mobile). */
  focusViewerOnSubmit?: boolean
  variant?: 'card' | 'bar'
}

/** Generate / Cancel action with disabled reasons, engine-loading note and keyboard shortcut. */
export function GenerateCard({ shortcut = false, focusViewerOnSubmit = false, variant = 'card' }: GenerateCardProps) {
  const { canGenerate, reason, note, busy, engineProblem } = useGenerateAvailability()
  const loadEngine = useSystemStore((s) => s.loadEngine)
  const loadPending = useSystemStore((s) => s.engineLoadPending)
  const submit = useJobStore((s) => s.submit)
  const cancel = useJobStore((s) => s.cancel)
  const submitting = useJobStore((s) => s.submitting)
  const activeJob = useJobStore(selectActiveJob)
  const cancelling = useJobStore((s) => (activeJob ? s.cancellingIds.includes(activeJob.id) : false))
  const supportsCancel = useSystemStore((s) => s.system?.engine.capabilities.supports_cancel ?? true)
  const setMobileTab = useUiStore((s) => s.setMobileTab)

  const generate = async () => {
    if (!canGenerate) return
    const { system, loadEngine } = useSystemStore.getState()
    // The worker waits for the model; make sure a load is actually in progress.
    if (system?.engine.state === 'not_loaded') void loadEngine()
    const ok = await submit()
    if (ok && focusViewerOnSubmit) setMobileTab('viewer')
  }

  const onShortcut = useEffectEvent(() => {
    if (busy) return
    // Numeric fields commit their draft on blur; blur synchronously so the shortcut submits what was typed.
    const focused = document.activeElement
    if (focused instanceof HTMLInputElement || focused instanceof HTMLTextAreaElement) focused.blur()
    void generate()
  })

  useEffect(() => {
    if (!shortcut) return
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Enter' || !(event.ctrlKey || event.metaKey) || event.repeat) return
      event.preventDefault()
      onShortcut()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [shortcut])

  const showCancel = busy && activeJob
  const shortcutLabel = IS_MAC ? '⌘ ↵' : 'Ctrl ↵'

  return (
    <div className={`${styles.card} ${variant === 'bar' ? styles.bar : ''}`}>
      {showCancel ? (
        <Button
          variant="danger"
          size="lg"
          fullWidth
          icon={<Square size={16} />}
          loading={cancelling}
          disabled={!supportsCancel}
          onClick={() => void cancel(activeJob.id)}
          title={supportsCancel ? 'Cancel the running generation' : 'This engine does not support cancellation'}
        >
          {cancelling ? 'Cancelling…' : activeJob.status === 'queued' ? 'Cancel queued job' : 'Cancel generation'}
        </Button>
      ) : (
        <Button
          variant="primary"
          size="lg"
          fullWidth
          icon={<Sparkles size={18} />}
          loading={submitting}
          disabled={!canGenerate}
          onClick={() => void generate()}
          aria-keyshortcuts={IS_MAC ? 'Meta+Enter' : 'Control+Enter'}
          aria-describedby={reason || note ? 'generate-status' : undefined}
          iconRight={<kbd className={styles.kbd}>{shortcutLabel}</kbd>}
        >
          {submitting ? 'Submitting…' : 'Generate 3D model'}
        </Button>
      )}
      {!showCancel && engineProblem ? (
        <div id="generate-status" className={styles.problem} role="status">
          <p className={styles.problemSummary}>
            <AlertTriangle size={13} aria-hidden />
            <span>{engineProblem.summary}</span>
          </p>
          {engineProblem.details && (
            <details className={styles.problemDetails}>
              <summary>Details &amp; how to fix</summary>
              <pre>{engineProblem.details}</pre>
            </details>
          )}
          <Button
            size="sm"
            variant="subtle"
            icon={<RotateCw size={14} />}
            loading={loadPending}
            onClick={() => void loadEngine()}
          >
            Retry loading model
          </Button>
        </div>
      ) : (
        !showCancel &&
        (reason || note) && (
          <p id="generate-status" className={`${styles.status} ${reason ? styles.reason : styles.note}`}>
            <Info size={13} aria-hidden />
            <span>{reason ?? note}</span>
          </p>
        )
      )}
    </div>
  )
}
