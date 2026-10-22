import { GlobalOverlays } from './app/GlobalOverlays'
import { ModeSwitch } from './app/ModeSwitch'
import { PreSession } from './app/PreSession'
import { useBackendSession } from './hooks/useBackendSession'
import { useAuthStore } from './store/authStore'
import styles from './App.module.css'

/**
 * App root (UI_DESIGN §3.7): the one `useBackendSession()` (UI6), the splash/login screens before a session, the
 * mode switch once there is one, and the app-wide overlays. An unreachable backend keeps today's behaviour: the
 * shell stays visible under the banner (it was the `else` branch of the old root).
 */
export default function App() {
  const { nextRetryAt } = useBackendSession()
  const phase = useAuthStore((s) => s.phase)
  const session = phase === 'ready' || phase === 'unreachable'

  return (
    <div className={styles.app}>
      <a className={styles.skipLink} href="#main-content">
        Skip to content
      </a>
      {session ? <ModeSwitch nextRetryAt={nextRetryAt} /> : <PreSession nextRetryAt={nextRetryAt} />}
      <GlobalOverlays />
    </div>
  )
}
