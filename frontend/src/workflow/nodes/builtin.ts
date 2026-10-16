/**
 * Built-in node catalog (UI_DESIGN.md §4.3.2): the DESIGN §4.7.2 node table in `CatalogOut.node_types` shape, used
 * for a legacy backend (`/features` 404) and until the server catalog has loaded. Only Input Image, Image-to-3D and
 * Export are available (they compile to a plain `POST /jobs`); every other type needs a newer backend. The refine
 * nodes stay locked (registry guard) and every hidden type keeps `hidden_by_default`.
 *
 * PURE (UI_DESIGN §1.5).
 */
import type { CatalogNodeTypeOut, CatalogPortIn } from '../../api/partAwareTypes.ts'

/** The catalog fields the registry reads (a full `CatalogNodeTypeOut` is assignable). */
export type CatalogNodeFields = Pick<
  CatalogNodeTypeOut,
  | 'type'
  | 'version'
  | 'category'
  | 'title'
  | 'inputs'
  | 'outputs'
  | 'defaults'
  | 'max_instances'
  | 'locked'
  | 'hidden_by_default'
  | 'available'
  | 'code'
  | 'reason'
>

export const LEGACY_NODE_CODE = 'legacy_backend'
export const LEGACY_NODE_REASON = 'Needs a newer Img3D backend.'

const IMAGES_IN: CatalogPortIn = { id: 'images', label: 'Image', accepts: ['image_set'], required: true, multiple: false }
const MESH_IN: CatalogPortIn = { id: 'mesh', label: 'Mesh', accepts: ['mesh'], required: true, multiple: false }
const PARTS_IN: CatalogPortIn = { id: 'parts', label: 'Parts', accepts: ['part_set'], required: true, multiple: false }
const IMAGES_OUT = { id: 'images', label: 'Image', type: 'image_set' } as const
const MESH_OUT = { id: 'mesh', label: 'Mesh', type: 'mesh' } as const
const PARTS_OUT = { id: 'parts', label: 'Parts', type: 'part_set' } as const

const AVAILABLE = { available: true, code: 'ok', reason: null, locked: false, hidden_by_default: false } as const
const LEGACY = {
  available: false,
  code: LEGACY_NODE_CODE,
  reason: LEGACY_NODE_REASON,
  locked: false,
  hidden_by_default: false,
} as const

function node(
  type: string,
  title: string,
  category: string,
  inputs: CatalogPortIn[],
  outputs: CatalogNodeFields['outputs'],
  state: Pick<CatalogNodeFields, 'available' | 'code' | 'reason' | 'locked' | 'hidden_by_default'>,
  defaults: Record<string, unknown> = {},
): CatalogNodeFields {
  return { type, version: 1, category, title, inputs, outputs, defaults, max_instances: 1, ...state }
}

const refine = (type: string, title: string): CatalogNodeFields =>
  node(type, title, 'refine', [PARTS_IN], [PARTS_OUT], { ...LEGACY, locked: true, hidden_by_default: true })

const unsupported = (type: string, title: string, input: CatalogPortIn): CatalogNodeFields =>
  node(type, title, 'parts', [input], [PARTS_OUT], { ...LEGACY, hidden_by_default: true })

/** DESIGN §4.7.2, in catalog order. */
export const BUILTIN_NODE_TYPES: readonly CatalogNodeFields[] = [
  node('input_image', 'Input Image', 'input', [], [IMAGES_OUT], AVAILABLE),
  node('prompt', 'Prompt', 'input', [], [IMAGES_OUT], LEGACY),
  node('job_result', 'Existing Result', 'input', [], [IMAGES_OUT, MESH_OUT], LEGACY),
  node('segmentation', 'Segmentation', 'analyze', [IMAGES_IN], [{ id: 'masks', label: 'Masks', type: 'segmentation' }], LEGACY, {
    profile: 'auto',
    subject: 'auto',
    classes: null,
    extra_terms: [],
    use_prompt_terms: true,
    head_pass: true,
    min_confidence: 0.35,
  }),
  node('image_to_3d', 'Image-to-3D', 'generate', [IMAGES_IN], [MESH_OUT], AVAILABLE),
  node(
    'part_extraction',
    'Part Extraction',
    'parts',
    [MESH_IN, { id: 'masks', label: 'Masks', accepts: ['segmentation'], required: false, multiple: false }],
    [PARTS_OUT],
    LEGACY,
  ),
  node(
    'export',
    'Export',
    'output',
    [{ id: 'asset', label: 'Asset', accepts: ['mesh', 'part_set'], required: true, multiple: false }],
    [],
    AVAILABLE,
    { per_part_glbs: false, previews: true },
  ),
  refine('part_refine', 'Part Refine'),
  refine('mesh_tune', 'Mesh Tune'),
  refine('eye_correction', 'Eye Correction'),
  refine('rig_correction', 'Rig Correction'),
  unsupported('segmentation_3d', '3D Segmentation', MESH_IN),
  unsupported('part_completion', 'Part Completion', PARTS_IN),
  unsupported('part_generation', 'Part Generation', MESH_IN),
]
