import { SINGLE_SLOTS, type ReferenceSlot, type SingleSlot } from '../api/types'

export interface SlotMeta {
  label: string
  short: string
  hint: string
}

export const SLOT_META: Record<ReferenceSlot, SlotMeta> = {
  front: { label: 'Front', short: 'Front', hint: 'Full body / object, facing the camera' },
  back: { label: 'Back', short: 'Back', hint: 'Seen from behind' },
  left: { label: 'Left', short: 'Left', hint: 'Left side profile' },
  right: { label: 'Right', short: 'Right', hint: 'Right side profile' },
  face_front: { label: 'Face Front', short: 'Face F', hint: 'Close-up of the face, front' },
  face_side: { label: 'Face Side', short: 'Face S', hint: 'Close-up of the face, profile' },
  detail: { label: 'Detail', short: 'Detail', hint: 'Extra close-ups (materials, accessories…)' },
}

export function isSingleSlot(slot: ReferenceSlot): slot is SingleSlot {
  return (SINGLE_SLOTS as readonly string[]).includes(slot)
}

export const MIME_EXTENSIONS: Record<string, string[]> = {
  'image/png': ['.png'],
  'image/jpeg': ['.jpg', '.jpeg'],
  'image/webp': ['.webp'],
}

/** `accept` attribute for file inputs from the allowed MIME types. */
export function acceptAttribute(mimeTypes: readonly string[]): string {
  return [...mimeTypes, ...mimeTypes.flatMap((type) => MIME_EXTENSIONS[type] ?? [])].join(',')
}

/** Human list of accepted formats, e.g. "PNG, JPEG, WEBP". */
export function formatList(mimeTypes: readonly string[]): string {
  return mimeTypes.map((type) => type.replace(/^image\//, '').toUpperCase()).join(', ')
}
