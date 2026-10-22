import { ArrowLeftRight } from 'lucide-react'

import { useFeaturesStatus } from '../../hooks/useFeatures'
import { useInputStore } from '../../store/inputStore'
import { Panel } from '../common/Panel'
import { InputSourceSwitch } from './InputSourceSwitch'

/**
 * Default-mode source card (UI_DESIGN §4.2 placements, P2/P22). Eager and tiny. Renders when `/features` is ready,
 * or while it loads / failed when the stored source is not `image` (UI11: the way back stays reachable); never on a
 * legacy backend, whatever is stored (UI1).
 */
export function InputSourceCard() {
  const status = useFeaturesStatus()
  const storedPrompt = useInputStore((s) => s.source !== 'image')

  if (status === 'legacy' || (status !== 'ready' && !storedPrompt)) return null
  return (
    <div data-ui-addition="input-source">
      <Panel id="input-source" title="Input" icon={<ArrowLeftRight size={16} />} subtitle="Reference images or a text prompt">
        <InputSourceSwitch />
      </Panel>
    </div>
  )
}
