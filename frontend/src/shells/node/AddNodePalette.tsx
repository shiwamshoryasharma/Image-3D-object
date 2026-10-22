/**
 * Add-node palette (UI_DESIGN §4.3.2): grouped by category; "Show unsupported" adds the hidden non-refine types; the
 * locked refine group only with `VITE_FEATURE_REFINEMENT_PREVIEW`. A present type is disabled ("Only one … node is
 * supported."), unavailable entries carry the backend reason. Opened from a dropped connection it lists only the
 * types that can connect to it.
 */
import { useState } from 'react'

import { Dialog } from '../../components/common/Dialog'
import { REFINEMENT_PREVIEW } from '../../lib/buildFlags'
import { useWorkflowStore, type PaletteRequest } from '../../store/workflowStore'
import { presentTypes, type NodeRegistry, type NodeTypeDef, type WorkflowDoc } from '../../workflow/engine/index.ts'
import styles from './NodeShell.module.css'

function compatible(def: NodeTypeDef, request: PaletteRequest, doc: WorkflowDoc, registry: NodeRegistry): boolean {
  const from = request.connectFrom
  if (!from) return true
  if (from.side === 'output') return request.accepts === null || def.inputs.some((p) => p.accepts.includes(request.accepts!))
  const type = doc.graph.nodes.find((n) => n.id === from.node)?.type
  const accepts = (type ? registry.get(type) : undefined)?.inputs.find((p) => p.id === from.port)?.accepts ?? []
  return def.outputs.some((p) => accepts.includes(p.type))
}

export function AddNodePalette({ doc, registry }: { doc: WorkflowDoc; registry: NodeRegistry }) {
  const request = useWorkflowStore((s) => s.palette)
  const [query, setQuery] = useState('')
  const [showHidden, setShowHidden] = useState(false)
  const close = () => {
    useWorkflowStore.getState().openPalette(null)
    setQuery('')
  }
  if (!request) return null

  const needle = query.trim().toLowerCase()
  const groups = registry
    .palette({ showHidden, refinementPreview: REFINEMENT_PREVIEW, present: presentTypes(doc) })
    .map((group) => ({
      ...group,
      entries: group.entries.filter(
        (e) =>
          compatible(e.def, request, doc, registry) &&
          (!needle || e.def.title.toLowerCase().includes(needle) || e.def.description.toLowerCase().includes(needle)),
      ),
    }))
    .filter((group) => group.entries.length > 0)

  return (
    <Dialog open onClose={close} title="Add node" subtitle={request.connectFrom ? 'Showing the nodes that can connect here.' : undefined}>
      <div className={styles.palette}>
        <input
          className={styles.search}
          type="search"
          value={query}
          placeholder="Search nodes"
          aria-label="Search nodes"
          onChange={(event) => setQuery(event.target.value)}
          autoFocus
        />
        <label className={styles.checkRow}>
          <input type="checkbox" checked={showHidden} onChange={(event) => setShowHidden(event.target.checked)} /> Show unsupported
        </label>
        {groups.length === 0 ? <p className={styles.muted}>No matching node.</p> : null}
        {groups.map((group) => (
          <section key={group.category} aria-labelledby={`palette-${group.category}`}>
            <h3 id={`palette-${group.category}`} className={styles.groupTitle}>
              {group.title}
            </h3>
            <ul className={styles.paletteList}>
              {group.entries.map((entry) => (
                <li key={entry.def.type}>
                  <button
                    type="button"
                    className={styles.paletteEntry}
                    disabled={!entry.enabled}
                    aria-describedby={`palette-reason-${entry.def.type}`}
                    onClick={() => {
                      if (useWorkflowStore.getState().addNode(entry.def.type, request.position, request.connectFrom)) close()
                    }}
                  >
                    <span className={styles.paletteTitle}>{entry.def.title}</span>
                    <span id={`palette-reason-${entry.def.type}`} className={styles.muted}>
                      {entry.reason ?? entry.def.description}
                    </span>
                  </button>
                </li>
              ))}
            </ul>
          </section>
        ))}
      </div>
    </Dialog>
  )
}
