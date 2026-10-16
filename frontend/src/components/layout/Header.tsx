import { GalleryVerticalEnd, LogOut } from 'lucide-react'

import type { Breakpoint } from '../../hooks/useBreakpoint'
import { useAuthStore } from '../../store/authStore'
import { useJobStore } from '../../store/jobStore'
import { useUiStore } from '../../store/uiStore'
import { Button, IconButton } from '../common/Button'
import { BlenderRuntimeStatus } from '../meshTuner/BlenderRuntimeStatus'
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
            {phase === 'ready' && <BlenderRuntimeStatus compact={isMobile} />}
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
      </div>
    </header>
  )
}
