/**
 * Client-side validation (UI_DESIGN.md §4.3.5). The rules emit the **server** issue codes (DESIGN §4.7.2), so client
 * and server problems read the same; `ui_parked_source` / `ui_unreachable_node` are client-only warnings for nodes
 * the compiler leaves out of the run (they are never sent to the server).
 *
 * Issue order (the first error is what `pipelineBlock` shows): graph limits, unknown types, extra instances, link
 * problems (doc order), loops, locked nodes, unavailable nodes, topology (`unsupported_topology`,
 * `multiple_sources`, `missing_required_input`, `no_source`), bound values (`invalid_param`), `feature_disabled`;
 * then the warnings.
 *
 * Scope rules that keep a parked or skipped node from blocking a Run:
 * - `capability_unavailable` and `invalid_param` only for nodes in the run set (a parked Prompt node on a backend
 *   without text-to-image is just a parked source);
 * - `missing_required_input` for the active nodes on the way to the end of the run (and an Export node with nothing
 *   connected); an active node outside it is `ui_unreachable_node` (skipped);
 * - `node_locked` for every active locked node, wherever it is (it can only become active through an import).
 *
 * PURE (UI_DESIGN §1.5).
 */
import type { GraphIssueOut } from '../../api/partAwareTypes.ts'
import { topoOrder } from './graph.ts'
import { analyzeRun } from './plan.ts'
import type { RunAnalysis } from './plan.ts'
import type { NodeRegistry } from './registry.ts'
import type { GraphIssue, GraphNode, IssueCode, NodeTypeDef, ValidationContext, WorkflowDoc } from './types.ts'

/**
 * Errors that make the topology meaningless for the store projection (`deriveConfig` → `config: null`). The other
 * errors (`graph_too_large`, `capability_unavailable`, `invalid_param`, `feature_disabled`) block a Run but keep the
 * projection, so Default/Mobile/Tablet still follow the graph.
 */
export const STRUCTURAL_CODES: ReadonlySet<IssueCode> = new Set<IssueCode>([
  'unknown_node_type',
  'too_many_instances',
  'unknown_port',
  'type_mismatch',
  'port_already_connected',
  'cycle',
  'node_locked',
  'unsupported_topology',
  'multiple_sources',
  'missing_required_input',
  'no_source',
])

/** Messages that are not built from a node title (UI_DESIGN §4.3.5 table). */
export const ISSUE_MESSAGES = {
  noSource: 'Connect an Input Image or Prompt node.',
  multipleSources: 'Use one source for the whole run.',
  featureDisabled: 'Workflows are turned off on the server.',
  noImages: 'Add at least one reference image.',
  emptyPrompt: 'Describe what to generate.',
  jobNotCompleted: 'Open a finished model first.',
  noSink: 'No Export node: the result is still kept.',
  segmentationSkipped: 'Segmentation is skipped: connect its Masks to an active Part Extraction.',
} as const

export interface ValidationResult {
  readonly issues: readonly GraphIssue[]
  readonly analysis: RunAnalysis
}

function issue(
  severity: GraphIssue['severity'],
  code: IssueCode,
  message: string,
  nodeId: string | null = null,
  port: string | null = null,
  edgeIndex: number | null = null,
): GraphIssue {
  return { severity, code, message, nodeId, port, edgeIndex, origin: 'client' }
}

export const tooLargeMessage = (maxNodes: number, maxEdges: number): string =>
  `The workflow is too large (max ${maxNodes} nodes, ${maxEdges} links).`

export const promptTooLongMessage = (max: number): string => `The prompt is too long (max ${max} characters).`

export const instancesMessage = (title: string, max: number): string =>
  max === 1 ? `Only one ${title} node is supported.` : `At most ${max} ${title} nodes are supported.`

/** Issues that depend only on the doc and the registry (no bound values, no `/features` limits or flags). */
function registryIssues(doc: WorkflowDoc, r: NodeRegistry, analysis: RunAnalysis): { errors: GraphIssue[]; warnings: GraphIssue[] } {
  const errors: GraphIssue[] = []
  const warnings: GraphIssue[] = []
  const byId = new Map<string, GraphNode>()
  for (const node of doc.graph.nodes) if (!byId.has(node.id)) byId.set(node.id, node)

  // Unknown node types: an error while active, a warning once bypassed (the import default; it is ignored).
  for (const node of doc.graph.nodes) {
    if (r.get(node.type)) continue
    const message = `Unknown node type "${node.type}". It is ignored.`
    if (node.mode === 'active') errors.push(issue('error', 'unknown_node_type', message, node.id))
    else warnings.push(issue('warning', 'unknown_node_type', message, node.id))
  }

  // More instances of a type than the server allows (only possible through an import).
  const counts = new Map<string, number>()
  for (const node of doc.graph.nodes) {
    const def = r.get(node.type)
    if (!def) continue
    const count = (counts.get(node.type) ?? 0) + 1
    counts.set(node.type, count)
    if (count > def.maxInstances) {
      errors.push(issue('error', 'too_many_instances', instancesMessage(def.title, def.maxInstances), node.id))
    }
  }

  // Link problems, in doc order.
  const taken = new Set<string>()
  doc.graph.edges.forEach((edge, index) => {
    const fromNode = byId.get(edge.from.node)
    const toNode = byId.get(edge.to.node)
    if (!fromNode || !toNode) {
      const missing = fromNode ? edge.to : edge.from
      errors.push(
        issue('error', 'unknown_port', `A link points to a missing node "${missing.node}".`, missing.node, missing.port, index),
      )
      return
    }
    const fromDef = r.get(fromNode.type)
    const toDef = r.get(toNode.type)
    if (!fromDef || !toDef) return // reported as unknown_node_type
    const output = fromDef.outputs.find((port) => port.id === edge.from.port)
    const input = toDef.inputs.find((port) => port.id === edge.to.port)
    if (!output) {
      errors.push(
        issue('error', 'unknown_port', `${fromDef.title}: unknown port "${edge.from.port}".`, fromNode.id, edge.from.port, index),
      )
    }
    if (!input) {
      errors.push(issue('error', 'unknown_port', `${toDef.title}: unknown port "${edge.to.port}".`, toNode.id, edge.to.port, index))
    }
    if (!output || !input) return
    if (!input.accepts.includes(output.type)) {
      const message = `${fromDef.title} → ${toDef.title}: ${output.label} cannot connect to ${input.label}.`
      errors.push(issue('error', 'type_mismatch', message, toNode.id, edge.to.port, index))
      return
    }
    const slot = `${edge.to.node}.${edge.to.port}`
    if (!input.multiple && taken.has(slot)) {
      const message = `${toDef.title}: "${edge.to.port}" already has a connection.`
      errors.push(issue('error', 'port_already_connected', message, toNode.id, edge.to.port, index))
    }
    taken.add(slot)
  })

  // Loops (Kahn leftovers over the whole doc graph).
  const ordered = new Set(topoOrder(doc.graph))
  const looped = [...byId.keys()].filter((id) => !ordered.has(id))
  if (looped.length > 0) errors.push(issue('error', 'cycle', `The workflow contains a loop (${looped.join(', ')}).`))

  // Locked nodes may never run; unavailable nodes only matter when they are part of the run.
  for (const node of doc.graph.nodes) {
    const def = r.get(node.type)
    if (node.mode === 'active' && def?.locked) {
      errors.push(issue('error', 'node_locked', `${def.title} is paused until you approve refinement.`, node.id))
    }
  }
  for (const id of analysis.runSet) {
    const { def } = analysis.nodes.get(id) as { def: NodeTypeDef }
    if (def.available || def.locked) continue
    const reason = def.reason ?? 'it is not available on this backend.'
    errors.push(issue('error', 'capability_unavailable', `${def.title} is unavailable: ${reason}`, id))
  }

  // Topology.
  const runOfType = (type: string): string[] => [...analysis.runSet].filter((id) => analysis.nodes.get(id)?.def.type === type)
  const cannotBeUsed = (id: string): GraphIssue =>
    issue('error', 'unsupported_topology', `${analysis.nodes.get(id)?.def.title ?? id} cannot be used here.`, id)
  let unsupported = false
  // Existing Result already has a mesh: rebuilding it through Image-to-3D is not a v1 topology.
  if (runOfType('job_result').length > 0) {
    for (const id of runOfType('image_to_3d')) {
      unsupported = true
      errors.push(cannotBeUsed(id))
    }
  }
  // Part Extraction needs its mesh from Image-to-3D or an Existing Result (defensive: future mesh producers).
  for (const id of runOfType('part_extraction')) {
    const meshEdge = analysis.edges.find((e) => e.to.node === id && e.to.port === 'mesh')
    const meshType = meshEdge ? analysis.nodes.get(meshEdge.from.node)?.def.type : undefined
    if (meshType !== undefined && meshType !== 'image_to_3d' && meshType !== 'job_result') {
      unsupported = true
      errors.push(cannotBeUsed(id))
    }
  }
  if (analysis.sources.length > 1) errors.push(issue('error', 'multiple_sources', ISSUE_MESSAGES.multipleSources))
  const needsInputs = [...analysis.feeding, ...analysis.danglingSinks]
  for (const id of needsInputs) {
    const entry = analysis.nodes.get(id)
    if (!entry || entry.def.isSource) continue
    for (const port of entry.def.inputs) {
      if (!port.required || analysis.edges.some((e) => e.to.node === id && e.to.port === port.id)) continue
      errors.push(issue('error', 'missing_required_input', `${entry.def.title}: connect "${port.label}".`, id, port.id))
    }
  }
  const noSource = analysis.sources.length === 0 || (analysis.sources.length === 1 && analysis.producer === null)
  if (noSource && !unsupported) errors.push(issue('error', 'no_source', ISSUE_MESSAGES.noSource))

  // Warnings: implicit export, parked sources, skipped nodes.
  if (analysis.sink === null && analysis.danglingSinks.length === 0 && analysis.terminal !== null) {
    warnings.push(issue('warning', 'no_sink', ISSUE_MESSAGES.noSink))
  }
  for (const id of analysis.parkedSources) {
    const { def } = analysis.nodes.get(id) as { def: NodeTypeDef }
    warnings.push(issue('warning', 'ui_parked_source', `${def.title} is not connected and is ignored.`, id))
  }
  for (const id of analysis.unreachable) {
    if (analysis.danglingSinks.includes(id)) continue
    const { def } = analysis.nodes.get(id) as { def: NodeTypeDef }
    const message =
      def.type === 'segmentation' ? ISSUE_MESSAGES.segmentationSkipped : `${def.title} is not connected and will be skipped.`
    warnings.push(issue('warning', 'ui_unreachable_node', message, id))
  }
  return { errors, warnings }
}

/** Issues that need the validation context: limits, bound values and the workflows flag. */
function contextIssues(doc: WorkflowDoc, analysis: RunAnalysis, ctx: ValidationContext): { first: GraphIssue[]; last: GraphIssue[] } {
  const first: GraphIssue[] = []
  const last: GraphIssue[] = []
  const { maxNodes, maxEdges } = ctx.features.workflow
  if (doc.graph.nodes.length > maxNodes || doc.graph.edges.length > maxEdges) {
    first.push(issue('error', 'graph_too_large', tooLargeMessage(maxNodes, maxEdges)))
  }
  const { bound } = ctx
  for (const id of analysis.runSet) {
    const binds = analysis.nodes.get(id)?.def.binds
    if (binds === 'references' && (bound.imageCount < 1 || bound.referenceSetId === null)) {
      last.push(issue('error', 'invalid_param', ISSUE_MESSAGES.noImages, id))
    } else if (binds === 'prompt' && bound.promptLength <= 0) {
      last.push(issue('error', 'invalid_param', ISSUE_MESSAGES.emptyPrompt, id))
    } else if (binds === 'prompt' && bound.promptLength > bound.promptMax) {
      last.push(issue('error', 'invalid_param', promptTooLongMessage(bound.promptMax), id))
    } else if (binds === 'viewed_job' && !bound.viewedJobCompleted) {
      last.push(issue('error', 'invalid_param', ISSUE_MESSAGES.jobNotCompleted, id))
    }
  }
  // Only a `ready` snapshot can say "turned off"; a legacy backend reports unavailable nodes instead.
  if (analysis.mode === 'workflow' && ctx.features.status === 'ready' && !ctx.features.flags.workflows) {
    last.push(issue('error', 'feature_disabled', ISSUE_MESSAGES.featureDisabled))
  }
  return { first, last }
}

/** `validateGraph` plus the run analysis it was computed from (used by `derive` and `compile`). */
export function validateWithAnalysis(doc: WorkflowDoc, r: NodeRegistry, ctx: ValidationContext): ValidationResult {
  const analysis = analyzeRun(doc, r)
  const { errors, warnings } = registryIssues(doc, r, analysis)
  const { first, last } = contextIssues(doc, analysis, ctx)
  return { issues: [...first, ...errors, ...last, ...warnings], analysis }
}

/** Client issues of a doc (errors first, then warnings). */
export function validateGraph(doc: WorkflowDoc, r: NodeRegistry, ctx: ValidationContext): GraphIssue[] {
  return [...validateWithAnalysis(doc, r, ctx).issues]
}

/** Structural errors only (no context needed): what decides whether the topology projects onto the stores. */
export function structuralErrors(doc: WorkflowDoc, r: NodeRegistry, analysis: RunAnalysis = analyzeRun(doc, r)): GraphIssue[] {
  return registryIssues(doc, r, analysis).errors.filter((i) => STRUCTURAL_CODES.has(i.code))
}

export function hasErrors(issues: readonly GraphIssue[]): boolean {
  return issues.some((i) => i.severity === 'error')
}

export function firstError(issues: readonly GraphIssue[]): GraphIssue | null {
  return issues.find((i) => i.severity === 'error') ?? null
}

const issueKey = (i: { code: string; nodeId: string | null; port: string | null }): string =>
  `${i.code}|${i.nodeId ?? ''}|${i.port ?? ''}`

/** A `POST /workflows/validate` issue in the engine shape. */
export function fromServerIssue(i: GraphIssueOut): GraphIssue {
  return {
    severity: i.severity === 'warning' ? 'warning' : 'error',
    code: i.code,
    message: i.message,
    nodeId: i.node_id ?? null,
    port: i.port ?? null,
    edgeIndex: i.edge_index ?? null,
    origin: 'server',
  }
}

/**
 * Displayed issues = server ∪ client, deduped by `(code, nodeId, port)`. Server issues are authoritative: they come
 * first and win the dedupe, so an error only the server reports still blocks Run.
 */
export function mergeIssues(client: readonly GraphIssue[], server: readonly GraphIssueOut[]): GraphIssue[] {
  const merged = server.map(fromServerIssue)
  const seen = new Set(merged.map(issueKey))
  for (const i of client) if (!seen.has(issueKey(i))) merged.push(i)
  return merged
}
