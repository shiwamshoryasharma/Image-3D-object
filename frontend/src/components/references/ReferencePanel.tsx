import { FolderPlus, Images } from 'lucide-react'

import { SLOT_META } from '../../lib/slots'
import { useReferenceStore } from '../../store/referenceStore'
import { useSystemStore } from '../../store/systemStore'
import { Button } from '../common/Button'
import { Panel } from '../common/Panel'
import { ReferenceContent } from './ReferenceContent'

/**
 * Reference set editor card: `Panel` + `ReferenceContent` (UI_DESIGN §1.3, §4.1 rule 2). Name, path and DOM are
 * today's; the body lives in `ReferenceContent` so sheets and side panels reuse it.
 */
export function ReferencePanel({ active = true }: { active?: boolean }) {
  const referenceSet = useReferenceStore((s) => s.set)
  const creatingSet = useReferenceStore((s) => s.creatingSet)
  const newSet = useReferenceStore((s) => s.newSet)
  const reachable = useSystemStore((s) => s.reachable)

  const disabled = !referenceSet || reachable === false
  const primary = referenceSet?.images.find((image) => image.is_primary) ?? null
  const imageCount = referenceSet?.images.length ?? 0

  const subtitle = referenceSet
    ? `${imageCount} image${imageCount === 1 ? '' : 's'}${primary ? ` · primary: ${SLOT_META[primary.slot].label}` : ''}`
    : 'Reference set'

  return (
    <Panel
      id="references"
      title="References"
      icon={<Images size={16} />}
      subtitle={subtitle}
      actions={
        <Button
          size="sm"
          variant="ghost"
          icon={<FolderPlus size={14} />}
          loading={creatingSet}
          disabled={disabled && reachable === false}
          onClick={() => void newSet()}
          title="Start a new, empty reference set"
        >
          New set
        </Button>
      }
    >
      <ReferenceContent active={active} />
    </Panel>
  )
}
