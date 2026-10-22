import { AlertTriangle, Info, Play, RotateCw, Sparkles, Square } from 'lucide-react'
import { useEffect, useId, useState } from 'react'

import { runGenerateShortcut } from '../../actions/generation'
import { useGenerateAction, type GenerateActionState } from '../../hooks/useGenerateAction'
import { FEATURE_META } from '../../lib/featureMeta.ts'
import { useAuthStore } from '../../store/authStore'
import { useNavigationStore } from '../../store/navigationStore'
import { useSystemStore } from '../../store/systemStore'
import { Button } from '../common/Button'
import { ProgressRing } from '../common/ProgressRing'
import { Spinner } from '../common/Spinner'
import { VisuallyHidden } from '../common/VisuallyHidden'
import cardStyles from './GenerateCard.module.css'
import styles from './GenerateAction.module.css'

const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.userAgent)

export type GenerateActionVariant = 'card' | 'bar' | 'fab' | 'rail' | 'toolbar'

export interface GenerateActionProps {
  variant: GenerateActionVariant
  /** Toolbar only: number of graph problems shown as a badge on Run. */
  issueCount?: number
}

function cancelLabel(state: GenerateActionState): string {
  const run = state.activeRun
  if (state.phase === 'cancelling') return 'Cancelling…'
  if (run?.status !== 'queued') return 'Cancel generation'
  return run.kind === 'job' ? 'Cancel queued job' : 'Cancel queued generation'
}

/** Default card and mobile bar: today's `GenerateCard` DOM and CSS, driven by the shared state. */
function CardAction({ state, bar }: { state: GenerateActionState; bar: boolean }) {
  const { availability, activeRun, phase, supportsCancel } = state
  const { canGenerate, reason, note, busy, engineProblem } = availability
  const loadEngine = useSystemStore((s) => s.loadEngine)
  const loadPending = useSystemStore((s) => s.engineLoadPending)
  const showCancel = busy && activeRun
  const shortcutLabel = IS_MAC ? '⌘ ↵' : 'Ctrl ↵'

  return (
    <div className={`${cardStyles.card} ${bar ? cardStyles.bar : ''}`}>
      {showCancel ? (
        <Button
          variant="danger"
          size="lg"
          fullWidth
          icon={<Square size={16} />}
          loading={phase === 'cancelling'}
          disabled={!supportsCancel}
          onClick={state.cancel}
          title={supportsCancel ? 'Cancel the running generation' : 'This engine does not support cancellation'}
        >
          {cancelLabel(state)}
        </Button>
      ) : (
        <Button
          variant="primary"
          size="lg"
          fullWidth
          icon={<Sparkles size={18} />}
          loading={phase === 'submitting'}
          disabled={!canGenerate}
          onClick={state.run}
          aria-keyshortcuts={IS_MAC ? 'Meta+Enter' : 'Control+Enter'}
          aria-describedby={reason || note ? 'generate-status' : undefined}
          iconRight={<kbd className={cardStyles.kbd}>{shortcutLabel}</kbd>}
        >
          {state.label}
        </Button>
      )}
      {!showCancel && engineProblem ? (
        <div id="generate-status" className={cardStyles.problem} role="status">
          <p className={cardStyles.problemSummary}>
            <AlertTriangle size={13} aria-hidden />
            <span>{engineProblem.summary}</span>
          </p>
          {engineProblem.details && (
            <details className={cardStyles.problemDetails}>
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
          <p id="generate-status" className={`${cardStyles.status} ${reason ? cardStyles.reason : cardStyles.note}`}>
            <Info size={13} aria-hidden />
            <span>{reason ?? note}</span>
          </p>
        )
      )}
    </div>
  )
}

/**
 * Mobile FAB and the rail button (UI_DESIGN §4.1): ready → run; blocked → `aria-disabled` (still focusable), opens
 * the `blocked` sheet; queued/running → progress ring around a stop icon, opens the `progress` sheet (Cancel lives
 * there, with a confirm); submitting / cancelling → spinner, `aria-busy`.
 */
function RoundAction({ state, rail }: { state: GenerateActionState; rail: boolean }) {
  const reasonId = useId()
  const { phase } = state
  const reason = state.availability.reason ?? state.availability.engineProblem?.summary ?? null
  const working = phase === 'submitting' || phase === 'cancelling'
  const tracking = phase === 'queued' || phase === 'running'
  const opensSheet = phase === 'blocked' || tracking

  const onClick = () => {
    if (phase === 'ready') state.run()
    else if (phase === 'blocked') useNavigationStore.getState().openSheet('blocked')
    else if (tracking) useNavigationStore.getState().openSheet('progress')
  }

  const ringSize = rail ? 52 : 56
  let content
  if (working) {
    content = (
      <>
        <Spinner size={20} />
        <span className={styles.roundLabel}>{phase === 'cancelling' ? 'Cancelling' : 'Submitting'}</span>
      </>
    )
  } else if (tracking) {
    content = (
      <ProgressRing value={phase === 'queued' ? null : state.percent} size={ringSize} thickness={3}>
        <Square size={16} aria-hidden />
      </ProgressRing>
    )
  } else {
    content = (
      <>
        <span className={styles.roundIcon}>
          <Sparkles size={20} aria-hidden />
          {phase === 'blocked' && (
            <span className={styles.blockedBadge} aria-hidden>
              <Info size={11} />
            </span>
          )}
        </span>
        <span className={styles.roundLabel}>{FEATURE_META.generate.shortTitle}</span>
      </>
    )
  }

  return (
    <>
      <button
        type="button"
        className={`${styles.round} ${rail ? styles.rail : styles.fab}`}
        data-phase={phase}
        aria-label={state.ariaLabel}
        aria-disabled={phase === 'blocked' || working ? 'true' : undefined}
        aria-busy={working || undefined}
        aria-haspopup={opensSheet ? 'dialog' : undefined}
        aria-describedby={phase === 'blocked' && reason ? reasonId : undefined}
        aria-keyshortcuts={IS_MAC ? 'Meta+Enter' : 'Control+Enter'}
        onClick={onClick}
      >
        {content}
      </button>
      {phase === 'blocked' && reason && <VisuallyHidden id={reasonId}>{reason}</VisuallyHidden>}
    </>
  )
}

/** Node toolbar: Run / Cancel pair; blocked Run stays focusable (`aria-disabled`) and toggles a reason popover. */
function ToolbarAction({ state, issueCount }: { state: GenerateActionState; issueCount: number }) {
  const popoverId = useId()
  const [reasonOpen, setReasonOpen] = useState(false)
  const { phase, activeRun } = state
  const blocked = phase === 'blocked'
  const reason = state.availability.reason ?? state.availability.engineProblem?.summary ?? null
  const showReason = blocked && reasonOpen && !!reason

  return (
    <div className={styles.toolbar}>
      <Button
        variant="primary"
        size="sm"
        icon={<Play size={14} />}
        loading={phase === 'submitting'}
        aria-disabled={blocked ? 'true' : undefined}
        aria-describedby={showReason ? popoverId : undefined}
        aria-expanded={blocked ? showReason : undefined}
        aria-keyshortcuts={IS_MAC ? 'Meta+Enter' : 'Control+Enter'}
        className={blocked ? styles.softDisabled : undefined}
        onClick={() => (blocked ? setReasonOpen(!reasonOpen) : state.run())}
      >
        Run
        {issueCount > 0 && (
          <span className={styles.issueBadge}>
            {issueCount}
            <VisuallyHidden> {issueCount === 1 ? 'problem' : 'problems'}</VisuallyHidden>
          </span>
        )}
      </Button>
      <Button
        variant="danger"
        size="sm"
        icon={<Square size={12} />}
        loading={phase === 'cancelling'}
        disabled={!activeRun || !state.supportsCancel}
        onClick={state.cancel}
      >
        Cancel
      </Button>
      {showReason && (
        <p id={popoverId} role="status" className={styles.reasonPopover}>
          <Info size={13} aria-hidden />
          <span>{reason}</span>
        </p>
      )}
    </div>
  )
}

/**
 * The shared Generate control (UI_DESIGN §4.1) over `useGenerateAction()`: `card` / `bar` (Default, identical to
 * the old `GenerateCard`), `fab` (Mobile, Tablet bottom/compact), `rail` (Tablet rail, Mobile landscape rail) and
 * `toolbar` (Node). Every variant runs `startGeneration()`; Ctrl/Cmd+Enter is registered once in
 * `app/GlobalShortcuts.tsx`.
 */
export function GenerateAction({ variant, issueCount = 0 }: GenerateActionProps) {
  const state = useGenerateAction()
  if (variant === 'card' || variant === 'bar') return <CardAction state={state} bar={variant === 'bar'} />
  if (variant === 'toolbar') return <ToolbarAction state={state} issueCount={issueCount} />
  return <RoundAction state={state} rail={variant === 'rail'} />
}

/**
 * The ONE Ctrl/Cmd+Enter listener (UI6), mounted once by `app/GlobalShortcuts.tsx` (app/ cannot import actions/,
 * UI_DESIGN §1.4). Active while a shell is mounted (session ready, or unreachable as before); every mode generates
 * through `runGenerateShortcut()` → `startGeneration()`.
 */
export function GenerateShortcut() {
  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key !== 'Enter' || !(event.ctrlKey || event.metaKey) || event.repeat) return
      const phase = useAuthStore.getState().phase
      if (phase !== 'ready' && phase !== 'unreachable') return
      event.preventDefault()
      runGenerateShortcut()
    }
    window.addEventListener('keydown', onKeyDown)
    return () => window.removeEventListener('keydown', onKeyDown)
  }, [])
  return null
}
