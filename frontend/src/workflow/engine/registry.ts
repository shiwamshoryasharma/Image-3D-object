/**
 * Node registry (UI_DESIGN.md §4.3.2): node type definitions built from the server's `CatalogOut` plus the UI-only
 * overlays, a built-in fallback for legacy backends, and the Add-node palette.
 *
 * Guard (judge graft): a `refine` node is accepted only when it is locked, so a tuning node can never become
 * runnable through a catalog or an import while refinement is not approved.
 *
 * PURE (UI_DESIGN §1.5).
 */
import type { CatalogOut, FeatureNodeOut } from '../../api/partAwareTypes.ts'
import { BUILTIN_NODE_TYPES } from '../nodes/builtin.ts'
import type { CatalogNodeFields } from '../nodes/builtin.ts'
import { DEFAULT_NODE_OVERLAY, NODE_OVERLAYS } from '../nodes/overlays.ts'
import type { NodeOverlay } from '../nodes/overlays.ts'
import { CATEGORY_META, NODE_CATEGORIES, isNodeCategory, isPortType } from './types.ts'
import type { BypassRule, InputPortDef, NodeCategory, NodeTypeDef, OutputPortDef } from './types.ts'

export interface PaletteEntry {
  readonly def: NodeTypeDef
  readonly enabled: boolean
  readonly reason: string | null
}

export interface PaletteGroup {
  readonly category: NodeCategory
  readonly title: string
  readonly entries: readonly PaletteEntry[]
}

export interface PaletteOptions {
  /** "Show unsupported": hidden types of the non-refine categories. */
  readonly showHidden: boolean
  /** `VITE_FEATURE_REFINEMENT_PREVIEW === 'true'`: the (locked) refine group. */
  readonly refinementPreview: boolean
  /** Node types already in the doc. */
  readonly present: ReadonlySet<string>
}

export interface NodeRegistry {
  get(type: string): NodeTypeDef | undefined
  list(): readonly NodeTypeDef[]
  palette(o: PaletteOptions): readonly PaletteGroup[]
}

export type RegistryErrorCode = 'duplicate_type' | 'refine_unlocked' | 'port_clash'

export class RegistryError extends Error {
  readonly code: RegistryErrorCode

  constructor(code: RegistryErrorCode, message: string) {
    super(message)
    this.name = 'RegistryError'
    this.code = code
  }
}

/** Palette reason of every locked entry (UI_DESIGN §4.3.11). */
export const REFINE_LOCKED_REASON = 'Paused until you approve refinement'

const UNAVAILABLE_REASON = 'Unavailable on this backend.'

function freezeDef(def: NodeTypeDef): NodeTypeDef {
  return Object.freeze({
    ...def,
    inputs: Object.freeze(def.inputs.map((port) => Object.freeze({ ...port, accepts: Object.freeze([...port.accepts]) }))),
    outputs: Object.freeze(def.outputs.map((port) => Object.freeze({ ...port }))),
    params: Object.freeze([...def.params]),
    defaults: Object.freeze({ ...def.defaults }),
  })
}

function hasDuplicateId(ports: readonly { id: string }[]): string | null {
  const seen = new Set<string>()
  for (const port of ports) {
    if (seen.has(port.id)) return port.id
    seen.add(port.id)
  }
  return null
}

function buildPalette(defs: readonly NodeTypeDef[], o: PaletteOptions): readonly PaletteGroup[] {
  const groups: PaletteGroup[] = []
  for (const category of NODE_CATEGORIES) {
    // The refine group appears only behind the preview flag, never through "Show unsupported".
    if (category === 'refine' && !o.refinementPreview) continue
    const entries: PaletteEntry[] = []
    for (const def of defs) {
      if (def.category !== category) continue
      if (category !== 'refine' && def.hiddenByDefault && !o.showHidden) continue
      if (def.locked) entries.push({ def, enabled: false, reason: REFINE_LOCKED_REASON })
      else if (o.present.has(def.type)) {
        entries.push({ def, enabled: false, reason: `Only one ${def.title} node is supported.` })
      } else if (!def.available) entries.push({ def, enabled: false, reason: def.reason ?? UNAVAILABLE_REASON })
      else entries.push({ def, enabled: true, reason: null })
    }
    if (entries.length > 0) groups.push({ category, title: CATEGORY_META[category].title, entries })
  }
  return groups
}

/**
 * Build a registry. Throws `RegistryError`:
 * - `duplicate_type` when two defs share a type;
 * - `port_clash` when two inputs (or two outputs) of one def share an id;
 * - `refine_unlocked` for a `refine` def without `locked: true`.
 */
export function createRegistry(defs: readonly NodeTypeDef[]): NodeRegistry {
  const byType = new Map<string, NodeTypeDef>()
  for (const def of defs) {
    if (byType.has(def.type)) throw new RegistryError('duplicate_type', `Node type "${def.type}" is defined twice.`)
    if (def.category === 'refine' && !def.locked) {
      throw new RegistryError('refine_unlocked', `Refinement node "${def.type}" must stay locked.`)
    }
    const clash = hasDuplicateId(def.inputs) ?? hasDuplicateId(def.outputs)
    if (clash !== null) throw new RegistryError('port_clash', `Node type "${def.type}" has two ports named "${clash}".`)
    byType.set(def.type, freezeDef(def))
  }
  const list = Object.freeze([...byType.values()])
  return Object.freeze({
    get: (type: string) => byType.get(type),
    list: () => list,
    palette: (o: PaletteOptions) => buildPalette(list, o),
  })
}

const positiveInt = (value: unknown, fallback: number): number =>
  typeof value === 'number' && Number.isInteger(value) && value >= 1 ? value : fallback

const isRecord = (value: unknown): value is Record<string, unknown> =>
  typeof value === 'object' && value !== null && !Array.isArray(value)

/** A one-in/one-out node of the same port type passes its input through when bypassed (e.g. locked refine nodes). */
function passThroughBypass(inputs: readonly InputPortDef[], outputs: readonly OutputPortDef[]): BypassRule {
  if (inputs.length !== 1 || outputs.length !== 1) return null
  return inputs[0].accepts.includes(outputs[0].type) ? { from: inputs[0].id, to: outputs[0].id } : null
}

/**
 * Map one catalog node type onto a `NodeTypeDef` (catalog facts + overlay row). Returns `null` for a type this UI
 * build cannot represent (unknown category or port type); such a type is simply not offered.
 */
export function defFromCatalogNode(
  n: CatalogNodeFields,
  overlays: Readonly<Record<string, NodeOverlay>> = NODE_OVERLAYS,
): NodeTypeDef | null {
  if (!isNodeCategory(n.category)) return null
  const outputs: OutputPortDef[] = []
  for (const port of n.outputs) {
    if (!isPortType(port.type)) return null
    outputs.push({ id: port.id, label: port.label, type: port.type })
  }
  const inputs: InputPortDef[] = []
  for (const port of n.inputs) {
    const accepts = port.accepts.filter(isPortType)
    if (accepts.length === 0) return null
    inputs.push({ id: port.id, label: port.label, accepts, required: port.required === true, multiple: port.multiple === true })
  }
  const row = Object.hasOwn(overlays, n.type) ? overlays[n.type] : null
  const overlay = row ?? DEFAULT_NODE_OVERLAY
  const available = n.available === true
  return {
    type: n.type,
    version: positiveInt(n.version, 1),
    category: n.category,
    title: n.title || n.type,
    description: overlay.description,
    inputs,
    outputs,
    maxInstances: positiveInt(n.max_instances, 1),
    isSource: row ? row.isSource : n.category === 'input',
    binds: overlay.binds,
    params: overlay.params ?? [],
    defaults: isRecord(n.defaults) ? n.defaults : {},
    bypass: row ? row.bypass : passThroughBypass(inputs, outputs),
    locked: n.locked === true,
    available,
    code: typeof n.code === 'string' && n.code ? n.code : available ? 'ok' : 'unavailable',
    reason: typeof n.reason === 'string' ? n.reason : null,
    hiddenByDefault: n.hidden_by_default === true,
    ...(overlay.migrate ? { migrate: overlay.migrate } : {}),
    ...(overlay.contribute ? { contribute: overlay.contribute } : {}),
  }
}

/** Registry from `GET /workflows/catalog` (throws `RegistryError` like `createRegistry`, e.g. an unlocked refine node). */
export function registryFromCatalog(
  c: CatalogOut,
  overlays: Readonly<Record<string, NodeOverlay>> = NODE_OVERLAYS,
): NodeRegistry {
  const defs: NodeTypeDef[] = []
  for (const n of Array.isArray(c.node_types) ? c.node_types : []) {
    const def = defFromCatalogNode(n, overlays)
    if (def) defs.push(def)
  }
  return createRegistry(defs)
}

/**
 * Apply the live `/features.nodes` states (availability, reason, hidden) to a registry, e.g. to the built-in
 * registry while the catalog loads. A node can become locked this way but never unlocked.
 */
export function withNodeStates(r: NodeRegistry, nodes: readonly FeatureNodeOut[]): NodeRegistry {
  const states = new Map(nodes.map((node) => [node.type, node]))
  return createRegistry(
    r.list().map((def) => {
      const state = states.get(def.type)
      if (!state) return def
      return {
        ...def,
        available: state.available === true,
        code: state.code,
        reason: state.reason ?? null,
        locked: def.locked || state.locked === true,
        hiddenByDefault: state.hidden_by_default === true,
      }
    }),
  )
}

/** Legacy backend / catalog-not-loaded fallback (DESIGN §4.7.2 table; only the `POST /jobs` nodes are available). */
export const BUILTIN_REGISTRY: NodeRegistry = createRegistry(
  BUILTIN_NODE_TYPES.flatMap((n) => {
    const def = defFromCatalogNode(n)
    return def ? [def] : []
  }),
)
