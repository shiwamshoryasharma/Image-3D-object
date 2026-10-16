/**
 * Default workflows (UI_DESIGN.md §4.3.6): the deterministic template doc for a `PipelineConfig`, the template
 * layout every mode shares, and the empty doc.
 *
 * Image / prompt template: both sources (the unused one parked, with a "Use this source" button in the shell),
 * Segmentation, Image-to-3D, Part Extraction and Export, columns 320 px apart; Part Extraction is active iff the
 * config extracts parts, Segmentation iff it also segments (otherwise bypassed). Existing Result template:
 * Existing Result → (Segmentation) → Part Extraction → Export (a job source always extracts parts).
 *
 * PURE (UI_DESIGN §1.5).
 */
import { nodeIdFor } from './graph.ts'
import { SOURCE_TYPE } from './plan.ts'
import type { NodeRegistry } from './registry.ts'
import { WORKFLOW_DOC_SCHEMA, WORKFLOW_DOC_VERSION } from './types.ts'
import type { GraphEdge, GraphNode, Layout, PipelineConfig, Point, Viewport, WorkflowDoc } from './types.ts'

/** Template position of each built-in type (UI_DESIGN §4.3.6). */
export const TEMPLATE_POSITIONS: Readonly<Record<string, Point>> = Object.freeze({
  input_image: { x: 0, y: 0 },
  prompt: { x: 0, y: 260 },
  job_result: { x: 0, y: 130 },
  segmentation: { x: 320, y: 0 },
  image_to_3d: { x: 320, y: 260 },
  part_extraction: { x: 640, y: 130 },
  export: { x: 960, y: 260 },
})

export const TEMPLATE_VIEWPORT: Viewport = Object.freeze({ x: 40, y: 40, zoom: 0.9 })

export const EMPTY_LAYOUT: Layout = Object.freeze({ positions: {}, collapsed: [], viewport: TEMPLATE_VIEWPORT })

export function emptyDoc(revision = 0): WorkflowDoc {
  return {
    schema: WORKFLOW_DOC_SCHEMA,
    version: WORKFLOW_DOC_VERSION,
    revision,
    graph: { nodes: [], edges: [] },
    layout: EMPTY_LAYOUT,
  }
}

/**
 * One of the 8 valid configs (DESIGN §4.7.1): segmentation only together with parts, and an Existing Result source
 * only with parts (without parts a job source has nothing to run, so it falls back to the image source).
 */
export function normalizeConfig(c: PipelineConfig): PipelineConfig {
  const source = c.source === 'job' && !c.partExtraction ? 'image' : c.source
  return { source, segmentation: c.partExtraction && c.segmentation, partExtraction: c.partExtraction }
}

export function sameConfig(a: PipelineConfig | null, b: PipelineConfig | null): boolean {
  if (a === null || b === null) return a === b
  return a.source === b.source && a.segmentation === b.segmentation && a.partExtraction === b.partExtraction
}

const link = (from: string, fromPort: string, to: string, toPort: string): GraphEdge => ({
  from: { node: nodeIdFor(from), port: fromPort },
  to: { node: nodeIdFor(to), port: toPort },
})

/** The template doc for a config (a type missing from the registry is left out together with its links). */
export function templateFromConfig(c: PipelineConfig, r: NodeRegistry, revision = 0): WorkflowDoc {
  const config = normalizeConfig(c)
  const source = SOURCE_TYPE[config.source]
  const types =
    config.source === 'job'
      ? ['job_result', 'segmentation', 'part_extraction', 'export']
      : ['input_image', 'prompt', 'segmentation', 'image_to_3d', 'part_extraction', 'export']
  const edges: GraphEdge[] =
    config.source === 'job'
      ? [
          link(source, 'mesh', 'part_extraction', 'mesh'),
          link(source, 'images', 'segmentation', 'images'),
          link('segmentation', 'masks', 'part_extraction', 'masks'),
          link('part_extraction', 'parts', 'export', 'asset'),
        ]
      : [
          link(source, 'images', 'image_to_3d', 'images'),
          link(source, 'images', 'segmentation', 'images'),
          link('image_to_3d', 'mesh', 'part_extraction', 'mesh'),
          link('segmentation', 'masks', 'part_extraction', 'masks'),
          link('part_extraction', 'parts', 'export', 'asset'),
        ]
  const nodes: GraphNode[] = []
  const positions: Record<string, Point> = {}
  for (const type of types) {
    const def = r.get(type)
    if (!def) continue
    const id = nodeIdFor(type)
    let active = true
    if (type === 'part_extraction') active = config.partExtraction
    else if (type === 'segmentation') active = config.segmentation
    nodes.push({ id, type, version: def.version, mode: active ? 'active' : 'bypass' })
    positions[id] = { ...TEMPLATE_POSITIONS[type] }
  }
  const present = new Set(nodes.map((node) => node.id))
  return {
    schema: WORKFLOW_DOC_SCHEMA,
    version: WORKFLOW_DOC_VERSION,
    revision,
    graph: { nodes, edges: edges.filter((e) => present.has(e.from.node) && present.has(e.to.node)) },
    layout: { positions, collapsed: [], viewport: { ...TEMPLATE_VIEWPORT } },
  }
}
