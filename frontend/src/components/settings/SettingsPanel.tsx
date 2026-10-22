import { RotateCcw, SlidersHorizontal } from 'lucide-react'

import { useSettingsStore } from '../../store/settingsStore'
import { selectCapabilities, useSystemStore } from '../../store/systemStore'
import { Button } from '../common/Button'
import { Panel } from '../common/Panel'
import { SettingsContent } from './SettingsContent'

/**
 * Generation settings card (persisted): `Panel` + `SettingsContent` (UI_DESIGN §1.3, §4.1 rule 2). Name, path and
 * DOM are today's; part options are appended inside the content only once `/features` allows them (§4.2).
 */
export function SettingsPanel() {
  const resetAll = useSettingsStore((s) => s.resetAll)
  const capabilities = useSystemStore(selectCapabilities)
  const defaults = useSystemStore((s) => s.system?.defaults ?? null)

  return (
    <Panel
      id="settings"
      title="Settings"
      icon={<SlidersHorizontal size={16} />}
      subtitle="Saved automatically"
      actions={
        <Button
          size="sm"
          variant="ghost"
          icon={<RotateCcw size={14} />}
          onClick={() => resetAll(defaults, capabilities)}
          title="Reset all settings to the server defaults"
        >
          Reset
        </Button>
      }
    >
      <SettingsContent />
    </Panel>
  )
}
