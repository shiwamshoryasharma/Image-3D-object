/**
 * Node-engine types (UI_DESIGN.md §4.3.1). The engine is a pure TypeScript model of the Node UI graph: registry,
 * graph operations, validation, projection onto the shared stores, compilation to the server graph, run-status
 * projection and persistence. React Flow is only an adapter on top of it.
 *
 * Built-in nodes keep no parameters in the graph: they bind to the shared stores (`binds`), so every UI mode edits
 * the same form. Only future nodes with `binds: 'none'` keep `values` in the doc. Node ids are `n_<type>` (unique
 * because every v1 type has `max_instances = 1`).
 *
 * PURE (UI_DESIGN §1.5): relative `.ts` value imports only; no react/zustand/axios, no browser globals.
 */
import type { GraphIssueCode } from '../../api/partAwareTypes.ts'

/** = `CatalogOut.port_types` ids. */
export const PORT_TYPES = ['image_set', 'segmentation', 'mesh', 'part_set'] as const
export type PortType = (typeof PORT_TYPES)[number]

export const NODE_CATEGORIES = ['input', 'analyze', 'generate', 'parts', 'output', 'refine'] as const
export type NodeCategory = (typeof NODE_CATEGORIES)[number]

export type NodeMode = 'active' | 'bypass'

/** Where a built-in node's params come from at compile time. */
export type Binding =
  | 'references'
  | 'prompt'
  | 'viewed_job'
  | 'segmentation'
  | 'settings'
  | 'part_options'
  | 'export'
  | 'none'

export interface InputPortDef {
  readonly id: string
  readonly label: string
  readonly accepts: readonly PortType[]
  readonly required: boolean
  readonly multiple: boolean
}

export interface OutputPortDef {
  readonly id: string
  readonly label: string
  readonly type: PortType
}

/** Generic inspector field for future (`binds: 'none'`) nodes. */
export type ParamDef =
  | { readonly kind: 'boolean'; readonly key: string; readonly label: string }
  | {
      readonly kind: 'number'
      readonly key: string
      readonly label: string
      readonly min: number
      readonly max: number
      readonly step: number
    }
  | {
      readonly kind: 'enum'
      readonly key: string
      readonly label: string
      readonly options: readonly { value: string; label: string }[]
    }
  | { readonly kind: 'text'; readonly key: string; readonly label: string; readonly maxLength: number }

/** `part_extraction` bypasses mesh → parts; `segmentation` is dropped; `null` = cannot be bypassed. */
export type BypassRule = { readonly from: string; readonly to: string } | 'drop' | null

export interface NodeTypeDef {
  /** Server node type id (`image_to_3d`); future UI-only types use `ui.<name>`. */
  readonly type: string
  readonly version: number
  readonly category: NodeCategory
  readonly title: string
  readonly description: string
  readonly inputs: readonly InputPortDef[]
  readonly outputs: readonly OutputPortDef[]
  /** 1 for every v1 type (server rule). */
  readonly maxInstances: number
  /** `input_image` | `prompt` | `job_result`. */
  readonly isSource: boolean
  readonly binds: Binding
  readonly params: readonly ParamDef[]
  readonly defaults: Readonly<Record<string, unknown>>
  readonly bypass: BypassRule
  readonly locked: boolean
  readonly available: boolean
  readonly code: string
  readonly reason: string | null
  readonly hiddenByDefault: boolean
  readonly migrate?: (values: Readonly<Record<string, unknown>>, fromVersion: number) => Record<string, unknown>
  /** Future nodes only: the server params of a `binds: 'none'` node. */
  readonly contribute?: (values: Readonly<Record<string, unknown>>) => Record<string, unknown>
}

export interface GraphNode {
  readonly id: string
  readonly type: string
  readonly version: number
  readonly mode: NodeMode
  /** Only for `binds: 'none'` nodes. */
  readonly values?: Readonly<Record<string, unknown>>
}

export interface PortRef {
  readonly node: string
  readonly port: string
}

/** Key = `edgeKey(e)` = `'a.p->b.q'`. */
export interface GraphEdge {
  readonly from: PortRef
  readonly to: PortRef
}

export interface Graph {
  readonly nodes: readonly GraphNode[]
  readonly edges: readonly GraphEdge[]
}

export interface Point {
  readonly x: number
  readonly y: number
}

export interface Viewport {
  readonly x: number
  readonly y: number
  readonly zoom: number
}

export interface Layout {
  readonly positions: Readonly<Record<string, Point>>
  readonly collapsed: readonly string[]
  readonly viewport: Viewport
}

export interface WorkflowDoc {
  readonly schema: 'img3d.ui_workflow'
  readonly version: 1
  readonly revision: number
  readonly graph: Graph
  readonly layout: Layout
}

/** Client-only codes (`ui_*`) are warnings. */
export type IssueCode = GraphIssueCode | 'ui_parked_source' | 'ui_unreachable_node'

export interface GraphIssue {
  readonly severity: 'error' | 'warning'
  readonly code: IssueCode
  readonly message: string
  readonly nodeId: string | null
  readonly port: string | null
  readonly edgeIndex: number | null
  readonly origin: 'client' | 'server'
}

export interface PipelineConfig {
  readonly source: 'image' | 'prompt' | 'job'
  readonly segmentation: boolean
  readonly partExtraction: boolean
}

export type NodeRunState =
  | 'idle'
  | 'queued'
  | 'running'
  | 'done'
  | 'warning'
  | 'failed'
  | 'skipped'
  | 'cancelled'
  | 'bypassed'
  | 'locked'
  | 'unavailable'

export interface NodeStatusView {
  readonly state: NodeRunState
  readonly percent: number | null
  readonly message: string | null
  readonly thumbnailUrl: string | null
}

// ------------------------------------------------------------------------------------------------
// Validation context
// ------------------------------------------------------------------------------------------------

/**
 * The part of `lib/featureFlags.FeaturesSnapshot` (UI_DESIGN §2.5) the engine reads. Declared structurally so the
 * engine does not depend on the store layer; a `FeaturesSnapshot` is assignable to it.
 */
export interface EngineFeatures {
  readonly status: 'loading' | 'ready' | 'legacy' | 'error'
  readonly flags: { readonly workflows: boolean }
  readonly workflow: { readonly maxNodes: number; readonly maxEdges: number; readonly graphMaxBytes: number }
}

/** Facts about the values the built-in nodes bind to (gathered from the shared stores by the Node shell). */
export interface BoundFacts {
  readonly imageCount: number
  readonly referenceSetId: string | null
  readonly promptLength: number
  readonly promptMax: number
  readonly viewedJobCompleted: boolean
}

export interface ValidationContext {
  readonly features: EngineFeatures
  readonly bound: BoundFacts
}

/** The two persisted form fields `configFromStores` reads (`PartOptionsForm` of UI_DESIGN §3.3 is assignable). */
export interface PartOptionsFlags {
  readonly enabled: boolean
  readonly previewSegmentation: boolean
}

/** `InputSource` of UI_DESIGN §3.3. */
export type EngineInputSource = 'image' | 'prompt'

// ------------------------------------------------------------------------------------------------
// Constants
// ------------------------------------------------------------------------------------------------

/** Server limits (DESIGN §4.7.1), used until `/features` or the catalog reports its own. */
export const GRAPH_LIMITS_FALLBACK = { maxNodes: 32, maxEdges: 64, graphMaxBytes: 65536 } as const

/** Server node id rule (DESIGN §4.7.1). */
export const NODE_ID_PATTERN = /^[a-z][a-z0-9_]{0,31}$/

export const WORKFLOW_DOC_SCHEMA = 'img3d.ui_workflow'
export const WORKFLOW_DOC_VERSION = 1

/** Port glyph, label and colour token (UI_DESIGN §4.3.3: glyph + label, never colour alone). */
export const PORT_META: Readonly<Record<PortType, { readonly label: string; readonly glyph: string; readonly token: string }>> = {
  image_set: { label: 'Image', glyph: '●', token: '--port-image' },
  segmentation: { label: 'Masks', glyph: '■', token: '--port-masks' },
  mesh: { label: 'Mesh', glyph: '▲', token: '--port-mesh' },
  part_set: { label: 'Parts', glyph: '⬢', token: '--port-parts' },
}

/** Palette group titles and category stripe tokens, in `NODE_CATEGORIES` order. */
export const CATEGORY_META: Readonly<Record<NodeCategory, { readonly title: string; readonly token: string }>> = {
  input: { title: 'Inputs', token: '--cat-input' },
  analyze: { title: 'Analyze', token: '--cat-analyze' },
  generate: { title: 'Generate', token: '--cat-generate' },
  parts: { title: 'Parts', token: '--cat-parts' },
  output: { title: 'Output', token: '--cat-output' },
  refine: { title: 'Refinement (paused)', token: '--cat-refine' },
}

export function isPortType(value: unknown): value is PortType {
  return typeof value === 'string' && (PORT_TYPES as readonly string[]).includes(value)
}

export function isNodeCategory(value: unknown): value is NodeCategory {
  return typeof value === 'string' && (NODE_CATEGORIES as readonly string[]).includes(value)
}
