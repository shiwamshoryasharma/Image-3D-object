import type { PartViewKind } from '../../lib/partsModel.ts'
import { PART_KIND_LABELS } from '../../lib/featureMeta.ts'
import { Tag, type TagTone } from '../common/Tag'

const TONE: Readonly<Record<PartViewKind, TagTone>> = Object.freeze({
  base: 'neutral',
  physical: 'accent',
  deferred: 'warning',
  region: 'info',
})

/** Part kind as a text badge (Base / Physical / Geometry later / Region): never colour alone (WCAG 1.4.1). */
export function PartKindBadge({ kind }: { kind: PartViewKind }) {
  return <Tag tone={TONE[kind]}>{PART_KIND_LABELS[kind]}</Tag>
}
