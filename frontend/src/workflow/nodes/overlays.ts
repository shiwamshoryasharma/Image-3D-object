/**
 * UI-only facts the workflow catalog does not carry (UI_DESIGN.md §4.3.2): where a node's params come from, whether
 * it is a source, how it is bypassed, and a description for the palette. Everything else (title, category, ports,
 * availability, version, defaults) comes from `CatalogOut.node_types`.
 *
 * Extension point (§4.3.11): an approved future node needs one row here (`binds: 'none'` + `params`, optional
 * `migrate`/`contribute`); `compile`, `validate` and the shells stay unchanged.
 *
 * PURE (UI_DESIGN §1.5).
 */
import type { Binding, BypassRule, NodeTypeDef, ParamDef } from '../engine/types.ts'

export interface NodeOverlay {
  readonly binds: Binding
  readonly isSource: boolean
  readonly bypass: BypassRule
  readonly description: string
  readonly params?: readonly ParamDef[]
  readonly migrate?: NodeTypeDef['migrate']
  readonly contribute?: NodeTypeDef['contribute']
}

export const NODE_OVERLAYS: Readonly<Record<string, NodeOverlay>> = {
  input_image: {
    binds: 'references',
    isSource: true,
    bypass: null,
    description: 'Uses the reference images of the current set.',
  },
  prompt: {
    binds: 'prompt',
    isSource: true,
    bypass: null,
    description: 'Generates the input image from a text prompt.',
  },
  job_result: {
    binds: 'viewed_job',
    isSource: true,
    bypass: null,
    description: 'Starts from the finished model that is open in the viewer.',
  },
  segmentation: {
    binds: 'segmentation',
    isSource: false,
    bypass: 'drop',
    description: 'Finds the parts in the input image before the 3D step.',
  },
  image_to_3d: {
    binds: 'settings',
    isSource: false,
    bypass: null,
    description: 'Builds the textured 3D model from the input image.',
  },
  part_extraction: {
    binds: 'part_options',
    isSource: false,
    bypass: { from: 'mesh', to: 'parts' },
    description: 'Splits the model into separate parts and surface regions.',
  },
  export: {
    binds: 'export',
    isSource: false,
    bypass: null,
    description: 'Chooses which files are written for the result.',
  },
}

/** Overlay of a catalog type without a row (refine, unsupported and unknown future types). */
export const DEFAULT_NODE_OVERLAY: NodeOverlay = {
  binds: 'none',
  isSource: false,
  bypass: null,
  description: '',
}
