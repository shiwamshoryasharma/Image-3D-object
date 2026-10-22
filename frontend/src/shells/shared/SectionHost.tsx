import { Suspense, useId, type KeyboardEvent } from 'react'

import { Skeleton } from '../../components/common/Skeleton'
import { HistoryList } from '../../components/history/HistoryList'
import { InputContent } from '../../components/input/InputContent'
import { LazyExportContent, LazyPartsContent } from '../../components/lazyFeatures'
import { SettingsContent } from '../../components/settings/SettingsContent'
import { ModelStatsContent } from '../../components/viewer/ModelStatsContent'
import { useNavigationStore, type CreateTab, type ShellSection } from '../../store/navigationStore'
import styles from './Shell.module.css'

export interface SectionHostProps {
  /** `viewer` renders nothing (it collapses the panel / closes the sheet). */
  section: ShellSection
  /** The host sheet or panel is open (owns the reference paste listener). Default true. */
  active?: boolean
  /** Level of the content's own headings under the host's title. Default 3. */
  headingLevel?: 2 | 3
}

const CREATE_TABS: readonly { id: CreateTab; label: string }[] = [
  { id: 'input', label: 'Input' },
  { id: 'settings', label: 'Settings' },
]

const loading = (
  <div aria-busy="true" className={styles.sectionFallback}>
    <Skeleton shape="block" height={160} />
  </div>
)

/** Create = tabs Input | Settings over `navigationStore.createTab` (Left/Right/Home/End move between the tabs). */
function CreateSection({ active }: { active: boolean }) {
  const tab = useNavigationStore((s) => s.createTab)
  const setCreateTab = useNavigationStore((s) => s.setCreateTab)
  const baseId = useId()
  const tabId = (id: CreateTab) => `${baseId}-tab-${id}`
  const panelId = `${baseId}-panel`

  const onKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    const index = CREATE_TABS.findIndex((item) => item.id === tab)
    let next = -1
    if (event.key === 'ArrowRight') next = (index + 1) % CREATE_TABS.length
    else if (event.key === 'ArrowLeft') next = (index - 1 + CREATE_TABS.length) % CREATE_TABS.length
    else if (event.key === 'Home') next = 0
    else if (event.key === 'End') next = CREATE_TABS.length - 1
    if (next < 0) return
    event.preventDefault()
    const target = CREATE_TABS[next].id
    setCreateTab(target)
    document.getElementById(tabId(target))?.focus()
  }

  return (
    <div className={styles.section}>
      <div role="tablist" aria-label="Create" className={styles.tabs} onKeyDown={onKeyDown}>
        {CREATE_TABS.map((item) => (
          <button
            key={item.id}
            id={tabId(item.id)}
            type="button"
            role="tab"
            className={styles.tab}
            aria-selected={tab === item.id}
            aria-controls={panelId}
            tabIndex={tab === item.id ? 0 : -1}
            onClick={() => setCreateTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </div>
      <div id={panelId} role="tabpanel" aria-labelledby={tabId(tab)} className={styles.tabPanel}>
        {tab === 'input' ? <InputContent active={active} /> : <SettingsContent />}
      </div>
    </div>
  )
}

/** Parts = `PartsContent` + "Model" (`ModelStatsContent`) and "Export" (`ExportContent`) disclosures. */
function PartsSection({ headingLevel }: { headingLevel: 2 | 3 }) {
  return (
    <div className={styles.section}>
      <Suspense fallback={loading}>
        <LazyPartsContent headingLevel={headingLevel} />
      </Suspense>
      <details className={styles.disclosure}>
        <summary>Model</summary>
        <ModelStatsContent headingLevel={headingLevel} />
      </details>
      <details className={styles.disclosure}>
        <summary>Export</summary>
        <Suspense fallback={loading}>
          <LazyExportContent headingLevel={headingLevel} />
        </Suspense>
      </details>
    </div>
  )
}

/**
 * Section content for the Tablet side panel, the Tablet bottom section sheet and the Mobile sheets (UI_DESIGN §4.5.2):
 * the same content in every host. The host provides the title (`FEATURE_META`) and, in sheets, the sticky Generate
 * footer.
 */
export function SectionHost({ section, active = true, headingLevel = 3 }: SectionHostProps) {
  if (section === 'create') return <CreateSection active={active} />
  if (section === 'parts') return <PartsSection headingLevel={headingLevel} />
  if (section === 'library') {
    return (
      <div className={styles.section}>
        <HistoryList onOpened={() => useNavigationStore.getState().requestReveal('viewer')} />
      </div>
    )
  }
  return null
}
