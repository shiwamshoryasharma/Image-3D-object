/**
 * Node toolbar (UI_DESIGN §4.3.13, 48 px): Run/Cancel with the issue count (`GenerateAction variant="toolbar"`),
 * + Add, Undo, Redo, Fit, Problems, the Workflow menu (templates, Tidy, Import, Export JSON), Canvas | List,
 * Graph | Viewer (below 1024 px), Library and App settings.
 */
import { useRef, type ChangeEvent } from 'react'
import { Ellipsis, Library, Maximize2, Plus, Redo2, TriangleAlert, Undo2 } from 'lucide-react'

import { Button, IconButton } from '../../components/common/Button'
import { Menu, type MenuSection } from '../../components/common/Menu'
import { SegmentedControl } from '../../components/common/SegmentedControl'
import { GenerateAction } from '../../components/generation/GenerateAction'
import { useFeaturesStore } from '../../store/featuresStore'
import { usePreferencesStore } from '../../store/preferencesStore'
import { toast } from '../../store/toastStore'
import { useWorkflowStore } from '../../store/workflowStore'
import { WORKFLOW_FILE_SUFFIX } from '../../workflow/engine/index.ts'
import { AppSettingsButton } from '../shared/AppSettingsButton'
import { fitCanvas } from './canvasControls'
import { NODE_TEXT } from './nodeText'
import styles from './NodeShell.module.css'

export type MainView = 'graph' | 'viewer'

export interface NodeToolbarProps {
  errors: number
  warnings: number
  view: 'canvas' | 'list'
  /** Shown below 1024 px and in the List view (the viewer swaps with the graph). */
  mainView: MainView | null
  onMainViewChange: (view: MainView) => void
  onOpenLibrary: () => void
  summary: string
}

const VIEW_OPTIONS = [
  { value: 'canvas', label: NODE_TEXT.canvas },
  { value: 'list', label: NODE_TEXT.list },
] as const
const MAIN_OPTIONS = [
  { value: 'graph', label: NODE_TEXT.graphView },
  { value: 'viewer', label: NODE_TEXT.viewerView },
] as const

function download(text: string): void {
  const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }))
  const link = document.createElement('a')
  link.href = url
  link.download = `workflow${WORKFLOW_FILE_SUFFIX}`
  link.click()
  window.setTimeout(() => URL.revokeObjectURL(url), 0)
}

export function NodeToolbar({ errors, warnings, view, mainView, onMainViewChange, onOpenLibrary, summary }: NodeToolbarProps) {
  const canUndo = useWorkflowStore((s) => s.past.length > 0)
  const canRedo = useWorkflowStore((s) => s.future.length > 0)
  const templates = useFeaturesStore((s) => s.catalog?.templates)
  const setNodePreference = usePreferencesStore((s) => s.setNodePreference)
  const fileInput = useRef<HTMLInputElement | null>(null)

  const store = useWorkflowStore.getState
  const onImport = async (event: ChangeEvent<HTMLInputElement>) => {
    const file = event.target.files?.[0]
    event.target.value = ''
    if (!file) return
    const result = store().importFile(await file.text())
    if (result.ok) toast.success(NODE_TEXT.imported, result.issues.length ? `${result.issues.length} problem(s) to review.` : undefined)
    else toast.error(NODE_TEXT.importFailed, result.message)
  }

  const sections: MenuSection[] = [
    {
      title: 'Templates',
      items: [
        { id: 'current', label: 'Default for current settings', onSelect: () => void store().loadTemplate('current') },
        ...(templates ?? []).map((t) => ({ id: `t:${t.id}`, label: t.title, onSelect: () => void store().loadTemplate(t.id) })),
      ],
    },
    {
      title: 'Workflow',
      items: [
        { id: 'tidy', label: 'Tidy layout', onSelect: () => store().tidy() },
        { id: 'import', label: 'Import…', onSelect: () => fileInput.current?.click() },
        { id: 'export', label: 'Export JSON', onSelect: () => download(store().exportFile()) },
      ],
    },
  ]

  return (
    <header className={styles.toolbar} aria-label="Workflow toolbar">
      <GenerateAction variant="toolbar" issueCount={errors} />
      <div className={styles.toolGroup} role="group" aria-label="Edit">
        <Button size="sm" icon={<Plus size={16} aria-hidden />} onClick={() => store().openPalette({ accepts: null, position: null, connectFrom: null })}>
          Add
        </Button>
        <IconButton size="sm" label="Undo (Ctrl+Z)" icon={<Undo2 size={16} />} disabled={!canUndo} onClick={() => store().undo()} />
        <IconButton size="sm" label="Redo (Ctrl+Y)" icon={<Redo2 size={16} />} disabled={!canRedo} onClick={() => store().redo()} />
        {view === 'canvas' ? <IconButton size="sm" label="Fit view (.)" icon={<Maximize2 size={16} />} onClick={fitCanvas} /> : null}
        <Button
          size="sm"
          variant={errors ? 'danger' : 'ghost'}
          icon={<TriangleAlert size={16} aria-hidden />}
          aria-label={`Problems: ${errors} error${errors === 1 ? '' : 's'}, ${warnings} warning${warnings === 1 ? '' : 's'}`}
          onClick={() => store().setProblemsOpen(true)}
        >
          {errors + warnings}
        </Button>
        <Menu label="Workflow menu" icon={<Ellipsis size={16} />} sections={sections} triggerVariant="ghost" />
        <input ref={fileInput} type="file" accept=".json,application/json" hidden onChange={onImport} />
      </div>
      <p className={styles.toolbarSummary} aria-live="off">
        {summary}
      </p>
      <div className={styles.toolGroup}>
        <SegmentedControl size="sm" label="Graph view" value={view} options={VIEW_OPTIONS} onChange={(v) => setNodePreference('view', v)} />
        {mainView ? <SegmentedControl size="sm" label="Main area" value={mainView} options={MAIN_OPTIONS} onChange={onMainViewChange} /> : null}
        <IconButton size="sm" label="Library" icon={<Library size={16} />} onClick={onOpenLibrary} />
        <AppSettingsButton />
      </div>
    </header>
  )
}
