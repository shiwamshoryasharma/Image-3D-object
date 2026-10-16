/**
 * Projection between the Node graph and the shared stores (UI_DESIGN.md §4.3.6).
 *
 * - `deriveConfig` (graph → stores): which source, whether parts are extracted and whether the workflow segments
 *   first. The config is null only for structural errors; a Run can still be blocked by bound values or
 *   capabilities (`pipelineBlock` comes from the full issue list), but Default/Mobile/Tablet keep following the graph.
 * - `configFromStores` + `reconcile` (stores → graph): the minimal edit that makes the graph express the stores'
 *   config; `reconcile` returns the input doc itself when nothing has to change (idempotent).
 *
 * Invariant (DESIGN §4.7.1): segmentation ⇒ partExtraction. Valid configs: source image|prompt × {none, parts,
 * parts+segmentation} and source job × {parts, parts+segmentation}.
 *
 * PURE (UI_DESIGN §1.5).
 */
import { addNode, canConnect, connect, findNode, nodeOfType, setMode } from './graph.ts'
import { analyzeRun, SOURCE_TYPE } from './plan.ts'
import type { NodeRegistry } from './registry.ts'
import { normalizeConfig, sameConfig, TEMPLATE_POSITIONS, templateFromConfig } from './template.ts'
import type { EngineInputSource, GraphIssue, PartOptionsFlags, PipelineConfig, PortRef, ValidationContext, WorkflowDoc } from './types.ts'
import { STRUCTURAL_CODES, structuralErrors, validateWithAnalysis } from './validate.ts'

export interface DeriveResult {
  readonly config: PipelineConfig | null
  readonly issues: readonly GraphIssue[]
}

export function deriveConfig(doc: WorkflowDoc, r: NodeRegistry, ctx: ValidationContext): DeriveResult {
  const { issues, analysis } = validateWithAnalysis(doc, r, ctx)
  const structural = issues.some((i) => i.severity === 'error' && STRUCTURAL_CODES.has(i.code))
  return { config: structural ? null : analysis.config, issues }
}

/** `deriveConfig(...).config` without a validation context (bound values and flags never change the topology). */
export function topologyConfig(doc: WorkflowDoc, r: NodeRegistry): PipelineConfig | null {
  const analysis = analyzeRun(doc, r)
  return structuralErrors(doc, r, analysis).length > 0 ? null : analysis.config
}

/**
 * The config the stores ask for. `docSource` is the source the graph currently runs from: an Existing Result source
 * is kept while parts are on (a job source needs Part Extraction), otherwise the stores' input source wins.
 */
export function configFromStores(
  docSource: PipelineConfig['source'] | null,
  input: EngineInputSource,
  parts: PartOptionsFlags,
): PipelineConfig {
  return {
    source: docSource === 'job' && parts.enabled ? 'job' : input,
    segmentation: parts.enabled && parts.previewSegmentation,
    partExtraction: parts.enabled,
  }
}

const port = (node: string, id: string): PortRef => ({ node, port: id })

/** `connect` when `canConnect` allows it (also replaces the link into a single-input port); otherwise unchanged. */
function link(doc: WorkflowDoc, r: NodeRegistry, from: PortRef, to: PortRef): WorkflowDoc {
  return canConnect(doc, r, from, to).ok ? connect(doc, from, to, r) : doc
}

/** Part Extraction / Segmentation modes as the config says (refused mode changes leave the node as it is). */
function applyModes(doc: WorkflowDoc, c: PipelineConfig, r: NodeRegistry): WorkflowDoc {
  let next = doc
  const parts = nodeOfType(next.graph, 'part_extraction')
  if (parts) next = setMode(next, parts.id, c.partExtraction ? 'active' : 'bypass', r)
  const segmentation = nodeOfType(next.graph, 'segmentation')
  if (segmentation) next = setMode(next, segmentation.id, c.segmentation ? 'active' : 'bypass', r)
  return next
}

/** Wire a source node into the producer input(s) and Segmentation, replacing the other source's links. */
function rewireFrom(doc: WorkflowDoc, sourceId: string, r: NodeRegistry): WorkflowDoc {
  const source = findNode(doc.graph, sourceId)
  if (!source) return doc
  let next = doc
  if (source.type === SOURCE_TYPE.job) {
    const parts = nodeOfType(next.graph, 'part_extraction')
    if (parts) next = link(next, r, port(source.id, 'mesh'), port(parts.id, 'mesh'))
  } else {
    const producer = nodeOfType(next.graph, 'image_to_3d')
    const parts = nodeOfType(next.graph, 'part_extraction')
    if (producer) next = link(next, r, port(source.id, 'images'), port(producer.id, 'images'))
    // Coming from an Existing Result: Part Extraction takes its mesh from Image-to-3D again.
    if (producer && parts) next = link(next, r, port(producer.id, 'mesh'), port(parts.id, 'mesh'))
  }
  const segmentation = nodeOfType(next.graph, 'segmentation')
  if (segmentation) next = link(next, r, port(source.id, 'images'), port(segmentation.id, 'images'))
  return next
}

function rewireSource(doc: WorkflowDoc, c: PipelineConfig, r: NodeRegistry): WorkflowDoc {
  const source = nodeOfType(doc.graph, SOURCE_TYPE[c.source])
  return source ? rewireFrom(doc, source.id, r) : doc
}

/** Insert the nodes the config needs (at their template positions) and lay the canonical links between them. */
function ensureCanonical(doc: WorkflowDoc, c: PipelineConfig, r: NodeRegistry): WorkflowDoc {
  const needed =
    c.source === 'job'
      ? ['job_result', 'part_extraction']
      : [SOURCE_TYPE[c.source], 'image_to_3d', ...(c.partExtraction ? ['part_extraction'] : [])]
  if (c.segmentation) needed.push('segmentation')
  let next = doc
  for (const type of needed) {
    const def = r.get(type)
    if (!def || nodeOfType(next.graph, type)) continue
    next = addNode(next, def, TEMPLATE_POSITIONS[type] ?? { x: 0, y: 0 })
  }
  const id = (type: string): string | null => nodeOfType(next.graph, type)?.id ?? null
  const source = id(SOURCE_TYPE[c.source])
  const producer = c.source === 'job' ? null : id('image_to_3d')
  const segmentation = id('segmentation')
  const parts = id('part_extraction')
  const sink = id('export')
  if (source === null) return next
  next = rewireSource(next, c, r)
  if (producer !== null && parts !== null) next = link(next, r, port(producer, 'mesh'), port(parts, 'mesh'))
  if (segmentation !== null && parts !== null) next = link(next, r, port(segmentation, 'masks'), port(parts, 'masks'))
  if (sink !== null) {
    if (parts !== null) next = link(next, r, port(parts, 'parts'), port(sink, 'asset'))
    else if (producer !== null) next = link(next, r, port(producer, 'mesh'), port(sink, 'asset'))
  }
  return applyModes(next, c, r)
}

/**
 * The minimal edit so `topologyConfig(result)` equals `c` (normalized to a valid config): first the Part Extraction
 * / Segmentation modes, then the source links, then missing nodes and canonical links, and only as a last resort
 * (a structurally broken doc) the template. Returns `doc` itself when it already expresses `c`; otherwise one
 * revision later than `doc`.
 */
export function reconcile(doc: WorkflowDoc, c: PipelineConfig, r: NodeRegistry): WorkflowDoc {
  const target = normalizeConfig(c)
  if (sameConfig(topologyConfig(doc, r), target)) return doc
  const steps = [applyModes, rewireSource, ensureCanonical]
  let next = doc
  for (const step of steps) {
    next = step(next, target, r)
    if (sameConfig(topologyConfig(next, r), target)) return { ...next, revision: doc.revision + 1 }
  }
  const template = templateFromConfig(target, r, doc.revision + 1)
  return { ...template, layout: { ...template.layout, viewport: doc.layout.viewport } }
}

/**
 * "Use this source" (UI_DESIGN §4.3.6): make a parked source the run's source in one edit. The previous source
 * stays in the graph, parked. Returns `doc` itself when nothing changes.
 */
export function switchSource(doc: WorkflowDoc, sourceId: string, r: NodeRegistry): WorkflowDoc {
  const next = rewireFrom(doc, sourceId, r)
  return next === doc ? doc : { ...next, revision: doc.revision + 1 }
}
