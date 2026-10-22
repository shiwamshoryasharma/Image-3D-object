/**
 * The Default mode (UI_DESIGN §4.2): today's ready UI, composed exactly as `App.tsx` did before the mode switch
 * (Header, BackendBanner, `#main-content` with the responsive AppShell, the History drawer on tablet/desktop), plus
 * the mapping of `navigationStore.requestReveal(target)` onto this shell's navigation.
 */
import type { ShellProps } from '../../app/shellRegistry'
import { HistoryDrawer } from '../../components/history/HistoryDrawer'
import { AppShell } from '../../components/layout/AppShell'
import { BackendBanner } from '../../components/layout/BackendBanner'
import { Header } from '../../components/layout/Header'
import { useBreakpoint, type Breakpoint } from '../../hooks/useBreakpoint'
import { useReveal } from '../../hooks/useReveal'
import { scrollBehavior } from '../../lib/motion'
import { useAuthStore } from '../../store/authStore'
import type { RevealTarget } from '../../store/navigationStore'
import { useUiStore } from '../../store/uiStore'
import appStyles from '../../App.module.css'
import styles from './DefaultShell.module.css'

/** Scrolls the card whose `Panel` heading is `${id}-title` into view; `null` when that panel is not rendered. */
function revealPanel(id: string): HTMLElement | null {
  const section = document.getElementById(`${id}-title`)?.closest('section') ?? null
  section?.scrollIntoView({ behavior: scrollBehavior(), block: 'nearest' })
  return section
}

/** Create/prompt: the input source card (F7) and its switch; without it (legacy backend) the references panel. */
function revealInput(): void {
  const card = revealPanel('input-source')
  if (!card) {
    revealPanel('references')
    return
  }
  const control = card.querySelector<HTMLElement>('[role="radio"][aria-checked="true"], [aria-pressed="true"], button, input')
  control?.focus({ preventScroll: true })
}

function defaultReveal(target: RevealTarget, breakpoint: Breakpoint): void {
  const ui = useUiStore.getState()
  if (breakpoint === 'mobile') {
    if (target === 'create' || target === 'prompt' || target === 'review') ui.setMobileTab('inputs')
    else if (target === 'settings') ui.setMobileTab('settings')
    else if (target === 'history') ui.setMobileTab('history')
    else ui.setMobileTab('viewer')
    return
  }
  if (target === 'parts' || target === 'export') revealPanel('parts')
  else if (target === 'create' || target === 'prompt') revealInput()
  else if (target === 'review') {
    if (!revealPanel('prompt-review')) revealInput()
  } else if (target === 'settings') revealPanel('settings')
  else if (target === 'history') ui.setHistoryOpen(true)
  // 'viewer' / 'progress': the viewer is always visible here; the progress card reveals itself (AppShell).
}

export function DefaultShell({ nextRetryAt }: ShellProps) {
  const breakpoint = useBreakpoint()
  const phase = useAuthStore((s) => s.phase)
  useReveal((target) => defaultReveal(target, breakpoint))
  return (
    <>
      <Header breakpoint={breakpoint} />
      <BackendBanner nextRetryAt={nextRetryAt} />
      <div id="main-content" tabIndex={-1} className={`${appStyles.content} ${styles.main}`}>
        <AppShell breakpoint={breakpoint} />
      </div>
      {breakpoint !== 'mobile' && phase === 'ready' && <HistoryDrawer />}
    </>
  )
}
