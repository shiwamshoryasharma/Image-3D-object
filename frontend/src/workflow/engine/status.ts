/**
 * Run-status projection onto the Node graph (UI_DESIGN.md §4.3.9): one `NodeStatusView` per doc node, from either a
 * workflow (`WorkflowOut.steps`, DESIGN §4.7.3/§4.7.5) or a plain `POST /jobs` job (DESIGN `legacy_job_node_map`).
 *
 * - Base states without a run: `locked`, `bypassed` (mode), `unavailable` (unknown type or `available: false`), else
 *   `idle`.
 * - `nodeIds` (the session `runIndex`, the ids that run compiled) restricts which doc nodes a run lights; a node not
 *   in it keeps its base state, so a node added after the Run never inherits a state. Runs from other sessions
 *   (`nodeIds: null`) map by node **type**. Only active nodes are lit.
 * - Step artifacts become thumbnails through `resolveUrl` (the shell passes `api/files.fileUrl`; this module only
 *   handles payload URLs and builds none).
 *
 * Which run is shown (active run, else the viewed job and its workflow) is decided by the caller.
 *
 * PURE (UI_DESIGN §1.5).
 */
import type { StepFailedDetails, WorkflowOut, WorkflowStepOut } from '../../api/partAwareTypes.ts'
import type { Job } from '../../api/types.ts'
import type { NodeRegistry } from './registry.ts'
import type { GraphNode, NodeRunState, NodeStatusView, NodeTypeDef, WorkflowDoc } from './types.ts'

export type RunView =
  | { readonly kind: 'workflow'; readonly workflow: WorkflowOut; readonly job: Job | null; readonly nodeIds: readonly string[] | null }
  | {
      readonly kind: 'job'
      readonly job: Job
      readonly nodeIds: readonly string[] | null
      /** `features.workflow.legacyJobNodeMap` (or the catalog's / the DESIGN fallback). */
      readonly legacyMap: Readonly<Record<string, string>>
    }

/** Node types of a plain job, in run order (UI_DESIGN §4.3.9 legacy table). */
export const LEGACY_NODE_ORDER = ['input_image', 'image_to_3d', 'export'] as const

export const STATUS_MESSAGES = {
  queued: 'Queued',
  waitingForModel: 'Waiting for the model',
} as const

const view = (
  state: NodeRunState,
  percent: number | null = null,
  message: string | null = null,
  thumbnailUrl: string | null = null,
): NodeStatusView => ({ state, percent, message, thumbnailUrl })

const IDLE = view('idle')

export function baseStatus(node: GraphNode, def: NodeTypeDef | undefined): NodeStatusView {
  if (def?.locked) return view('locked', null, def.reason)
  if (node.mode === 'bypass') return view('bypassed')
  if (!def) return view('unavailable', null, `Unknown node type "${node.type}".`)
  if (!def.available) return view('unavailable', null, def.reason)
  return IDLE
}

const finite = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)

function isStepFailedDetails(value: unknown): value is StepFailedDetails {
  if (typeof value !== 'object' || value === null) return false
  const details = value as Record<string, unknown>
  const child = details.child_error as Record<string, unknown> | null | undefined
  return typeof details.step === 'string' && typeof child === 'object' && child !== null && typeof child.message === 'string'
}

function stepStatus(step: WorkflowStepOut, run: Extract<RunView, { kind: 'workflow' }>, resolveUrl: (url: string) => string): NodeStatusView {
  const { workflow, job } = run
  const artifact = step.artifacts[0]
  const thumbnail = artifact ? resolveUrl(artifact.url) : null
  const message = step.message ?? null
  switch (step.status) {
    case 'pending':
      // A failed or cancelled workflow runs nothing more.
      return workflow.status === 'failed' || workflow.status === 'cancelled' ? view('skipped', null, message, thumbnail) : view('queued', null, message, thumbnail)
    case 'running':
      if (step.id === 'reconstruction' && job && (job.status === 'queued' || job.status === 'running')) {
        return view('running', finite(job.progress.percent), job.progress.stage_label, thumbnail)
      }
      return view('running', finite(step.progress), message, thumbnail)
    case 'done':
      return view((step.warning_count ?? 0) > 0 ? 'warning' : 'done', null, message, thumbnail)
    case 'failed': {
      // The child's own error ("The reassembly check failed: …") says more than the step summary.
      const details = workflow.error?.code === 'step_failed' ? workflow.error.details : null
      const childMessage = isStepFailedDetails(details) && details.step === step.id ? details.child_error.message : null
      return view('failed', null, childMessage ?? message, thumbnail)
    }
    case 'skipped':
      return view('skipped', null, message, thumbnail)
    case 'cancelled':
      return view('cancelled', null, message, thumbnail)
  }
}

/** Legacy table (UI_DESIGN §4.3.9): status of each `LEGACY_NODE_ORDER` type for a plain job. */
export function legacyJobStatuses(job: Job, legacyMap: Readonly<Record<string, string>>): readonly NodeStatusView[] {
  const order: readonly string[] = LEGACY_NODE_ORDER
  const mapped = order.indexOf(legacyMap[job.progress.stage] ?? 'image_to_3d')
  const k = mapped === -1 ? 1 : mapped
  const label = job.progress.stage_label || null
  const percentAt = (index: number): number | null => (order[index] === 'image_to_3d' ? finite(job.progress.percent) : null)
  switch (job.status) {
    case 'completed':
      return order.map(() => view('done'))
    case 'queued': {
      const position = job.queue_position
      const message = position === null ? STATUS_MESSAGES.queued : `Queued, position ${position}`
      return [view('done'), view('queued', null, message), view('queued')]
    }
    case 'running':
      if (job.progress.stage === 'waiting_for_model') {
        return [view('done'), view('queued', null, STATUS_MESSAGES.waitingForModel), view('queued')]
      }
      return order.map((_, i) => (i < k ? view('done') : i === k ? view('running', percentAt(i), label) : view('queued')))
    case 'failed':
    case 'cancelled': {
      const state = job.status
      const message = state === 'failed' ? (job.error?.message ?? null) : null
      return order.map((_, i) => (i < k ? view('done') : i === k ? view(state, null, message) : view('skipped')))
    }
  }
}

/**
 * One status per doc node id. Nodes the run does not touch keep their base state.
 * `resolveUrl` turns a step artifact's payload URL into a usable one (default: unchanged).
 */
export function projectRunState(
  doc: WorkflowDoc,
  r: NodeRegistry,
  run: RunView | null,
  resolveUrl: (url: string) => string = (url) => url,
): Readonly<Record<string, NodeStatusView>> {
  const out: Record<string, NodeStatusView> = {}
  for (const node of doc.graph.nodes) out[node.id] = baseStatus(node, r.get(node.type))
  if (run === null) return out

  const allowed = run.nodeIds === null ? null : new Set(run.nodeIds)
  const litable = (node: GraphNode): boolean =>
    node.mode === 'active' && r.get(node.type)?.locked !== true && (allowed === null || allowed.has(node.id))
  /** The doc node a step or a legacy type maps to: by id within the compiled ids, else by type. */
  const target = (nodeId: string | null, type: string): string | null => {
    if (allowed !== null && nodeId !== null) {
      const node = doc.graph.nodes.find((n) => n.id === nodeId)
      return node && litable(node) ? node.id : null
    }
    return doc.graph.nodes.find((n) => n.type === type && litable(n))?.id ?? null
  }

  if (run.kind === 'workflow') {
    for (const step of run.workflow.steps) {
      const id = target(step.node_id, step.node)
      if (id !== null) out[id] = stepStatus(step, run, resolveUrl)
    }
    return out
  }
  const statuses = legacyJobStatuses(run.job, run.legacyMap)
  LEGACY_NODE_ORDER.forEach((type, index) => {
    const id = target(null, type)
    if (id !== null) out[id] = statuses[index]
  })
  return out
}
