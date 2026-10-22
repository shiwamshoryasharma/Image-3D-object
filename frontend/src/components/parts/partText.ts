/**
 * Shared wording of the parts UI (UI_DESIGN §6.1 rule 7, §6.4, §6.5). Plain `.ts` so component files export only
 * components (react/only-export-components).
 */
import type { PartView } from '../../lib/partsModel.ts'

export const SHARED_LABEL_TEXT = 'Highlight unavailable: this part shares its surface label with another part'

/** "Hair (L)": label plus side. */
export function partName(part: Pick<PartView, 'label' | 'side'>): string {
  return `${part.label}${part.side === 'left' ? ' (L)' : part.side === 'right' ? ' (R)' : ''}`
}

/** "layered over Body" (out) / "Hair: attached to this part" (in). */
export function relationText(type: string, otherName: string, direction: 'out' | 'in'): string {
  const verb = type.replace(/_/g, ' ')
  return direction === 'out' ? `${verb} ${otherName}` : `${otherName}: ${verb} this part`
}

/** 0..1 ratio → "91 %"; `null` stays `null` (omitted by the caller). */
export function percentText(ratio: number | null): string | null {
  return ratio === null || !Number.isFinite(ratio) ? null : `${Math.round(ratio * 100)} %`
}
