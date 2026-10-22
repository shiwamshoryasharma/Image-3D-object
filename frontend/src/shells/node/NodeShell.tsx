/**
 * Node UI shell (UI_DESIGN §4.3.13). ≥ 1024 px: toolbar over canvas (or list) + viewer dock | Inspector.
 * 600–1023 px: canvas full width, "Graph | Viewer" toggle, Inspector as a right side sheet on selection.
 * List view (`resolveNodeView`): cards + the toggle, Inspector inline in the card.
 * While mounted: `runOverride` = `runNodeWorkflow`, the write-through of the derived config and `pipelineBlock`;
 * both are cleared on unmount (UI10). The canvas is a second lazy chunk (the List view never downloads @xyflow).
 */
import { lazy, Suspense, useEffect, useId, useState, type KeyboardEvent } from 'react'

import type { ShellProps } from '../../app/shellRegistry'
import { Sheet } from '../../components/common/Sheet'
import { Skeleton } from '../../components/common/Skeleton'
import { BackendBanner } from '../../components/layout/BackendBanner'
import { ViewerPanel } from '../../components/viewer/ViewerPanel'
import { useReveal } from '../../hooks/useReveal'
import { useViewport } from '../../hooks/useViewport'
import { prefersReducedMotion } from '../../lib/motion'
import { resolveNodeView } from '../../lib/uiModes.ts'
import { useFeaturesStore } from '../../store/featuresStore'
import { useInputStore } from '../../store/inputStore'
import { selectViewedJob, useJobStore } from '../../store/jobStore'
import { useNavigationStore, type RevealTarget } from '../../store/navigationStore'
import { usePreferencesStore } from '../../store/preferencesStore'
import { useWorkflowStore } from '../../store/workflowStore'
import { nodeIdFor } from '../../workflow/engine/index.ts'
import { AddNodePalette } from './AddNodePalette'
import { startWriteThrough, useNodeRegistry, useValidationContext } from './bindings'
import { fitCanvas } from './canvasControls'
import { NodeInspector } from './NodeInspector'
import { NodeLibraryDrawer } from './NodeLibraryDrawer'
import { NodeListView } from './NodeListView'
import { handleNodeShortcut } from './nodeShortcuts'
import { graphSummaryText, NODE_TEXT } from './nodeText'
import { NodeToolbar, type MainView } from './NodeToolbar'
import { ProblemsDialog } from './ProblemsPanel'
import { runNodeWorkflow } from './runNodeWorkflow'
import { useNodeRunStates } from './useNodeRunStates'
import { useWorkflowValidation } from './useWorkflowValidation'
import { ViewerDock } from './ViewerDock'
import styles from './NodeShell.module.css'

const NodeCanvas = lazy(() => import('./canvas/NodeCanvas.tsx'))

const WIDE_MIN = 1024

function revealNode(target: RevealTarget): string | null {
  if (target === 'create') return nodeIdFor(useInputStore.getState().source === 'prompt' ? 'prompt' : 'input_image')
  if (target === 'prompt' || target === 'review') return nodeIdFor('prompt')
  if (target === 'settings') return nodeIdFor('image_to_3d')
  if (target === 'parts') return nodeIdFor('part_extraction')
  if (target === 'export') return nodeIdFor('export')
  return null
}

export function NodeShell({ nextRetryAt }: ShellProps) {
  const viewport = useViewport()
  const viewPreference = usePreferencesStore((s) => s.node.view)
  const motionReduced = usePreferencesStore((s) => s.motion === 'reduce')
  const view = resolveNodeView(viewPreference, viewport)
  const layout = view === 'list' ? 'list' : viewport.width >= WIDE_MIN ? 'wide' : 'medium'

  const registry = useNodeRegistry()
  const doc = useWorkflowStore((s) => s.doc)
  const selection = useWorkflowStore((s) => s.selection)
  const ctx = useValidationContext()
  const validation = useWorkflowValidation(doc, registry, ctx)
  const statuses = useNodeRunStates(doc, registry)
  const reducedMotion = motionReduced || prefersReducedMotion()
  const summaryId = useId()

  const [mainView, setMainView] = useState<MainView>('graph')
  const [inspectorOpen, setInspectorOpen] = useState(false)
  const [libraryOpen, setLibraryOpen] = useState(false)
  // The dock starts collapsed while there is no model to show (the canvas gets the full height, as in a node
  // editor) and opens by itself once one is viewed, until the user toggles it.
  const hasModel = useJobStore((s) => selectViewedJob(s)?.status === 'completed')
  const [dockChoice, setDockCollapsed] = useState<boolean | null>(null)
  const dockCollapsed = dockChoice ?? !hasModel

  useEffect(() => {
    const workflow = useWorkflowStore.getState()
    workflow.syncFromStores()
    const stop = startWriteThrough()
    const nav = useNavigationStore.getState()
    nav.setRunOverride(runNodeWorkflow)
    void useFeaturesStore
      .getState()
      .ensureCatalog()
      .then(() => useWorkflowStore.getState().syncFromStores())
    return () => {
      stop()
      useNavigationStore.getState().setRunOverride(null)
      useNavigationStore.getState().setPipelineBlock(null)
    }
  }, [])

  const showViewer = () => {
    setMainView('viewer')
    setDockCollapsed(false)
  }
  const openInspectorFor = (id: string | null) => {
    if (id && doc.graph.nodes.some((n) => n.id === id)) useWorkflowStore.getState().select({ kind: 'node', id })
    setMainView('graph')
    setInspectorOpen(true)
  }
  useReveal((target) => {
    if (target === 'history') setLibraryOpen(true)
    else if (target === 'viewer' || target === 'progress') showViewer()
    else openInspectorFor(revealNode(target))
  })

  let bypassed = 0
  for (const node of doc.graph.nodes) if (node.mode === 'bypass') bypassed += 1
  const runningId = Object.keys(statuses).find((id) => statuses[id]?.state === 'running') ?? null
  const runningNode = runningId ? doc.graph.nodes.find((n) => n.id === runningId) : undefined
  const summary = graphSummaryText(
    doc.graph.nodes.length,
    bypassed,
    validation.errors,
    runningNode ? { title: registry.get(runningNode.type)?.title ?? runningNode.type, percent: statuses[runningNode.id]?.percent ?? null } : null,
  )
  const issueCounts: Record<string, number> = {}
  for (const issue of validation.issues) if (issue.nodeId) issueCounts[issue.nodeId] = (issueCounts[issue.nodeId] ?? 0) + 1

  const onShortcut = (event: KeyboardEvent) =>
    handleNodeShortcut(event, {
      openPalette: () => useWorkflowStore.getState().openPalette({ accepts: null, position: null, connectFrom: null }),
      fitView: fitCanvas,
      openInspector: () => openInspectorFor(null),
    })
  const onEdit = (id: string) => {
    const current = useWorkflowStore.getState().selection
    const same = current?.kind === 'node' && current.id === id
    useWorkflowStore.getState().select(same && layout === 'list' ? null : { kind: 'node', id })
    if (layout === 'medium') setInspectorOpen(true)
  }

  const inspector = (
    <NodeInspector
      doc={doc}
      registry={registry}
      selection={selection}
      issues={validation.issues}
      statuses={statuses}
      summary={summary}
      onOpenLibrary={() => setLibraryOpen(true)}
    />
  )
  const graph =
    view === 'list' ? (
      <NodeListView
        doc={doc}
        registry={registry}
        selection={selection}
        statuses={statuses}
        issues={validation.issues}
        summary={summary}
        summaryId={summaryId}
        inlineInspector={layout === 'list'}
        onOpenLibrary={() => setLibraryOpen(true)}
        onEdit={onEdit}
      />
    ) : (
      <>
        <p id={summaryId} className={styles.srOnly}>
          {summary}
        </p>
        <Suspense fallback={<Skeleton shape="block" height="100%" />}>
          <NodeCanvas
            doc={doc}
            registry={registry}
            selection={selection}
            statuses={statuses}
            issueCounts={issueCounts}
            reducedMotion={reducedMotion}
            describedBy={summaryId}
            onNodeSelected={() => {
              if (layout === 'medium') setInspectorOpen(true)
            }}
          />
        </Suspense>
      </>
    )

  return (
    <div className={styles.shell} data-layout={layout}>
      <NodeToolbar
        errors={validation.errors}
        warnings={validation.warnings}
        view={view}
        mainView={layout === 'wide' ? null : mainView}
        onMainViewChange={setMainView}
        onOpenLibrary={() => setLibraryOpen(true)}
        summary={summary}
      />
      <BackendBanner nextRetryAt={nextRetryAt} />
      <main id="main-content" tabIndex={-1} className={styles.main} aria-label={NODE_TEXT.shellLabel}>
        {layout === 'wide' ? (
          <div className={styles.wide}>
            <div className={styles.column}>
              <section className={styles.graphArea} aria-label="Workflow graph" onKeyDown={onShortcut}>
                {graph}
              </section>
              <ViewerDock collapsed={dockCollapsed} onCollapsedChange={setDockCollapsed} />
            </div>
            <aside className={styles.inspector} aria-label="Inspector">
              {inspector}
            </aside>
          </div>
        ) : (
          <div className={styles.stack}>
            <section className={styles.graphArea} aria-label="Workflow graph" hidden={mainView !== 'graph'} onKeyDown={onShortcut}>
              {graph}
            </section>
            <section className={styles.viewerArea} aria-label="3D viewer" hidden={mainView !== 'viewer'}>
              <ViewerPanel active={mainView === 'viewer'} />
            </section>
          </div>
        )}
      </main>
      {layout === 'medium' ? (
        <Sheet
          id="node-inspector"
          side="right"
          modal={false}
          title="Inspector"
          open={inspectorOpen && selection !== null && mainView === 'graph'}
          onClose={() => setInspectorOpen(false)}
        >
          {inspector}
        </Sheet>
      ) : null}
      <AddNodePalette doc={doc} registry={registry} />
      <ProblemsDialog
        issues={validation.issues}
        doc={doc}
        registry={registry}
        onShow={(id) => openInspectorFor(id)}
      />
      <NodeLibraryDrawer open={libraryOpen} onClose={() => setLibraryOpen(false)} />
    </div>
  )
}

