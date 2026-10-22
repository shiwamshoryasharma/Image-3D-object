/**
 * Library drawer (UI_DESIGN §4.3.13): `HistoryList` (opening an item views it) plus "Load into canvas" for the viewed
 * job when a workflow made it (`GET /workflows/{id}/graph` → topology and positions; "with its settings" also writes
 * the known params back to the shared stores).
 */
import { Sheet } from '../../components/common/Sheet'
import { HistoryList } from '../../components/history/HistoryList'
import { useJobStore } from '../../store/jobStore'
import { useRunStore } from '../../store/runStore'
import { useWorkflowStore } from '../../store/workflowStore'
import { Button } from '../../components/common/Button'
import styles from './NodeShell.module.css'

export function NodeLibraryDrawer({ open, onClose }: { open: boolean; onClose: () => void }) {
  const viewedJobId = useJobStore((s) => s.viewedJobId)
  const workflowId = useRunStore((s) => (viewedJobId ? (s.workflowByJobId[viewedJobId] ?? null) : null))
  const graphStored = useRunStore((s) => (workflowId ? s.workflows[workflowId]?.graph_stored === true : false))
  const load = async (withSettings: boolean) => {
    if (!workflowId) return
    if (await useWorkflowStore.getState().loadServerGraph(workflowId, withSettings)) onClose()
  }
  return (
    <Sheet id="node-library" title="Library" open={open} onClose={onClose} side="right" modal>
      <div className={styles.stackSm}>
        {workflowId && graphStored ? (
          <div className={styles.libraryActions} role="group" aria-label="Viewed model workflow">
            <p className={styles.muted}>The viewed model was made by a workflow.</p>
            <Button size="sm" variant="primary" onClick={() => void load(false)}>
              Load into canvas
            </Button>
            <Button size="sm" onClick={() => void load(true)}>
              Load into canvas with its settings
            </Button>
          </div>
        ) : null}
        <HistoryList />
      </div>
    </Sheet>
  )
}
