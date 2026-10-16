import { Camera, Focus, Grid3x3, Maximize, Minimize, Rotate3d, SunDim, Sun, Triangle } from 'lucide-react'

import { useUiStore, type ViewMode } from '../../store/uiStore'
import { IconButton } from '../common/Button'
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
}

/** Overlay controls for the viewer: view mode, display toggles and camera/screenshot/fullscreen actions. */
export function ViewerToolbar({
  disabled,
  isFullscreen,
  fullscreenSupported,
  onResetCamera,
  onScreenshot,
  onToggleFullscreen,
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
      </div>
    </>
  )
}
