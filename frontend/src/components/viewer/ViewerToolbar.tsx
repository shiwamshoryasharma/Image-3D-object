import { Camera, Focus, Grid3x3, Layers, Maximize, Minimize, Palette, Puzzle, Rotate3d, SunDim, Sun, Triangle } from 'lucide-react'

import { analyzePartsForViewedJob } from '../../actions/parts'
import { useFeatures } from '../../hooks/useFeatures'
import { isTerminalRunStatus } from '../../lib/runSteps.ts'
import { useJobStore } from '../../store/jobStore'
import { usePartStore } from '../../store/partStore'
import { useUiStore, type ViewMode } from '../../store/uiStore'
import { Button, IconButton } from '../common/Button'
import { SegmentedControl } from '../common/SegmentedControl'
import styles from './ViewerToolbar.module.css'

const VIEW_MODES: Array<{ value: ViewMode; label: string; title: string }> = [
  { value: 'pbr', label: 'PBR', title: 'Original textured materials' },
  { value: 'clay', label: 'Clay', title: 'Neutral clay material to inspect geometry' },
  { value: 'normals', label: 'Normals', title: 'Surface normals' },
]

export interface ViewerToolbarProps {
  disabled: boolean
  isFullscreen: boolean
  fullscreenSupported: boolean
  onResetCamera: () => void
  onScreenshot: () => void
  onToggleFullscreen: () => void
  /** Parts in the viewed job's ready part set; `null` = no part set (no parts toggles, UI_DESIGN §6.6). */
  partsCount?: number | null
}

/** "Parts" and (while the parts view is on) "Colorize" toggles: a marked part-aware addition (§4.2). */
function PartsToggles({ disabled }: { disabled: boolean }) {
  const show = usePartStore((s) => s.view.show)
  const colorize = usePartStore((s) => s.view.colorize)
  const setShow = usePartStore((s) => s.setShow)
  const setColorize = usePartStore((s) => s.setColorize)
  return (
    <span className={styles.partsGroup} data-ui-addition>
      <IconButton
        label="Parts view"
        icon={<Layers size={16} />}
        variant="solid"
        active={show}
        aria-pressed={show}
        disabled={disabled}
        onClick={() => setShow(!show)}
      />
      {show && (
        <IconButton
          label="Colorize parts"
          icon={<Palette size={16} />}
          variant="solid"
          active={colorize}
          aria-pressed={colorize}
          disabled={disabled}
          onClick={() => setColorize(!colorize)}
        />
      )}
    </span>
  )
}

/**
 * "Split into parts" while the viewed, finished model has no part set yet (a marked part-aware addition, §4.2): the
 * same command as the Parts card's "Analyse parts", one click from the model. Hidden when the backend cannot analyse
 * finished models (legacy backends included).
 */
function SplitPartsButton({ disabled }: { disabled: boolean }) {
  const { parts } = useFeatures()
  const jobId = useJobStore((s) => s.viewedJobId)
  const busy = usePartStore((s) => {
    if (!jobId) return false
    const latest = s.byJob[jobId]?.analyses[0]
    return s.requestingJobIds.includes(jobId) || (latest !== undefined && !isTerminalRunStatus(latest.status))
  })
  if (!parts.available || !parts.rebuild) return null
  return (
    <span className={styles.partsGroup} data-ui-addition>
      <Button
        size="sm"
        variant="secondary"
        icon={<Puzzle size={14} />}
        loading={busy}
        disabled={disabled || busy}
        onClick={() => void analyzePartsForViewedJob()}
      >
        {busy ? 'Splitting…' : 'Split into parts'}
      </Button>
    </span>
  )
}

/** Overlay controls for the viewer: view mode, display toggles and camera/screenshot/fullscreen actions. */
export function ViewerToolbar({
  disabled,
  isFullscreen,
  fullscreenSupported,
  onResetCamera,
  onScreenshot,
  onToggleFullscreen,
  partsCount = null,
}: ViewerToolbarProps) {
  const options = useUiStore((s) => s.viewer)
  const setOption = useUiStore((s) => s.setViewerOption)

  const toggles = [
    { key: 'wireframe', label: 'Wireframe overlay', icon: <Triangle size={16} /> },
    { key: 'grid', label: 'Grid', icon: <Grid3x3 size={16} /> },
    { key: 'shadows', label: 'Contact shadows', icon: <SunDim size={16} /> },
    { key: 'environment', label: 'Studio lighting', icon: <Sun size={16} /> },
    { key: 'autoRotate', label: 'Auto-rotate', icon: <Rotate3d size={16} /> },
  ] as const

  return (
    <>
      <div className={styles.top}>
        <div className={styles.modes}>
          <SegmentedControl<ViewMode>
            label="View mode"
            size="sm"
            value={options.viewMode}
            options={VIEW_MODES}
            onChange={(mode) => setOption('viewMode', mode)}
            disabled={disabled}
          />
        </div>
        <div className={styles.group} role="toolbar" aria-label="Viewer actions">
          <IconButton label="Reset camera" icon={<Focus size={16} />} variant="solid" disabled={disabled} onClick={onResetCamera} />
          <IconButton label="Save screenshot (PNG)" icon={<Camera size={16} />} variant="solid" disabled={disabled} onClick={onScreenshot} />
          {fullscreenSupported && (
            <IconButton
              label={isFullscreen ? 'Exit fullscreen' : 'Fullscreen'}
              icon={isFullscreen ? <Minimize size={16} /> : <Maximize size={16} />}
              variant="solid"
              onClick={onToggleFullscreen}
            />
          )}
        </div>
      </div>
      <div className={styles.bottom} role="toolbar" aria-label="Display options">
        {toggles.map((toggle) => (
          <IconButton
            key={toggle.key}
            label={toggle.label}
            icon={toggle.icon}
            variant="solid"
            active={options[toggle.key]}
            aria-pressed={options[toggle.key]}
            disabled={disabled}
            onClick={() => setOption(toggle.key, !options[toggle.key])}
          />
        ))}
        {partsCount !== null ? <PartsToggles disabled={disabled} /> : <SplitPartsButton disabled={disabled} />}
      </div>
    </>
  )
}
