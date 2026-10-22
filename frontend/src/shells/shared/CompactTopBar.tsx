import { ChevronDown } from 'lucide-react'

import { Spinner } from '../../components/common/Spinner'
import { ConnectionIndicator } from '../../components/layout/ConnectionIndicator'
import { STATE_LABEL } from '../../components/layout/EngineStatus'
import { Logo } from '../../components/layout/Logo'
import { useNavigationStore } from '../../store/navigationStore'
import { useSystemStore } from '../../store/systemStore'
import { AppSettingsButton } from './AppSettingsButton'
import { openShellSheet, sheetElementId } from './sheets'
import { BACKEND_OFFLINE_TEXT, CONNECTING_TEXT } from './shellText'
import styles from './Shell.module.css'

/**
 * Status pill: the engine state (as `EngineStatus compact`) plus the realtime dot (`ConnectionIndicator`, laid over
 * the pill's end, not inside the button). One button opens the `status` sheet.
 */
export function StatusPill() {
  const engineState = useSystemStore((s) => s.system?.engine.state ?? null)
  const reachable = useSystemStore((s) => s.reachable)
  const open = useNavigationStore((s) => s.sheet === 'status')

  const text = engineState ? STATE_LABEL[engineState] : reachable === false ? BACKEND_OFFLINE_TEXT : CONNECTING_TEXT
  const tone = engineState ?? (reachable === false ? 'offline' : 'unknown')

  return (
    <div className={styles.statusPill}>
      <button
        type="button"
        className={styles.statusButton}
        data-tone={tone}
        aria-label={`System status: ${text}. Show details`}
        aria-haspopup="dialog"
        aria-expanded={open}
        aria-controls={sheetElementId('status')}
        onClick={() => openShellSheet('status')}
      >
        {engineState === 'loading' || (engineState === null && reachable !== false) ? (
          <Spinner size={12} />
        ) : (
          <span className={styles.statusDot} aria-hidden />
        )}
        <span className={styles.statusText}>{text}</span>
        <ChevronDown size={14} aria-hidden className={styles.statusChevron} />
      </button>
      <span className={styles.statusConnection}>
        <ConnectionIndicator />
      </span>
    </div>
  )
}

/**
 * Top bar of the Mobile and Tablet bottom/compact layouts (UI_DESIGN §4.1): `--top-bar-height` (56 px) plus the top
 * safe area; logo, status pill (→ `status` sheet), App settings gear. Every target is ≥ `--hit-min`.
 */
export function CompactTopBar() {
  return (
    <header className={styles.topBar}>
      <div className={styles.brand}>
        <Logo size={28} />
        <span className={styles.brandName}>Img3D</span>
      </div>
      <StatusPill />
      <AppSettingsButton />
    </header>
  )
}
