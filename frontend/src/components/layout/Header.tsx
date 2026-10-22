import { GalleryVerticalEnd, LogOut, Settings2 } from 'lucide-react'
import { Suspense } from 'react'

import type { Breakpoint } from '../../hooks/useBreakpoint'
import { useTunerLocked } from '../../hooks/useTunerLocked'
import { useTunerVisible } from '../../hooks/useTunerVisible'
import { useAuthStore } from '../../store/authStore'
import { useJobStore } from '../../store/jobStore'
import { useNavigationStore } from '../../store/navigationStore'
import { useUiStore } from '../../store/uiStore'
import { Button, IconButton } from '../common/Button'
import { LazyBlenderRuntimeStatus } from '../tunerLock/lazyTuner.ts'
import { TunerLockFrame } from '../tunerLock/TunerLockFrame'
import { ConnectionIndicator } from './ConnectionIndicator'
import { EngineStatus } from './EngineStatus'
import { GpuMeter } from './GpuMeter'
import { Logo } from './Logo'
import styles from './Header.module.css'

export function Header({ breakpoint }: { breakpoint: Breakpoint }) {
  const authRequired = useAuthStore((s) => s.authRequired)
  const username = useAuthStore((s) => s.username)
  const phase = useAuthStore((s) => s.phase)
  const logout = useAuthStore((s) => s.logout)
  const historyCount = useJobStore((s) => s.order.length)
  const historyOpen = useUiStore((s) => s.historyOpen)
  const setHistoryOpen = useUiStore((s) => s.setHistoryOpen)
  const appSettingsOpen = useNavigationStore((s) => s.appSettingsOpen)
  const setAppSettingsOpen = useNavigationStore((s) => s.setAppSettingsOpen)
  const tunerVisible = useTunerVisible()
  const tunerLocked = useTunerLocked()
  const isMobile = breakpoint === 'mobile'

  return (
    <header className={styles.header}>
      <div className={styles.brand}>
        <Logo size={28} />
        <div className={styles.brandText}>
          <span className={styles.name}>Img3D</span>
          {!isMobile && <span className={styles.tagline}>Image → 3D · TRELLIS.2</span>}
        </div>
      </div>

      <div className={styles.status}>
        {/* System status needs an authenticated session; hide it on the splash / login screens. */}
        {(phase === 'ready' || phase === 'unreachable') && (
          <>
            <EngineStatus compact={isMobile} />
            {phase === 'ready' && tunerVisible && (
              // Paused tuner (UI_DESIGN §3.9): live read-only status while locked; the popover cannot open.
              <TunerLockFrame variant="pill" locked={tunerLocked}>
                <Suspense fallback={null}>
                  <LazyBlenderRuntimeStatus compact={isMobile} />
                </Suspense>
              </TunerLockFrame>
            )}
            {breakpoint === 'desktop' && <GpuMeter />}
            <ConnectionIndicator showLabel={!isMobile} />
          </>
        )}
      </div>

      <div className={styles.actions}>
        {!isMobile && phase === 'ready' && (
          <Button
            variant={historyOpen ? 'subtle' : 'ghost'}
            size="sm"
            icon={<GalleryVerticalEnd size={15} />}
            aria-expanded={historyOpen}
            aria-controls="history-drawer"
            onClick={() => setHistoryOpen(!historyOpen)}
          >
            History{historyCount > 0 ? ` (${historyCount})` : ''}
          </Button>
        )}
        {authRequired && phase === 'ready' && (
          <IconButton
            label={username ? `Sign out ${username}` : 'Sign out'}
            icon={<LogOut size={16} />}
            size="sm"
            onClick={() => void logout()}
          />
        )}
        <IconButton
          label="App settings"
          icon={<Settings2 size={16} />}
          size="sm"
          aria-haspopup="dialog"
          aria-expanded={appSettingsOpen}
          onClick={() => setAppSettingsOpen(true, 'interface')}
        />
      </div>
    </header>
  )
}
