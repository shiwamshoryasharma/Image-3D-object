import { useEffect } from 'react'

import { useResolvedUiMode } from '../../hooks/useResolvedUiMode'
import { STORAGE_KEYS } from '../../lib/storage'
import { UI_MODE_META } from '../../lib/uiModes.ts'
import { useFeaturesStore } from '../../store/featuresStore'
import { useNavigationStore } from '../../store/navigationStore'
import { useSystemStore } from '../../store/systemStore'
import { DensityPicker } from './DensityPicker'
import { FeatureStatusList } from './FeatureStatusList'
import { ModelRegistryList } from './ModelRegistryList'
import { MotionPicker } from './MotionPicker'
import { NodeEditorPrefs } from './NodeEditorPrefs'
import { KEYBOARD_SHORTCUTS, SECTION_DOM_IDS, type ModeActions } from './preferencesText'
import { UiModePicker } from './UiModePicker'
import styles from './preferences.module.css'

function About() {
  const app = useSystemStore((s) => s.system?.app ?? null)
  const featuresStatus = useFeaturesStore((s) => s.status)
  const apiVersion = useFeaturesStore((s) => s.snapshot.apiVersion)
  const mode = useResolvedUiMode()
  let backend = apiVersion ?? 'unknown'
  if (featuresStatus === 'legacy') backend = 'legacy'
  else if (featuresStatus === 'loading') backend = 'checking…'
  return (
    <>
      <dl className={styles.facts}>
        <dt>App</dt>
        <dd>{app ? `${app.name} ${app.version}` : 'Unknown until connected'}</dd>
        <dt>Backend API</dt>
        <dd>{backend}</dd>
        <dt>UI style</dt>
        <dd>{UI_MODE_META[mode].name}</dd>
      </dl>
      <p className={styles.hint}>
        Preferences are kept in this browser ({STORAGE_KEYS.preferences}); clearing the site data resets them.
      </p>
      <h4 className={styles.fieldLabel}>Keyboard shortcuts</h4>
      <dl className={styles.shortcuts}>
        {KEYBOARD_SHORTCUTS.map((shortcut) => (
          <div key={shortcut.keys} className={styles.shortcut}>
            <dt>
              <kbd>{shortcut.keys}</kbd>
            </dt>
            <dd>{shortcut.action}</dd>
          </div>
        ))}
      </dl>
    </>
  )
}

/** The lazily loaded App settings content (UI_DESIGN §3.8); opens scrolled to the requested section. */
export function AppSettingsBody({ prepareMode, prefetchMode }: ModeActions) {
  const section = useNavigationStore((s) => s.appSettingsSection)

  useEffect(() => {
    if (section === 'interface') return
    document.getElementById(SECTION_DOM_IDS[section])?.scrollIntoView({ block: 'start' })
  }, [section])

  return (
    <div className={styles.body}>
      <section id={SECTION_DOM_IDS.interface} aria-labelledby={`${SECTION_DOM_IDS.interface}-title`} className={styles.section}>
        <h3 id={`${SECTION_DOM_IDS.interface}-title`} className={styles.sectionTitle}>
          Interface
        </h3>
        <UiModePicker prepareMode={prepareMode} prefetchMode={prefetchMode} />
        <DensityPicker />
        <MotionPicker />
        <p className={styles.hint}>Saved on this device.</p>
      </section>
      <section id={SECTION_DOM_IDS.node} aria-labelledby={`${SECTION_DOM_IDS.node}-title`} className={styles.section}>
        <h3 id={`${SECTION_DOM_IDS.node}-title`} className={styles.sectionTitle}>
          Node editor
        </h3>
        <NodeEditorPrefs />
      </section>
      <section id={SECTION_DOM_IDS.features} aria-labelledby={`${SECTION_DOM_IDS.features}-title`} className={styles.section}>
        <h3 id={`${SECTION_DOM_IDS.features}-title`} className={styles.sectionTitle}>
          Features &amp; models
        </h3>
        <FeatureStatusList />
        <ModelRegistryList />
      </section>
      <section id={SECTION_DOM_IDS.about} aria-labelledby={`${SECTION_DOM_IDS.about}-title`} className={styles.section}>
        <h3 id={`${SECTION_DOM_IDS.about}-title`} className={styles.sectionTitle}>
          About
        </h3>
        <About />
      </section>
    </div>
  )
}
