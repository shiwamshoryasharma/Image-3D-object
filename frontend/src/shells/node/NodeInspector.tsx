/**
 * Inspector (UI_DESIGN §4.3.13): the same Content components as the other modes, so every mode edits the same form.
 * Input Image → ReferenceContent; Prompt → PromptContent + PromptReviewContent; Existing Result → viewed job +
 * "Pick from Library"; Segmentation → SegmentationOptionsContent + SegmentationPreview; Image-to-3D →
 * SettingsContent (no part options); Part Extraction → PartOptionsSection `parts` + PartsContent; Export →
 * ExportContent; locked → explanation only; nothing selected → progress, problems and the graph summary.
 */
import { Suspense, type ReactNode } from 'react'
import { Trash, Unplug } from 'lucide-react'

import { analyzePartsForViewedJob } from '../../actions/parts'
import { Button } from '../../components/common/Button'
import { Switch } from '../../components/common/Switch'
import { ProgressContent } from '../../components/generation/ProgressContent'
import {
  LazyExportContent,
  LazyPartOptionsSection,
  LazyPartsContent,
  LazyPromptContent,
  LazyPromptReviewContent,
  LazySegmentationOptionsContent,
  LazySegmentationPreview,
} from '../../components/lazyFeatures'
import { ReferenceContent } from '../../components/references/ReferenceContent'
import { SettingsContent } from '../../components/settings/SettingsContent'
import { useFeaturesStatus } from '../../hooks/useFeatures'
import { selectViewedJob, useJobStore } from '../../store/jobStore'
import { useWorkflowStore, type WorkflowSelection } from '../../store/workflowStore'
import { edgeKey, type GraphIssue, type NodeRegistry, type NodeStatusView, type WorkflowDoc } from '../../workflow/engine/index.ts'
import { NODE_TEXT, statusText } from './nodeText'
import { ProblemsPanel } from './ProblemsPanel'
import styles from './NodeShell.module.css'

export interface NodeInspectorProps {
  doc: WorkflowDoc
  registry: NodeRegistry
  selection: WorkflowSelection | null
  issues: readonly GraphIssue[]
  statuses: Readonly<Record<string, NodeStatusView>>
  summary: string
  onOpenLibrary: () => void
  /** Hide the node header (the List view card shows it). */
  bare?: boolean
}

function Lazy({ children }: { children: ReactNode }) {
  return <Suspense fallback={<p className={styles.muted}>Loading…</p>}>{children}</Suspense>
}

function NodeBody({ type, onOpenLibrary }: { type: string; onOpenLibrary: () => void }) {
  const featuresReady = useFeaturesStatus() === 'ready'
  const viewed = useJobStore(selectViewedJob)
  const viewedDone = viewed?.status === 'completed'
  const needsFeatures = !['input_image', 'image_to_3d', 'job_result'].includes(type)
  if (needsFeatures && !featuresReady) return <p className={styles.muted}>{NODE_TEXT.needsBackend}.</p>
  switch (type) {
    case 'input_image':
      return <ReferenceContent active headingLevel={3} compact />
    case 'prompt':
      return (
        <Lazy>
          <LazyPromptContent headingLevel={3} compact />
          <LazyPromptReviewContent headingLevel={3} compact />
        </Lazy>
      )
    case 'job_result':
      return (
        <div className={styles.stackSm}>
          <p>{viewedDone ? `Uses the viewed model${viewed?.id ? ` (${viewed.id})` : ''}.` : 'Open a finished model first.'}</p>
          <Button size="sm" onClick={onOpenLibrary}>
            Pick from Library
          </Button>
        </div>
      )
    case 'segmentation':
      return (
        <Lazy>
          <LazySegmentationOptionsContent headingLevel={3} compact />
          <LazySegmentationPreview headingLevel={3} compact />
        </Lazy>
      )
    case 'image_to_3d':
      return <SettingsContent showPartOptions={false} />
    case 'part_extraction':
      return (
        <Lazy>
          <LazyPartOptionsSection mode="parts" headingLevel={3} compact />
          {viewedDone ? (
            <Button size="sm" onClick={() => void analyzePartsForViewedJob()}>
              Run from here on the viewed model
            </Button>
          ) : null}
          <LazyPartsContent headingLevel={3} compact />
        </Lazy>
      )
    case 'export':
      return (
        <Lazy>
          <LazyExportContent headingLevel={3} />
        </Lazy>
      )
    default:
      return null
  }
}

export function NodeInspector({ doc, registry, selection, issues, statuses, summary, onOpenLibrary, bare = false }: NodeInspectorProps) {
  const store = useWorkflowStore.getState
  if (!selection) {
    return (
      <div className={styles.inspectorBody}>
        <h2 className={styles.inspectorTitle}>Workflow</h2>
        <p className={styles.muted}>{summary}</p>
        <ProgressContent headingLevel={3} compact />
        <h3 className={styles.groupTitle}>Problems</h3>
        <ProblemsPanel issues={issues} doc={doc} registry={registry} onShow={(id) => store().select({ kind: 'node', id })} />
        <p className={styles.muted}>{NODE_TEXT.noSelection}</p>
      </div>
    )
  }

  if (selection.kind === 'edge') {
    const edge = doc.graph.edges.find((e) => edgeKey(e) === selection.id)
    const title = (id: string) => registry.get(doc.graph.nodes.find((n) => n.id === id)?.type ?? '')?.title ?? id
    return (
      <div className={styles.inspectorBody}>
        <h2 className={styles.inspectorTitle}>Link</h2>
        {edge ? (
          <p>
            {title(edge.from.node)} · {edge.from.port} → {title(edge.to.node)} · {edge.to.port}
          </p>
        ) : null}
        <Button size="sm" variant="danger" icon={<Unplug size={16} aria-hidden />} onClick={() => store().disconnect(selection.id)}>
          Remove link
        </Button>
      </div>
    )
  }

  const node = doc.graph.nodes.find((n) => n.id === selection.id)
  const def = node ? registry.get(node.type) : undefined
  if (!node) return null
  const status = statuses[node.id]
  const nodeIssues = issues.filter((i) => i.nodeId === node.id)
  const parked = nodeIssues.some((i) => i.code === 'ui_parked_source')
  const canBypass = !!def && def.bypass !== null && !def.locked

  return (
    <div className={styles.inspectorBody}>
      {!bare && (
        <>
          <h2 className={styles.inspectorTitle}>{def?.title ?? node.type}</h2>
          {def?.description ? <p className={styles.muted}>{def.description}</p> : null}
          {status ? <p className={styles.muted}>Status: {statusText(status.state, status.percent)}</p> : null}
        </>
      )}
      {canBypass && !bare ? (
        <Switch
          label="Active"
          description={node.mode === 'bypass' ? 'Bypassed: skipped when the workflow runs.' : undefined}
          checked={node.mode === 'active'}
          onChange={(on) => store().setMode(node.id, on ? 'active' : 'bypass')}
        />
      ) : null}
      {nodeIssues.length ? (
        <ul className={styles.problems} aria-label={`Problems of ${def?.title ?? node.type}`}>
          {nodeIssues.map((issue, index) => (
            <li key={`${issue.code}:${index}`} className={styles.problem} data-severity={issue.severity}>
              <span className={styles.problemText}>{issue.message}</span>
            </li>
          ))}
        </ul>
      ) : null}
      {parked && def?.isSource ? (
        <Button size="sm" onClick={() => store().switchSource(node.id)}>
          Use this source
        </Button>
      ) : null}
      {def?.locked ? (
        <p>{NODE_TEXT.lockedNode}</p>
      ) : def && !def.available ? (
        <p className={styles.muted}>{def.reason ?? NODE_TEXT.needsBackend}</p>
      ) : (
        <NodeBody type={node.type} onOpenLibrary={onOpenLibrary} />
      )}
      {!bare && (
        <Button size="sm" variant="ghost" icon={<Trash size={16} aria-hidden />} onClick={() => store().removeNodes([node.id])}>
          Remove node
        </Button>
      )}
    </div>
  )
}
