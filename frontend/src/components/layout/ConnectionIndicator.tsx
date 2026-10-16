import { useSystemStore } from '../../store/systemStore'
import styles from './ConnectionIndicator.module.css'

/** Small dot showing the realtime (WebSocket) connection state. */
export function ConnectionIndicator({ showLabel = false }: { showLabel?: boolean }) {
  const realtime = useSystemStore((s) => s.realtime)
  const reachable = useSystemStore((s) => s.reachable)

  let tone: 'live' | 'pending' | 'offline' | 'idle' = 'idle'
  let label = 'Realtime idle'
  if (reachable === false) {
    tone = 'offline'
    label = 'Backend unreachable'
  } else if (realtime.state === 'open') {
    tone = 'live'
    label = 'Live updates connected'
  } else if (realtime.state === 'connecting' || realtime.state === 'reconnecting') {
    tone = 'pending'
    label = realtime.state === 'connecting' ? 'Connecting live updates…' : 'Live updates reconnecting — polling meanwhile'
  } else if (realtime.state === 'closed') {
    tone = 'offline'
    label = 'Live updates disconnected'
  }

  return (
    <span className={styles.indicator} title={label} role="status" aria-label={label}>
      <span className={`${styles.dot} ${styles[tone]}`} aria-hidden />
      {showLabel && <span className={styles.label}>{tone === 'live' ? 'Live' : tone === 'pending' ? 'Polling' : 'Offline'}</span>}
    </span>
  )
}
