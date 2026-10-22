import { useNavigationStore } from '../../store/navigationStore'
import { usePreferencesStore } from '../../store/preferencesStore'
import { SegmentedControl } from '../common/SegmentedControl'
import { Switch } from '../common/Switch'
import { LINK_STYLE_OPTIONS, NODE_VIEW_OPTIONS } from './preferencesText'
import styles from './preferences.module.css'

/** Node editor preferences (UI_DESIGN §3.8); stored now, used by the Node UI. */
export function NodeEditorPrefs() {
  const node = usePreferencesStore((s) => s.node)
  const setNodePreference = usePreferencesStore((s) => s.setNodePreference)
  const nodeIncluded = useNavigationStore((s) => s.readyModes.has('node'))
  return (
    <div className={styles.fields}>
      {!nodeIncluded && <p className={styles.hint}>Used by the Node UI, which is not in this build yet.</p>}
      <div className={styles.field}>
        <h4 className={styles.fieldLabel}>Default view</h4>
        <SegmentedControl
          label="Default view"
          value={node.view}
          options={NODE_VIEW_OPTIONS}
          onChange={(value) => setNodePreference('view', value)}
        />
      </div>
      <Switch checked={node.minimap} onChange={(value) => setNodePreference('minimap', value)} label="Minimap" />
      <Switch
        checked={node.snapToGrid}
        onChange={(value) => setNodePreference('snapToGrid', value)}
        label="Snap to grid"
      />
      <div className={styles.field}>
        <h4 className={styles.fieldLabel}>Link style</h4>
        <SegmentedControl
          label="Link style"
          value={node.linkStyle}
          options={LINK_STYLE_OPTIONS}
          onChange={(value) => setNodePreference('linkStyle', value)}
        />
      </div>
    </div>
  )
}
