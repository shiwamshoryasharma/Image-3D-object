/**
 * Node status projection (UI_DESIGN §4.3.9): the active run if not terminal, else the viewed job (with its workflow
 * when `runStore.workflowByJobId` knows one), else none. `nodeIds` come from the session `runIndex`; runs from other
 * sessions map by node type. Re-evaluated on every `workflow.updated` / `job.updated` without touching the doc.
 */
import { FALLBACK_LEGACY_JOB_NODE_MAP } from '../../lib/featureFlags.ts'
import { useFeaturesStore } from '../../store/featuresStore'
import { isTerminal, selectActiveJob, selectViewedJob, useJobStore } from '../../store/jobStore'
import { selectActiveWorkflow, useRunStore } from '../../store/runStore'
import { useWorkflowStore } from '../../store/workflowStore'
import {
  projectRunState,
  type NodeRegistry,
  type NodeStatusView,
  type RunView,
  type WorkflowDoc,
} from '../../workflow/engine/index.ts'

export function useNodeRunStates(doc: WorkflowDoc, registry: NodeRegistry): Readonly<Record<string, NodeStatusView>> {
  const activeWorkflow = useRunStore(selectActiveWorkflow)
  const activeJob = useJobStore(selectActiveJob)
  const viewedJob = useJobStore(selectViewedJob)
  const viewedWorkflowId = useRunStore((s) => (viewedJob ? (s.workflowByJobId[viewedJob.id] ?? null) : null))
  const viewedWorkflow = useRunStore((s) => (viewedWorkflowId ? (s.workflows[viewedWorkflowId] ?? null) : null))
  const workflowJob = useJobStore((s) => {
    const id = (activeWorkflow && !isTerminal(activeWorkflow) ? activeWorkflow : viewedWorkflow)?.outputs.job_id
    return id ? (s.jobs[id] ?? null) : null
  })
  const runIndex = useWorkflowStore((s) => s.runIndex)
  const legacyMap = useFeaturesStore((s) => s.snapshot.workflow.legacyJobNodeMap)

  let run: RunView | null = null
  if (activeWorkflow && !isTerminal(activeWorkflow)) {
    run = { kind: 'workflow', workflow: activeWorkflow, job: workflowJob, nodeIds: runIndex[activeWorkflow.id]?.nodeIds ?? null }
  } else if (activeJob && !isTerminal(activeJob)) {
    run = { kind: 'job', job: activeJob, nodeIds: runIndex[activeJob.id]?.nodeIds ?? null, legacyMap: mapOrFallback(legacyMap) }
  } else if (viewedJob && viewedWorkflow) {
    run = { kind: 'workflow', workflow: viewedWorkflow, job: workflowJob, nodeIds: runIndex[viewedWorkflow.id]?.nodeIds ?? null }
  } else if (viewedJob) {
    run = { kind: 'job', job: viewedJob, nodeIds: runIndex[viewedJob.id]?.nodeIds ?? null, legacyMap: mapOrFallback(legacyMap) }
  }
  // Payload URLs stay unresolved: shells may not import the URL helpers (§1.4), so node bodies show no step thumbnail.
  return projectRunState(doc, registry, run)
}

function mapOrFallback(map: Readonly<Record<string, string>>): Readonly<Record<string, string>> {
  return Object.keys(map).length ? map : FALLBACK_LEGACY_JOB_NODE_MAP
}
