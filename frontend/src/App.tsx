import { LoginScreen } from './components/auth/LoginScreen'
import { Spinner } from './components/common/Spinner'
import { Toaster } from './components/common/Toaster'
import { HistoryDrawer } from './components/history/HistoryDrawer'
import { AppShell } from './components/layout/AppShell'
import { BackendBanner } from './components/layout/BackendBanner'
import { Header } from './components/layout/Header'
import { BlenderSetupDialog } from './components/meshTuner/BlenderSetupDialog'
import { useBackendSession } from './hooks/useBackendSession'
import { useBreakpoint } from './hooks/useBreakpoint'
import { useAuthStore } from './store/authStore'
import styles from './App.module.css'

export default function App() {
  const { nextRetryAt } = useBackendSession()
  const phase = useAuthStore((s) => s.phase)
  const breakpoint = useBreakpoint()

  return (
    <div className={styles.app}>
      <a className={styles.skipLink} href="#main-content">
        Skip to content
      </a>
      <Header breakpoint={breakpoint} />
      <BackendBanner nextRetryAt={nextRetryAt} />
      <div id="main-content" className={styles.content}>
        {phase === 'checking' ? (
          <div className={styles.splash} role="status">
            <Spinner size={22} />
            <span>Connecting to the Img3D backend…</span>
          </div>
        ) : phase === 'login' ? (
          <LoginScreen />
        ) : (
          <AppShell breakpoint={breakpoint} />
        )}
      </div>
      {breakpoint !== 'mobile' && phase === 'ready' && <HistoryDrawer />}
      {phase === 'ready' && <BlenderSetupDialog />}
      <Toaster />
    </div>
  )
}
