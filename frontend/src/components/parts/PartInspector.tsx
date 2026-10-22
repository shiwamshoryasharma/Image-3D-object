import { Download, Image as ImageIcon, Info } from 'lucide-react'
import { useId } from 'react'

import { downloadArtifact } from '../../actions/downloads'
import { relationsOf, type PartSetView, type PartView } from '../../lib/partsModel.ts'
import { Button } from '../common/Button'
import { PartKindBadge } from './PartKindBadge'
import styles from './Parts.module.css'
import { partName, relationText } from './partText'

const SIDE_TEXT = { left: 'Left', right: 'Right', center: 'Centre' } as const

/**
 * Read-only part details (UI_DESIGN §6.5): category, kind, side, faces, confidence, hierarchy, relations, geometry
 * facts, expectations and the (locked) refinement status. Downloads: part GLB (physical) and mask PNG. No refine or
 * tune action exists.
 */
export function PartInspector({ set, part }: { set: PartSetView; part: PartView }) {
  const headingId = useId()
  const nameOf = (id: string | null): string | null => {
    if (id === null) return null
    const other = set.byId[id]
    return other ? partName(other) : id
  }
  const relations = relationsOf(set, part.id).map((r) => relationText(r.type, nameOf(r.otherId) ?? r.otherId, r.direction))
  const geometry: string[] = []
  if (part.geometry) {
    geometry.push(
      part.geometry.status === 'open_surface'
        ? `Open surface · ${part.geometry.openBoundaryLoops} boundary loop${part.geometry.openBoundaryLoops === 1 ? '' : 's'}`
        : part.geometry.watertight
          ? 'Closed surface'
          : part.geometry.status.replace(/_/g, ' '),
    )
  }
  if (part.geometryNote) geometry.push(part.geometryNote)
  if (part.kind === 'base' && part.floaterCount > 0) {
    geometry.push(`${part.floaterCount} small loose piece${part.floaterCount === 1 ? '' : 's'} kept on the body`)
  }

  const facts: Array<[string, string]> = [['Category', part.category]]
  if (part.side) facts.push(['Side', SIDE_TEXT[part.side]])
  facts.push(['Faces', part.faces.toLocaleString()])
  if (part.confidence !== null) facts.push(['Confidence', `${Math.round(part.confidence * 100)} %`])
  const parent = nameOf(part.parentId)
  if (parent) facts.push(['Parent', parent])
  const attached = nameOf(part.attachToId)
  if (attached) facts.push(['Attached to', attached])
  const host = nameOf(part.hostId)
  if (host && part.kind === 'region') facts.push(['Host', `Region on ${host}`])
  const symmetric = nameOf(part.symmetricWithId)
  if (symmetric) facts.push(['Symmetric with', symmetric])
  if (relations.length > 0) facts.push(['Relations', relations.join('; ')])
  if (geometry.length > 0) facts.push(['Geometry', geometry.join(' · ')])
  if (part.expected) facts.push(['Prompt', 'Asked for in the prompt'])
  facts.push([
    'Refinement',
    part.refinement.locked ? 'Untouched · refinement is paused until you approve it' : part.refinement.status.replace(/_/g, ' '),
  ])

  return (
    <section className={styles.inspector} aria-labelledby={headingId}>
      <h3 id={headingId} className={styles.inspectorTitle}>
        {partName(part)} <PartKindBadge kind={part.kind} />
      </h3>
      {part.groupNote && <p className={styles.note}>{part.groupNote}</p>}
      {!set.hasManifest && (
        <p className={styles.note}>
          <Info size={12} aria-hidden /> Details unavailable
        </p>
      )}
      <dl className={styles.facts}>
        {facts.map(([label, value]) => (
          <div key={label} style={{ display: 'contents' }}>
            <dt>{label}</dt>
            <dd>{value}</dd>
          </div>
        ))}
      </dl>
      {(part.downloadUrl || part.maskUrl) && (
        <div className={styles.actions}>
          {part.downloadUrl && (
            <Button
              size="sm"
              variant="secondary"
              icon={<Download size={14} />}
              onClick={() =>
                void downloadArtifact({ kind: 'part_glb', url: part.downloadUrl ?? '', fallbackName: `${part.id}.glb`, sizeHint: null })
              }
            >
              Part GLB
            </Button>
          )}
          {part.maskUrl && (
            <Button
              size="sm"
              variant="ghost"
              icon={<ImageIcon size={14} />}
              onClick={() =>
                void downloadArtifact({ kind: 'mask_png', url: part.maskUrl ?? '', fallbackName: `${part.id}_mask.png`, sizeHint: null })
              }
            >
              Mask PNG
            </Button>
          )}
        </div>
      )}
    </section>
  )
}
