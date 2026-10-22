/**
 * Problems (UI_DESIGN §4.3.5): client ∪ server issues (server first), errors before warnings; "Show" selects the node.
 * Rendered in the Inspector when nothing is selected and in a dialog opened by the toolbar or a refused Run.
 */
import { Dialog } from '../../components/common/Dialog'
import { useWorkflowStore } from '../../store/workflowStore'
import type { GraphIssue, NodeRegistry, WorkflowDoc } from '../../workflow/engine/index.ts'
import styles from './NodeShell.module.css'

export interface ProblemsPanelProps {
  issues: readonly GraphIssue[]
  doc: WorkflowDoc
  registry: NodeRegistry
  onShow?: (nodeId: string) => void
}

export function ProblemsPanel({ issues, doc, registry, onShow }: ProblemsPanelProps) {
  if (!issues.length) return <p className={styles.muted}>No problems: the workflow is ready to run.</p>
  const sorted = [...issues].sort((a, b) => (a.severity === b.severity ? 0 : a.severity === 'error' ? -1 : 1))
  return (
    <ul className={styles.problems} aria-label="Workflow problems">
      {sorted.map((issue, index) => {
        const node = issue.nodeId ? doc.graph.nodes.find((n) => n.id === issue.nodeId) : undefined
        const title = node ? (registry.get(node.type)?.title ?? node.type) : null
        return (
          <li key={`${issue.origin}:${issue.code}:${issue.nodeId ?? ''}:${issue.port ?? ''}:${index}`} className={styles.problem} data-severity={issue.severity}>
            <span className={styles.problemSeverity}>{issue.severity === 'error' ? 'Error' : 'Warning'}</span>
            <span className={styles.problemText}>{issue.message}</span>
            {node && onShow ? (
              <button type="button" className={styles.linkButton} onClick={() => onShow(node.id)}>
                Show {title}
              </button>
            ) : null}
          </li>
        )
      })}
    </ul>
  )
}

export function ProblemsDialog(props: Omit<ProblemsPanelProps, 'onShow'> & { onShow: (nodeId: string) => void }) {
  const open = useWorkflowStore((s) => s.problemsOpen)
  const close = () => useWorkflowStore.getState().setProblemsOpen(false)
  return (
    <Dialog open={open} onClose={close} title="Problems">
      <ProblemsPanel
        {...props}
        onShow={(nodeId) => {
          close()
          props.onShow(nodeId)
        }}
      />
    </Dialog>
  )
}
