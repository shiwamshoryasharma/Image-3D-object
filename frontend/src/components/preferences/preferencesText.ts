/**
 * Constants, tables and prop types of App settings (UI_DESIGN §3.8). Kept in a `.ts` file (React Compiler /
 * only-export-components rules); feature words come from `lib/featureMeta.ts`.
 */
import type { CommercialUse, ModelKind } from '../../api/partAwareTypes.ts'
import type { TagTone } from '../common/Tag'
import type { DensityPreference, LinkStyle, MotionPreference, NodeViewPreference, UiMode } from '../../lib/uiModes.ts'

/** Passed down from `app/GlobalOverlays` (components cannot import `app/`, UI_DESIGN §1.4). */
export interface ModeActions {
  /** Resolves once the mode's shell chunk is loaded; rejects when it cannot be loaded. */
  prepareMode: (mode: UiMode) => Promise<void>
  /** Focus/hover prefetch of a mode card. */
  prefetchMode: (mode: UiMode) => void
}

export const SECTION_DOM_IDS = Object.freeze({
  interface: 'app-settings-interface',
  node: 'app-settings-node',
  features: 'app-settings-features',
  about: 'app-settings-about',
} as const)

export const DENSITY_OPTIONS: readonly { value: DensityPreference; label: string }[] = Object.freeze([
  { value: 'auto', label: 'Auto' },
  { value: 'comfortable', label: 'Comfortable' },
  { value: 'touch', label: 'Touch' },
])

export const MOTION_OPTIONS: readonly { value: MotionPreference; label: string }[] = Object.freeze([
  { value: 'system', label: 'Follow system' },
  { value: 'reduce', label: 'Reduce motion' },
])

export const NODE_VIEW_OPTIONS: readonly { value: NodeViewPreference; label: string }[] = Object.freeze([
  { value: 'auto', label: 'Auto' },
  { value: 'canvas', label: 'Canvas' },
  { value: 'list', label: 'List' },
])

export const LINK_STYLE_OPTIONS: readonly { value: LinkStyle; label: string }[] = Object.freeze([
  { value: 'bezier', label: 'Bezier' },
  { value: 'smoothstep', label: 'Smooth step' },
  { value: 'straight', label: 'Straight' },
])

/** Registry groups in display order. */
export const MODEL_KIND_ORDER: readonly ModelKind[] = Object.freeze([
  'text_to_image',
  'segmentation',
  'part_extraction',
  'segmentation_3d',
  'completion',
  'part_generation',
])

export const MODEL_KIND_LABELS: Readonly<Record<ModelKind, string>> = Object.freeze({
  text_to_image: 'Prompt to image',
  segmentation: 'Image segmentation',
  part_extraction: 'Part extraction',
  segmentation_3d: '3D segmentation',
  completion: 'Shape completion',
  part_generation: 'Part generation',
})

/** Kinds that support "Warm up" (DESIGN §6.1: T2I and segmentation models). */
export const WARM_UP_KINDS: ReadonlySet<ModelKind> = Object.freeze(new Set<ModelKind>(['text_to_image', 'segmentation']))

export const LICENSE_TONES: Readonly<Record<CommercialUse, TagTone>> = Object.freeze({
  allowed: 'success',
  allowed_with_conditions: 'info',
  revenue_capped: 'warning',
  non_commercial: 'warning',
  research_only: 'danger',
  unknown: 'neutral',
})

export const IS_MAC = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.userAgent)

const MOD = IS_MAC ? '⌘' : 'Ctrl'

export const KEYBOARD_SHORTCUTS: readonly { keys: string; action: string }[] = Object.freeze([
  { keys: `${MOD} + Enter`, action: 'Generate with the current inputs' },
  { keys: `${MOD} + ,`, action: 'Open App settings' },
  { keys: '?', action: 'Show these shortcuts' },
  { keys: 'Escape', action: 'Close a dialog, sheet or the history drawer' },
])
