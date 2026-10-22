import { UI_MODE_META, type UiMode } from '../lib/uiModes.ts'
import styles from './ShellFallback.module.css'

/** Suspense fallback while a lazy shell chunk loads (UI_DESIGN §3.7): a pure-CSS skeleton of that shell. */
export function ShellFallback({ mode }: { mode: UiMode }) {
  return (
    <div className={styles.fallback} data-shell={mode} aria-busy="true">
      <span className="visually-hidden" role="status">
        Loading the {UI_MODE_META[mode].name}…
      </span>
      <div className={styles.topBar} aria-hidden="true" />
      <div className={styles.body} aria-hidden="true">
        <div className={styles.side} />
        <div className={styles.viewer} />
      </div>
      <div className={styles.nav} aria-hidden="true" />
    </div>
  )
}
