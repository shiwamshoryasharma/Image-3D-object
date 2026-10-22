import { Settings2 } from 'lucide-react'
import { lazy, Suspense } from 'react'

import { FEATURE_META } from '../../lib/featureMeta.ts'
import { useNavigationStore } from '../../store/navigationStore'
import { Dialog } from '../common/Dialog'
import { Spinner } from '../common/Spinner'
import type { ModeActions } from './preferencesText'
import styles from './preferences.module.css'

const AppSettingsBody = lazy(() => import('./AppSettingsBody.tsx').then((m) => ({ default: m.AppSettingsBody })))

/**
 * App settings (UI_DESIGN §3.8): tiny and eager; the body is a lazy chunk loaded on first open. Opened by the
 * "App settings" gear of every shell and by Ctrl/Cmd+`,` (`navigationStore.setAppSettingsOpen`). Lives in
 * `app/GlobalOverlays`, so it survives a mode switch.
 */
export function AppSettingsDialog({ prepareMode, prefetchMode }: ModeActions) {
  const open = useNavigationStore((s) => s.appSettingsOpen)
  const setOpen = useNavigationStore((s) => s.setAppSettingsOpen)
  return (
    <Dialog
      open={open}
      onClose={() => setOpen(false)}
      title={FEATURE_META.appSettings.title}
      subtitle={FEATURE_META.appSettings.description}
      icon={<Settings2 size={18} />}
      size="lg"
    >
      <Suspense
        fallback={
          <p className={styles.loading} role="status">
            <Spinner size={16} />
            <span>Loading App settings…</span>
          </p>
        }
      >
        <AppSettingsBody prepareMode={prepareMode} prefetchMode={prefetchMode} />
      </Suspense>
    </Dialog>
  )
}
