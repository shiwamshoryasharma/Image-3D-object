import { RotateCw } from 'lucide-react'
import { Component, type ErrorInfo, type ReactNode } from 'react'

import { Button } from '../components/common/Button'
import { UI_MODE_META, type UiMode } from '../lib/uiModes.ts'
import { useNavigationStore } from '../store/navigationStore'
import { prepareMode } from './shellRegistry'
import styles from './ShellFallback.module.css'

interface ShellErrorBoundaryProps {
  /** Also the boundary's `key`, so a mode change starts clean. */
  mode: UiMode
  children: ReactNode
}

interface ShellErrorBoundaryState {
  failed: boolean
}

/**
 * FAILED state of the mode switch (UI_DESIGN §3.7). [Retry] reloads the page: `React.lazy` caches a rejected payload
 * and the browser caches the failed module fetch, so a remount would throw again. [Use Default UI for this session]
 * sets the session override only; the saved preference is unchanged.
 */
export class ShellErrorBoundary extends Component<ShellErrorBoundaryProps, ShellErrorBoundaryState> {
  state: ShellErrorBoundaryState = { failed: false }

  static getDerivedStateFromError(): ShellErrorBoundaryState {
    return { failed: true }
  }

  componentDidCatch(error: unknown, info: ErrorInfo): void {
    console.error(`[img3d] ${UI_MODE_META[this.props.mode].name} failed to load`, error, info.componentStack)
  }

  render(): ReactNode {
    if (!this.state.failed) return this.props.children
    const { mode } = this.props
    return (
      <main id="main-content" tabIndex={-1} className={styles.failed}>
        <div role="alert">
          <p className={styles.failedTitle}>Couldn&apos;t load the {UI_MODE_META[mode].name}.</p>
          <p className={styles.failedText}>Check the connection to the app server, then try again.</p>
        </div>
        <div className={styles.failedActions}>
          <Button variant="primary" icon={<RotateCw size={15} />} onClick={() => window.location.reload()}>
            Retry
          </Button>
          {mode !== 'default' && (
            <Button variant="secondary" onClick={() => void prepareMode('default').then(() => useNavigationStore.getState().setSessionUiOverride('default'))}>
              Use Default UI for this session
            </Button>
          )}
        </div>
      </main>
    )
  }
}
