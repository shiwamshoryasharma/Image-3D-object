/**
 * List view (UI_DESIGN §4.3.13): the canonical screen-reader representation. Cards in topological order (title,
 * status, mode switch, summary, issues, Edit); every input port is a `<select>` of the compatible upstream outputs, so
 * links are made and removed without dragging (WCAG 2.5.7). Below 600 px the Inspector expands inline in the card.
 */
import { Plus } from 'lucide-react'

import { Button } from '../../components/common/Button'
import { Switch } from '../../components/common/Switch'
import { useWorkflowStore, type WorkflowSelection } from '../../store/workflowStore'
import {
  canConnect,
  edgeKey,
  PORT_META,
  topoOrder,
  type GraphIssue,
  type GraphNode,
  type InputPortDef,
  type NodeRegistry,
  type NodeStatusView,
  type WorkflowDoc,
} from '../../workflow/engine/index.ts'
import { NodeInspector } from './NodeInspector'
import { RUN_STATE_GLYPH, statusText } from './nodeText'
import { useNodeSummary } from './useNodeSummary'
import styles from './NodeShell.module.css'

export interface NodeListViewProps {
  doc: WorkflowDoc
  registry: NodeRegistry
  selection: WorkflowSelection | null
  statuses: Readonly<Record<string, NodeStatusView>>
  issues: readonly GraphIssue[]
  summary: string
  summaryId: string
  /** Expand the Inspector inside the selected card. */
  inlineInspector: boolean
  onOpenLibrary: () => void
  onEdit: (nodeId: string) => void
}

const refKey = (node: string, port: string): string => `${node}.${port}`

function PortSelect({ doc, registry, node, port }: { doc: WorkflowDoc; registry: NodeRegistry; node: GraphNode; port: InputPortDef }) {
  const store = useWorkflowStore.getState
  const incoming = doc.graph.edges.filter((e) => e.to.node === node.id && e.to.port === port.id)
  const options: { value: string; label: string }[] = []
  for (const other of doc.graph.nodes) {
    if (other.id === node.id) continue
    const def = registry.get(other.type)
    for (const out of def?.outputs ?? []) {
      if (!port.accepts.includes(out.type)) continue
      const from = { node: other.id, port: out.id }
      const linked = incoming.some((e) => e.from.node === other.id && e.from.port === out.id)
      if (linked || canConnect(doc, registry, from, { node: node.id, port: port.id }).ok) {
        options.push({ value: refKey(other.id, out.id), label: `${def?.title ?? other.type} · ${out.label}` })
      }
    }
  }
  const id = `port-${node.id}-${port.id}`
  const accepts = port.accepts.map((t) => PORT_META[t].label).join(' / ')
  const onChange = (value: string) => {
    if (!value) {
      for (const e of incoming) store().disconnect(edgeKey(e))
      return
    }
    // `connect` replaces the link into a single-input port (one history entry).
    const [fromNode, fromPort] = value.split('.')
    store().connect({ node: fromNode, port: fromPort }, { node: node.id, port: port.id })
  }
  return (
    <div className={styles.portField}>
      <label htmlFor={id}>
        {port.label} <span className={styles.muted}>({accepts}{port.required ? ', required' : ''})</span>
      </label>
      {port.multiple ? (
        <>
          {incoming.map((e) => (
            <span key={edgeKey(e)} className={styles.linkChip}>
              {registry.get(doc.graph.nodes.find((n) => n.id === e.from.node)?.type ?? '')?.title ?? e.from.node}
              <button type="button" className={styles.linkButton} onClick={() => store().disconnect(edgeKey(e))}>
                Disconnect
              </button>
            </span>
          ))}
          <select id={id} value="" onChange={(event) => onChange(event.target.value)}>
            <option value="">Add a link…</option>
            {options
              .filter((o) => !incoming.some((e) => refKey(e.from.node, e.from.port) === o.value))
              .map((o) => (
                <option key={o.value} value={o.value}>
                  {o.label}
                </option>
              ))}
          </select>
        </>
      ) : (
        <select id={id} value={incoming[0] ? refKey(incoming[0].from.node, incoming[0].from.port) : ''} onChange={(event) => onChange(event.target.value)}>
          <option value="">Not connected</option>
          {options.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </select>
      )}
    </div>
  )
}

function NodeCard(props: NodeListViewProps & { node: GraphNode }) {
  const { doc, registry, selection, statuses, issues, node, inlineInspector, onEdit } = props
  const summaryLine = useNodeSummary(node.type)
  const def = registry.get(node.type)
  const status = statuses[node.id]
  const selected = selection?.kind === 'node' && selection.id === node.id
  const nodeIssues = issues.filter((i) => i.nodeId === node.id)
  const title = def?.title ?? node.type
  const headingId = `node-card-${node.id}`
  const canBypass = !!def && def.bypass !== null && !def.locked
  return (
    <li>
      <article className={`${styles.card} ${selected ? styles.cardSelected : ''}`} aria-labelledby={headingId} data-mode={node.mode}>
        <header className={styles.cardHeader}>
          <h3 id={headingId} className={styles.cardTitle}>
            {title}
          </h3>
          {status ? (
            <span className={styles.statusChip} data-state={status.state}>
              <span aria-hidden="true">{RUN_STATE_GLYPH[status.state]}</span> {statusText(status.state, status.percent)}
            </span>
          ) : null}
        </header>
        {summaryLine ? <p className={styles.muted}>{summaryLine}</p> : null}
        {canBypass ? (
          <Switch
            size="sm"
            label={`${title} active`}
            checked={node.mode === 'active'}
            onChange={(on) => useWorkflowStore.getState().setMode(node.id, on ? 'active' : 'bypass')}
          />
        ) : null}
        {nodeIssues.length ? (
          <ul className={styles.problems}>
            {nodeIssues.map((issue, index) => (
              <li key={`${issue.code}:${index}`} className={styles.problem} data-severity={issue.severity}>
                <span className={styles.problemText}>{issue.message}</span>
              </li>
            ))}
          </ul>
        ) : null}
        {def?.inputs.map((port) => <PortSelect key={port.id} doc={doc} registry={registry} node={node} port={port} />)}
        <div className={styles.cardActions}>
          <Button size="sm" aria-expanded={inlineInspector ? selected : undefined} onClick={() => onEdit(node.id)}>
            {inlineInspector && selected ? 'Close' : 'Edit'}
          </Button>
        </div>
        {inlineInspector && selected ? (
          <div className={styles.inlineInspector}>
            <NodeInspector {...props} bare />
          </div>
        ) : null}
      </article>
    </li>
  )
}

export function NodeListView(props: NodeListViewProps) {
  const { doc, summary, summaryId } = props
  const order = topoOrder(doc.graph)
  const ids = [...order, ...doc.graph.nodes.map((n) => n.id).filter((id) => !order.includes(id))]
  const nodes = ids.map((id) => doc.graph.nodes.find((n) => n.id === id)).filter((n): n is GraphNode => n !== undefined)
  return (
    <section className={styles.list} aria-label="Workflow nodes">
      <div className={styles.listHeader}>
        <p id={summaryId} className={styles.muted}>
          {summary}
        </p>
        <Button
          size="sm"
          icon={<Plus size={16} aria-hidden />}
          onClick={() => useWorkflowStore.getState().openPalette({ accepts: null, position: null, connectFrom: null })}
        >
          Add node
        </Button>
      </div>
      <ol className={styles.cards}>
        {nodes.map((node) => (
          <NodeCard key={node.id} {...props} node={node} />
        ))}
      </ol>
    </section>
  )
}
