import { RefreshCw, ServerCrash } from 'lucide-react'

import { API_BASE_URL } from '../../api/client'
import { useNow } from '../../hooks/useNow'
import { useAuthStore } from '../../store/authStore'
import { useSystemStore } from '../../store/systemStore'
import { Button } from '../common/Button'
import styles from './BackendBanner.module.css'

/**
 * Prominent banner shown while the backend is unreachable (or answered unexpectedly before the session could
 * start). Shows the countdown to the next automatic retry and a manual retry button.
 */
export function BackendBanner({ nextRetryAt }: { nextRetryAt: number | null }) {
  const reachable = useSystemStore((s) => s.reachable)
  const lastError = useSystemStore((s) => s.lastError)
  const realtime = useSystemStore((s) => s.realtime)
  const requestRetry = useSystemStore((s) => s.requestRetry)
  const phase = useAuthStore((s) => s.phase)
  const checkError = useAuthStore((s) => s.checkError)

  const visible = reachable === false || phase === 'unreachable'
  const now = useNow(1000, visible)
  if (!visible) return null

  const retryAt = phase === 'unreachable' ? nextRetryAt : realtime.nextRetryAt
  const seconds = retryAt ? Math.max(0, Math.ceil((retryAt - now) / 1000)) : null
  const error = phase === 'unreachable' ? checkError : lastError
  const unreachable = reachable === false || !error || error.isUnreachable
  const apiLocation = API_BASE_URL.startsWith('http') ? API_BASE_URL : `${window.location.origin}${API_BASE_URL}`

  return (
    <div className={styles.banner} role="region" aria-label="Backend connection">
      <ServerCrash size={18} className={styles.icon} aria-hidden />
      {/* Only the message is a live region; the per-second countdown must not be re-announced. */}
      <div className={styles.text} role="alert">
        <strong>{unreachable ? 'Backend unreachable' : 'Backend error'}</strong>
        <span>
          {unreachable
            ? `Cannot connect to the Img3D API at ${apiLocation}. Start the backend (backend\\main.py); the app reconnects automatically.`
            : `${error?.message ?? 'Unexpected response'}${error?.requestId ? ` (request ${error.requestId})` : ''}`}
        </span>
      </div>
      <div className={styles.actions}>
        {seconds !== null && (
          <span className={styles.countdown} aria-hidden>
            Retrying in {seconds}s
          </span>
        )}
        <Button size="sm" variant="secondary" icon={<RefreshCw size={14} />} onClick={requestRetry}>
          Retry now
        </Button>
      </div>
    </div>
  )
}
