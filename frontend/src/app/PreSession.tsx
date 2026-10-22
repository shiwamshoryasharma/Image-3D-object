import { LoginScreen } from '../components/auth/LoginScreen'
import { Spinner } from '../components/common/Spinner'
import { BackendBanner } from '../components/layout/BackendBanner'
import { Header } from '../components/layout/Header'
import { useBreakpoint } from '../hooks/useBreakpoint'
import { useAuthStore } from '../store/authStore'
import styles from '../App.module.css'
import type { ShellProps } from './shellRegistry'

/** Before the session exists (splash / login): today's Header, banner and screens, identical in every mode. */
export function PreSession({ nextRetryAt }: ShellProps) {
  const phase = useAuthStore((s) => s.phase)
  const breakpoint = useBreakpoint()
  return (
    <>
      <Header breakpoint={breakpoint} />
      <BackendBanner nextRetryAt={nextRetryAt} />
      <div id="main-content" className={styles.content}>
        {phase === 'login' ? (
          <LoginScreen />
        ) : (
          <div className={styles.splash} role="status">
            <Spinner size={22} />
            <span>Connecting to the Img3D backend…</span>
          </div>
        )}
      </div>
    </>
  )
}
