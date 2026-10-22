/**
 * Run (UI_DESIGN §4.3.8): registered as `navigationStore.runOverride` while the Node shell is mounted, so the toolbar
 * Run and the one global Ctrl/Cmd+Enter both reach it through `startGeneration()`.
 * Returns whether something was posted:
 * (1) `!out.ok` → Problems + assertive announce, false · (2) `mode:'job'` → only `startDefaultGeneration()` (G1 via
 * `jobStore.submit()`) · (3) legacy / `!workflow.available` → toast, false · (4) remote validation `null` → error toast
 * + announce, false, nothing posted · (5) `!report.valid` → Problems, false · (6) valid → one `POST /workflows`,
 * `rememberRun`, reveal the viewer, true.
 */
import { startDefaultGeneration } from '../../actions/generation'
import { announce } from '../../store/announcerStore'
import { useFeaturesStore } from '../../store/featuresStore'
import { useJobStore } from '../../store/jobStore'
import { useNavigationStore } from '../../store/navigationStore'
import { useRunStore } from '../../store/runStore'
import { toast } from '../../store/toastStore'
import { registryNow, useWorkflowStore } from '../../store/workflowStore'
import { compileDoc, firstError } from '../../workflow/engine/index.ts'
import { boundFromStores, contextFromStores } from './bindings'
import { NODE_TEXT } from './nodeText'

export async function runNodeWorkflow(): Promise<boolean> {
  const workflow = useWorkflowStore.getState()
  const { doc } = workflow
  const out = compileDoc(doc, registryNow(), boundFromStores(), contextFromStores())
  if (!out.ok) {
    workflow.setProblemsOpen(true)
    announce(firstError(out.issues)?.message ?? NODE_TEXT.fixProblems, 'assertive')
    return false
  }
  if (out.mode === 'job') {
    const ok = await startDefaultGeneration()
    const jobId = useJobStore.getState().activeJobId
    if (ok && jobId) useWorkflowStore.getState().rememberRun(jobId, 'job', out.nodeIds)
    return ok
  }
  const features = useFeaturesStore.getState().snapshot
  if (features.status === 'legacy' || !features.workflow.available) {
    const reason = features.status === 'legacy' ? null : features.workflow.reason
    toast.warning(NODE_TEXT.needsBackend, reason ?? undefined)
    announce(reason ?? NODE_TEXT.needsBackend, 'assertive')
    return false
  }
  const report = await useWorkflowStore.getState().validateRemote(doc.revision, out.request)
  if (report === null) {
    toast.error(NODE_TEXT.validateFailedTitle, NODE_TEXT.validateFailedText)
    announce(`${NODE_TEXT.validateFailedTitle}. ${NODE_TEXT.validateFailedText}`, 'assertive')
    return false
  }
  if (!report.valid) {
    useWorkflowStore.getState().setProblemsOpen(true)
    announce(report.issues.find((i) => i.severity === 'error')?.message ?? NODE_TEXT.fixProblems, 'assertive')
    return false
  }
  const wf = await useRunStore.getState().startWorkflow(out.request)
  if (!wf) return false
  useWorkflowStore.getState().rememberRun(wf.id, 'workflow', out.nodeIds)
  useNavigationStore.getState().requestReveal('viewer')
  announce('Workflow queued')
  return true
}
