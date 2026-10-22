import { Settings2 } from 'lucide-react'

import { IconButton } from '../../components/common/Button'
import { FEATURE_META } from '../../lib/featureMeta.ts'
import { useNavigationStore } from '../../store/navigationStore'

/** The App settings gear of the Mobile / Tablet / Node chrome (same name and dialog as the Default header gear). */
export function AppSettingsButton({ className }: { className?: string }) {
  const open = useNavigationStore((s) => s.appSettingsOpen)
  const setAppSettingsOpen = useNavigationStore((s) => s.setAppSettingsOpen)
  return (
    <IconButton
      label={FEATURE_META.appSettings.title}
      icon={<Settings2 size={20} />}
      className={className}
      aria-haspopup="dialog"
      aria-expanded={open}
      onClick={() => setAppSettingsOpen(true, 'interface')}
    />
  )
}
