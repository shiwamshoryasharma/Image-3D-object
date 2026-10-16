/**
 * Run analysis: which nodes a Run would execute (UI_DESIGN.md §4.3.5–§4.3.7). Shared by `validate`, `derive` and
 * `compile`, so the three always agree on the run set.
 *
 * 1. Effective graph: active nodes with a known type and the valid edges between them, after bypass rewiring
 *    (`bypass: 'drop'`, no rule or an unknown type removes the node and its edges; `{from, to}` turns
 *    `X → node.from` + `node.to → Y` into `X → Y` when the types still match; bypassed nodes are rewired
 *    downstream first, so a chain of bypassed nodes passes through as far as the types allow). Bypassing Part
 *    Extraction therefore also drops its masks edge.
 * 2. End of the run: the active Export node that has an incoming link, else (implicit export, `no_sink`) the
 *    producer reached from a source whose output is a part set (preferred) or a mesh and which feeds nothing. An
 *    Export node with nothing connected is reported as a dangling sink (`missing_required_input`).
 * 3. Feeding set = the end and everything upstream of it. Run set = feeding nodes that are sources or reachable
 *    from an active source. A Segmentation node is in the run only when its masks reach an active Part Extraction
 *    in the run: masks have no other consumer, so a Segmentation node feeding nothing is outside the feeding set.
 * 4. Producer: the run's Image-to-3D node for an image/prompt source, its Part Extraction node for an Existing
 *    Result source (DESIGN §4.7.2 `no_source`: "no active source reaches Image-to-3D, or Part Extraction for
 *    job_result").
 *
 * PURE (UI_DESIGN §1.5).
 */
import { topoOrder } from './graph.ts'
import type { NodeRegistry } from './registry.ts'
import type { GraphEdge, GraphNode, NodeTypeDef, PipelineConfig, PortRef, PortType, WorkflowDoc } from './types.ts'

export interface EffectiveNode {
  readonly node: GraphNode
  readonly def: NodeTypeDef
}

export interface RunAnalysis {
  /** Active nodes with a known type, in doc order. */
  readonly nodes: ReadonlyMap<string, EffectiveNode>
  /** Valid edges between them after bypass rewiring. */
  readonly edges: readonly GraphEdge[]
  /** The active Export node that has an incoming link (null → an implicit export is added at compile time). */
  readonly sink: string | null
  /** Active Export nodes with nothing connected to them. */
  readonly danglingSinks: readonly string[]
  /** With `sink === null`: the producer output the implicit export is fed from. */
  readonly terminal: PortRef | null
  /** `sink ?? terminal.node`. */
  readonly end: string | null
  /** The end and every node upstream of it. */
  readonly feeding: ReadonlySet<string>
  /** Nodes reachable from an active source (sources excluded). */
  readonly reached: ReadonlySet<string>
  /** Nodes a Run executes (sources included). */
  readonly runSet: ReadonlySet<string>
  /** Active sources feeding the end, in doc order. */
  readonly sources: readonly string[]
  /** Active sources not connected to the run. */
  readonly parkedSources: readonly string[]
  /** Active non-source nodes outside the feeding set (dangling sinks included). */
  readonly unreachable: readonly string[]
  /** With exactly one source: its Image-to-3D (image/prompt) or Part Extraction (job) node in the run, else null. */
  readonly producer: string | null
  /** Topology-only projection; null unless exactly one source and its producer are in the run. */
  readonly config: PipelineConfig | null
  /** `job` iff the run types are exactly {input_image, image_to_3d, export}; null when nothing reaches the end. */
  readonly mode: 'job' | 'workflow' | null
}

/** Source node type → `PipelineConfig.source`. */
export const SOURCE_CONFIG: Readonly<Record<string, PipelineConfig['source']>> = {
  input_image: 'image',
  prompt: 'prompt',
  job_result: 'job',
}

/** `PipelineConfig.source` → source node type. */
export const SOURCE_TYPE: Readonly<Record<PipelineConfig['source'], string>> = {
  image: 'input_image',
  prompt: 'prompt',
  job: 'job_result',
}

/** DESIGN §4.7.2 `CompileResult.mode == "job"`: exactly input_image → image_to_3d → export. */
const JOB_MODE_TYPES = ['export', 'image_to_3d', 'input_image']

function outputType(def: NodeTypeDef, port: string): PortType | null {
  return def.outputs.find((p) => p.id === port)?.type ?? null
}

function accepts(def: NodeTypeDef, port: string, type: PortType | null): boolean {
  return type !== null && (def.inputs.find((p) => p.id === port)?.accepts.includes(type) ?? false)
}

/**
 * Valid doc edges: both nodes exist with a known type, both ports exist, the types match, and (for a
 * single-link input) the edge is the first one into that input. Returned with their doc index.
 */
export function validEdges(doc: WorkflowDoc, r: NodeRegistry): readonly { edge: GraphEdge; index: number }[] {
  const defs = new Map<string, NodeTypeDef>()
  for (const node of doc.graph.nodes) {
    const def = r.get(node.type)
    if (def && !defs.has(node.id)) defs.set(node.id, def)
  }
  const taken = new Set<string>()
  const out: { edge: GraphEdge; index: number }[] = []
  doc.graph.edges.forEach((edge, index) => {
    const fromDef = defs.get(edge.from.node)
    const toDef = defs.get(edge.to.node)
    if (!fromDef || !toDef) return
    const input = toDef.inputs.find((p) => p.id === edge.to.port)
    if (!input || !accepts(toDef, edge.to.port, outputType(fromDef, edge.from.port))) return
    const slot = `${edge.to.node}.${edge.to.port}`
    if (!input.multiple && taken.has(slot)) return
    taken.add(slot)
    out.push({ edge, index })
  })
  return out
}

function effectiveGraph(doc: WorkflowDoc, r: NodeRegistry): { nodes: Map<string, EffectiveNode>; edges: GraphEdge[] } {
  const known = new Map<string, EffectiveNode>()
  for (const node of doc.graph.nodes) {
    const def = r.get(node.type)
    if (def && !known.has(node.id)) known.set(node.id, { node, def })
  }
  const dropped = new Set<string>()
  const passing: EffectiveNode[] = []
  for (const entry of known.values()) {
    if (entry.node.mode !== 'bypass') continue
    if (entry.def.bypass === null || entry.def.bypass === 'drop') dropped.add(entry.node.id)
    else passing.push(entry)
  }
  // Downstream first: a bypassed node then sees the edges its bypassed successors already rewired.
  const order = topoOrder(doc.graph)
  const rank = (id: string): number => {
    const at = order.indexOf(id)
    return at === -1 ? order.length : at
  }
  passing.sort((a, b) => rank(b.node.id) - rank(a.node.id))

  let edges = validEdges(doc, r)
    .map(({ edge }) => edge)
    .filter((edge) => !dropped.has(edge.from.node) && !dropped.has(edge.to.node))
  for (const { node, def } of passing) {
    const rule = def.bypass as { from: string; to: string }
    const into = edges.filter((e) => e.to.node === node.id && e.to.port === rule.from)
    const outOf = edges.filter((e) => e.from.node === node.id && e.from.port === rule.to)
    edges = edges.filter((e) => e.from.node !== node.id && e.to.node !== node.id)
    for (const a of into) {
      const fromDef = known.get(a.from.node)?.def
      for (const b of outOf) {
        const toDef = known.get(b.to.node)?.def
        if (!fromDef || !toDef || !accepts(toDef, b.to.port, outputType(fromDef, a.from.port))) continue
        const multiple = toDef.inputs.find((p) => p.id === b.to.port)?.multiple === true
        if (!multiple && edges.some((e) => e.to.node === b.to.node && e.to.port === b.to.port)) continue
        edges.push({ from: a.from, to: b.to })
      }
    }
  }
  const nodes = new Map<string, EffectiveNode>()
  for (const [id, entry] of known) if (entry.node.mode === 'active') nodes.set(id, entry)
  return { nodes, edges }
}

function walk(start: Iterable<string>, next: (id: string) => readonly string[]): Set<string> {
  const seen = new Set<string>()
  const stack = [...start]
  while (stack.length > 0) {
    const id = stack.pop() as string
    for (const other of next(id)) {
      if (!seen.has(other)) {
        seen.add(other)
        stack.push(other)
      }
    }
  }
  return seen
}

export function analyzeRun(doc: WorkflowDoc, r: NodeRegistry): RunAnalysis {
  const { nodes, edges } = effectiveGraph(doc, r)
  const targets = new Map<string, string[]>()
  const parents = new Map<string, string[]>()
  for (const edge of edges) {
    targets.set(edge.from.node, [...(targets.get(edge.from.node) ?? []), edge.to.node])
    parents.set(edge.to.node, [...(parents.get(edge.to.node) ?? []), edge.from.node])
  }
  const entries = [...nodes.values()]
  const activeSources = entries.filter((e) => e.def.isSource).map((e) => e.node.id)
  const reached = walk(activeSources, (id) => targets.get(id) ?? [])

  const outputs = entries.filter((e) => e.def.category === 'output').map((e) => e.node.id)
  const sink = outputs.find((id) => parents.has(id)) ?? null
  const danglingSinks = outputs.filter((id) => !parents.has(id))
  let terminal: PortRef | null = null
  if (sink === null) {
    const leaves = entries.filter((e) => reached.has(e.node.id) && !e.def.isSource && !targets.has(e.node.id))
    for (const wanted of ['part_set', 'mesh'] as const) {
      const leaf = leaves.find((e) => e.def.outputs.some((p) => p.type === wanted))
      if (leaf) {
        terminal = { node: leaf.node.id, port: leaf.def.outputs.find((p) => p.type === wanted)?.id ?? '' }
        break
      }
    }
  }
  const end = sink ?? terminal?.node ?? null
  const feeding = end === null ? new Set<string>() : walk([end], (id) => parents.get(id) ?? [])
  if (end !== null) feeding.add(end)

  const sources = activeSources.filter((id) => feeding.has(id))
  const runSet = new Set([...feeding].filter((id) => reached.has(id) || sources.includes(id)))
  const parkedSources = activeSources.filter((id) => !feeding.has(id))
  const unreachable = entries.filter((e) => !e.def.isSource && !feeding.has(e.node.id)).map((e) => e.node.id)

  const typeOf = (id: string): string | undefined => nodes.get(id)?.def.type
  const runTypes = new Set([...runSet].map(typeOf))
  const source = sources.length === 1 ? SOURCE_CONFIG[typeOf(sources[0]) ?? ''] : undefined
  const producerType = source === undefined ? null : source === 'job' ? 'part_extraction' : 'image_to_3d'
  const producer = producerType === null ? null : ([...runSet].find((id) => typeOf(id) === producerType) ?? null)

  const endRuns = end !== null && runSet.has(end)
  let mode: RunAnalysis['mode'] = null
  if (endRuns) {
    const types = new Set(runTypes)
    if (sink === null) types.add('export')
    mode = types.size === JOB_MODE_TYPES.length && JOB_MODE_TYPES.every((t) => types.has(t)) ? 'job' : 'workflow'
  }

  let config: PipelineConfig | null = null
  if (endRuns && source !== undefined && producer !== null) {
    const partExtraction = runTypes.has('part_extraction')
    // DESIGN §4.7.1: segmentation only together with parts (masks have no other consumer; defensive).
    config = { source, segmentation: partExtraction && runTypes.has('segmentation'), partExtraction }
  }

  return {
    nodes,
    edges,
    sink,
    danglingSinks,
    terminal,
    end,
    feeding,
    reached,
    runSet,
    sources,
    parkedSources,
    unreachable,
    producer,
    config,
    mode,
  }
}

/** The effective-graph edge into `node.port`, if any. */
export function inputEdge(analysis: RunAnalysis, node: string, port: string): GraphEdge | undefined {
  return analysis.edges.find((edge) => edge.to.node === node && edge.to.port === port)
}

/** Every node upstream of `id` in the effective graph. */
export function upstreamOf(analysis: RunAnalysis, id: string): ReadonlySet<string> {
  return walk([id], (node) => analysis.edges.filter((e) => e.to.node === node).map((e) => e.from.node))
}
