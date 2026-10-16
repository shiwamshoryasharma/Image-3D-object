/**
 * Compile a Node doc to the server's `WorkflowRequest {graph, layout}` (UI_DESIGN.md §4.3.7, DESIGN §4.7.1–§4.7.2).
 *
 * 1. `validateGraph`; any error → `{ok: false}`.
 * 2. Run set (plan.ts): the active source connected to the run, every active node on its path to the end, and
 *    Segmentation only when its masks feed an active Part Extraction in the run. Parked sources, unreachable nodes
 *    and a Segmentation node feeding nothing are left out (warnings), so the server never sees a graph it would
 *    reject with `unsupported_topology`.
 * 3. Bypassed nodes are removed (`'drop'`) or rewired (`{from, to}`, e.g. `n_image_to_3d.mesh → n_export.asset`).
 * 4. Params: `bound[type]` for built-ins (the fixed DESIGN v1 key lists of the §2.8 builders; no catalog filtering);
 *    `def.contribute(node.values)` for future `binds: 'none'` nodes (none in v1).
 * 5. Nodes in `topoOrder`; edges by (topo index of `from.node`, `from.port`, topo index of `to.node`, `to.port`), so
 *    the bytes are deterministic (golden fixture G7).
 * 6. `mode = 'job'` iff the run types are exactly {input_image, image_to_3d, export} (the client then posts its own
 *    `POST /jobs` body, G1).
 * 7. `layout = {positions: run nodes rounded to integers, viewport}`; graph + layout ≤ `graph_max_bytes`.
 *
 * PURE (UI_DESIGN §1.5).
 */
import type {
  ImageGenerationRequest,
  PartAnalysisOptions,
  SegmentationOptions,
  WorkflowGraph,
  WorkflowGraphEdge,
  WorkflowGraphNode,
  WorkflowRequest,
} from '../../api/partAwareTypes.ts'
import type { GenerationSettings } from '../../api/types.ts'
import { topoOrder } from './graph.ts'
import type { EffectiveNode } from './plan.ts'
import type { NodeRegistry } from './registry.ts'
import type { GraphIssue, PipelineConfig, Point, ValidationContext, WorkflowDoc } from './types.ts'
import { ISSUE_MESSAGES, validateWithAnalysis } from './validate.ts'

/** Values the built-in nodes bind to, gathered by `shells/node/bindings.ts` from the stores with the §2.8 builders. */
export interface BoundParams {
  input_image: { reference_set_id: string | null; primary_image_id: string | null }
  /** `buildGeneration(prompt, 1, ctx)`. */
  prompt: ImageGenerationRequest | null
  /** The viewed job when completed. */
  job_result: { job_id: string | null }
  /** `buildSegmentationOptions(form) ?? {}`. */
  segmentation: SegmentationOptions
  /** `toRequestSettings(form, referenceMode)`, unfiltered (UI_DESIGN §2.6). */
  image_to_3d: GenerationSettings
  /** `buildPartAnalysisOptions(form, ctx)` minus `export` (stripped here as well). */
  part_extraction: Omit<PartAnalysisOptions, 'export' | 'segmentation_id'>
  /** `buildExportParams(form)`; the server compiles it to `parts.export`. */
  export: { per_part_glbs: boolean }
}

export type CompileOutput =
  | {
      readonly ok: true
      readonly mode: 'job' | 'workflow'
      /** `{graph, layout}` only. */
      readonly request: WorkflowRequest
      readonly config: PipelineConfig
      /** The compiled node ids in request order (the session `runIndex`, UI_DESIGN §4.3.9). */
      readonly nodeIds: readonly string[]
      readonly warnings: readonly GraphIssue[]
    }
  | { readonly ok: false; readonly issues: readonly GraphIssue[] }

export const WORKFLOW_GRAPH_SCHEMA = 'img3d.workflow_graph'
export const WORKFLOW_GRAPH_VERSION = 1

function compileIssue(code: GraphIssue['code'], message: string, nodeId: string | null = null): GraphIssue {
  return { severity: 'error', code, message, nodeId, port: null, edgeIndex: null, origin: 'client' }
}

/** UTF-8 byte length of a string (no `TextEncoder`: pure modules use no host globals). */
export function utf8Length(text: string): number {
  let bytes = 0
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i)
    if (code < 0x80) bytes += 1
    else if (code < 0x800) bytes += 2
    else if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length) {
      bytes += 4
      i += 1
    } else bytes += 3
  }
  return bytes
}

type ParamsResult = { readonly params: Record<string, unknown> } | { readonly issue: GraphIssue }

function paramsFor({ node, def }: EffectiveNode, bound: BoundParams): ParamsResult {
  switch (def.binds) {
    case 'references': {
      const { reference_set_id, primary_image_id } = bound.input_image
      if (reference_set_id === null) return { issue: compileIssue('invalid_param', ISSUE_MESSAGES.noImages, node.id) }
      return { params: { reference_set_id, primary_image_id } }
    }
    case 'prompt':
      if (bound.prompt === null) return { issue: compileIssue('invalid_param', ISSUE_MESSAGES.emptyPrompt, node.id) }
      return { params: { ...bound.prompt } }
    case 'viewed_job':
      if (bound.job_result.job_id === null) {
        return { issue: compileIssue('invalid_param', ISSUE_MESSAGES.jobNotCompleted, node.id) }
      }
      return { params: { job_id: bound.job_result.job_id } }
    case 'segmentation':
      return { params: { ...bound.segmentation } }
    case 'settings':
      return { params: { ...bound.image_to_3d } }
    case 'part_options': {
      // Workflows never send `parts.segmentation_id`; export options belong to the Export node (DESIGN §4.7.1).
      const params: Record<string, unknown> = { ...bound.part_extraction }
      delete params.export
      delete params.segmentation_id
      return { params }
    }
    case 'export':
      return { params: { per_part_glbs: bound.export.per_part_glbs } }
    case 'none': {
      const values = node.values ?? {}
      return { params: def.contribute ? def.contribute(values) : { ...values } }
    }
  }
}

export function compileDoc(doc: WorkflowDoc, r: NodeRegistry, bound: BoundParams, ctx: ValidationContext): CompileOutput {
  const { issues, analysis } = validateWithAnalysis(doc, r, ctx)
  if (issues.some((i) => i.severity === 'error')) return { ok: false, issues }
  const warnings = issues.filter((i) => i.severity === 'warning')
  if (analysis.config === null || analysis.mode === null) {
    // Unreachable when validation passed (no source or no producer is always an error); kept as a guard.
    return { ok: false, issues: [...issues, compileIssue('no_source', ISSUE_MESSAGES.noSource)] }
  }

  const runEdges = analysis.edges.filter((e) => analysis.runSet.has(e.from.node) && analysis.runSet.has(e.to.node))
  const order = topoOrder({
    nodes: doc.graph.nodes.filter((node) => analysis.runSet.has(node.id)),
    edges: runEdges,
  })
  const rank = new Map(order.map((id, index) => [id, index]))
  const byRank = (id: string): number => rank.get(id) ?? order.length

  const nodes: WorkflowGraphNode[] = []
  for (const id of order) {
    const entry = analysis.nodes.get(id) as EffectiveNode
    const result = paramsFor(entry, bound)
    if ('issue' in result) return { ok: false, issues: [...issues, result.issue] }
    nodes.push({ id, type: entry.def.type, params: result.params })
  }
  const edges: WorkflowGraphEdge[] = [...runEdges]
    .sort(
      (a, b) =>
        byRank(a.from.node) - byRank(b.from.node) ||
        (a.from.port < b.from.port ? -1 : a.from.port > b.from.port ? 1 : 0) ||
        byRank(a.to.node) - byRank(b.to.node) ||
        (a.to.port < b.to.port ? -1 : a.to.port > b.to.port ? 1 : 0),
    )
    .map((e) => ({ from: { node: e.from.node, port: e.from.port }, to: { node: e.to.node, port: e.to.port } }))

  const positions: Record<string, Point> = {}
  for (const id of order) {
    const point = doc.layout.positions[id]
    positions[id] = point ? { x: Math.round(point.x), y: Math.round(point.y) } : { x: 0, y: 0 }
  }
  const { x, y, zoom } = doc.layout.viewport
  const graph: WorkflowGraph = { schema: WORKFLOW_GRAPH_SCHEMA, version: WORKFLOW_GRAPH_VERSION, nodes, edges }
  const request: WorkflowRequest = { graph, layout: { positions, viewport: { x, y, zoom } } }

  const maxBytes = ctx.features.workflow.graphMaxBytes
  if (utf8Length(JSON.stringify(request)) > maxBytes) {
    const tooLarge = compileIssue('graph_too_large', `The workflow is too large (max ${maxBytes} bytes).`)
    return { ok: false, issues: [...issues, tooLarge] }
  }
  return { ok: true, mode: analysis.mode, request, config: analysis.config, nodeIds: order, warnings }
}
