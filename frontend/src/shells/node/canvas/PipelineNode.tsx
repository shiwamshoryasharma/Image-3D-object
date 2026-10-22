/**
 * Custom React Flow node (UI_DESIGN §4.3.12), laid out like a ComfyUI node: a title bar tinted with the category
 * colour (collapse toggle, title, "Bypassed" tag, lock / unavailable / problem badges, a status pill once the node is
 * not idle), an inline progress bar while running, the slot rows (inputs on the left edge, outputs on the right
 * edge), then read-only widget rows with the values the node binds to (editing happens in the Inspector; never a 3D
 * view, UI5). No step thumbnail yet: its payload URL needs a URL helper, which shells may not import (§1.4). Status
 * comes from `NodeStatusContext`, not from `nodes`.
 */
import { useContext, type CSSProperties } from 'react'
import type { NodeProps } from '@xyflow/react'
import { ChevronDown, ChevronRight } from 'lucide-react'

import { ProgressBar } from '../../../components/common/ProgressBar'
import { useWorkflowStore } from '../../../store/workflowStore'
import { CATEGORY_META, type NodeStatusView } from '../../../workflow/engine/types.ts'
import { useNodeRegistry } from '../bindings'
import { RUN_STATE_GLYPH, statusText } from '../nodeText'
import { useNodeWidgets } from '../useNodeSummary'
import type { PipelineFlowNode } from './flowAdapter.ts'
import { NodeIssueContext, NodeStatusContext } from './statusContext'
import { PortHandle } from './PortHandle'
import styles from './flow.module.css'

const IDLE: NodeStatusView = { state: 'idle', percent: null, message: null, thumbnailUrl: null }

export function PipelineNode({ data, selected }: NodeProps<PipelineFlowNode>) {
  const registry = useNodeRegistry()
  const status = useContext(NodeStatusContext)[data.id] ?? IDLE
  const issueCount = useContext(NodeIssueContext)[data.id] ?? 0
  const mode = useWorkflowStore((s) => s.doc.graph.nodes.find((n) => n.id === data.id)?.mode ?? 'active')
  const collapsed = useWorkflowStore((s) => s.doc.layout.collapsed.includes(data.id))
  const connectedPorts = useWorkflowStore((s) =>
    s.doc.graph.edges
      .filter((e) => e.to.node === data.id || e.from.node === data.id)
      .map((e) => (e.to.node === data.id ? `in:${e.to.port}` : `out:${e.from.port}`))
      .join('|'),
  )
  const setCollapsed = useWorkflowStore((s) => s.setCollapsed)
  const widgets = useNodeWidgets(data.type)
  const def = registry.get(data.type)
  const title = def?.title ?? data.type
  const category = def ? CATEGORY_META[def.category] : null
  const chip = statusText(status.state, status.percent)
  const connected = new Set(connectedPorts ? connectedPorts.split('|') : [])
  const bypassed = mode === 'bypass'
  const accent = { '--node-accent': `var(${category?.token ?? '--color-border-strong'})` } as CSSProperties

  return (
    <div
      role="group"
      aria-label={`${title}, ${bypassed ? 'bypassed, ' : ''}${chip}${issueCount ? `, ${issueCount} problem${issueCount === 1 ? '' : 's'}` : ''}`}
      className={`${styles.node} ${selected ? styles.nodeSelected : ''} ${bypassed ? styles.nodeBypassed : ''}`}
      data-state={status.state}
      style={accent}
    >
      <div className={styles.nodeHeader}>
        <button
          type="button"
          className={`nodrag ${styles.collapse}`}
          aria-expanded={!collapsed}
          aria-label={collapsed ? `Expand ${title}` : `Collapse ${title}`}
          onClick={() => setCollapsed(data.id, !collapsed)}
        >
          {collapsed ? <ChevronRight size={14} aria-hidden /> : <ChevronDown size={14} aria-hidden />}
        </button>
        <span className={styles.nodeTitle}>{title}</span>
        {bypassed ? (
          <span className={styles.bypassTag} aria-hidden="true">
            Bypassed
          </span>
        ) : null}
        {def?.locked ? <span className={styles.badge}>Locked</span> : null}
        {def && !def.locked && !def.available ? <span className={styles.badge}>Unavailable</span> : null}
        {issueCount ? (
          <span className={`${styles.badge} ${styles.badgeIssue}`} aria-hidden="true">
            ! {issueCount}
          </span>
        ) : null}
        {status.state !== 'idle' && status.state !== 'bypassed' ? (
          <span className={styles.statusPill} data-state={status.state} aria-hidden="true">
            {RUN_STATE_GLYPH[status.state]} {chip}
          </span>
        ) : null}
      </div>
      {status.state === 'running' ? (
        <div className={styles.progress}>
          <ProgressBar value={status.percent} size="xs" label={`${title} progress`} />
        </div>
      ) : null}
      <div className={styles.slots}>
        <div className={styles.slotColumn}>
          {def?.inputs.map((port) => (
            <PortHandle
              key={port.id}
              side="input"
              portId={port.id}
              label={port.label}
              portType={port.accepts[0] ?? 'image_set'}
              required={port.required}
              connected={connected.has(`in:${port.id}`)}
            />
          ))}
        </div>
        <div className={styles.slotColumn}>
          {def?.outputs.map((port) => (
            <PortHandle key={port.id} side="output" portId={port.id} label={port.label} portType={port.type} connected={connected.has(`out:${port.id}`)} />
          ))}
        </div>
      </div>
      {!collapsed && widgets.length ? (
        <dl className={styles.widgets}>
          {widgets.map((w) => (
            <div key={w.label} className={styles.widget} data-empty={w.empty ? 'true' : undefined}>
              <dt className={styles.widgetLabel}>{w.label}</dt>
              <dd className={styles.widgetValue}>{w.value}</dd>
            </div>
          ))}
        </dl>
      ) : null}
      {!collapsed && status.message && (status.state === 'running' || status.state === 'failed' || status.state === 'queued') ? (
        <p className={styles.message}>{status.message}</p>
      ) : null}
    </div>
  )
}
