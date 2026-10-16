import { useEffect, useRef, type ReactNode } from 'react'

import type { Breakpoint } from '../../hooks/useBreakpoint'
import { isTerminal, selectActiveJob, useJobStore } from '../../store/jobStore'
import { isRunTerminal, selectLatestRun, useMeshTunerStore } from '../../store/meshTunerStore'
import { useUiStore, type MobileTab } from '../../store/uiStore'
import { GenerateCard } from '../generation/GenerateCard'
import { ProgressCard } from '../generation/ProgressCard'
import { HistoryList } from '../history/HistoryList'
import { TuningCard } from '../meshTuner/TuningCard'
import { ReferencePanel } from '../references/ReferencePanel'
import { SettingsPanel } from '../settings/SettingsPanel'
import { ModelStatsCard } from '../viewer/ModelStatsCard'
import { ViewerPanel } from '../viewer/ViewerPanel'
import { MobileTabBar } from './MobileTabBar'
import { mobileTabId, mobileTabPanelId } from './mobileTabs'
import styles from './AppShell.module.css'

/** Scrolls the progress card into view when a new job becomes active. */
function useRevealProgress() {
  const ref = useRef<HTMLDivElement>(null)
  const activeJob = useJobStore(selectActiveJob)
  const activeRunningId = activeJob && !isTerminal(activeJob) ? activeJob.id : null
  useEffect(() => {
    if (activeRunningId) ref.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [activeRunningId])
  return ref
}

/** Scrolls the Auto Tune step checklist into view when a tuning run of the viewed model becomes active. */
function useRevealTuning() {
  const ref = useRef<HTMLDivElement>(null)
  const viewedJobId = useJobStore((s) => s.viewedJobId)
  const activeRunId = useMeshTunerStore((s) => {
    const run = selectLatestRun(s, viewedJobId)
    return run && !isRunTerminal(run) ? run.id : null
  })
  useEffect(() => {
    if (!activeRunId) return
    const target = ref.current?.querySelector('[data-reveal="tuning-steps"]') ?? ref.current
    target?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
  }, [activeRunId])
  return ref
}

function DesktopLayout() {
  const progressRef = useRevealProgress()
  const tuningRef = useRevealTuning()
  return (
    <main className={styles.desktop}>
      <div className={styles.scrollColumn}>
        <ReferencePanel />
      </div>
      <div className={styles.viewerColumn}>
        <ViewerPanel />
      </div>
      <div className={styles.column}>
        <div className={styles.scrollArea}>
          <SettingsPanel />
          <div ref={progressRef}>
            <ProgressCard />
          </div>
          <ModelStatsCard />
          <div ref={tuningRef}>
            <TuningCard />
          </div>
        </div>
        <GenerateCard shortcut />
      </div>
    </main>
  )
}

function TabletLayout() {
  const progressRef = useRevealProgress()
  const tuningRef = useRevealTuning()
  return (
    <main className={styles.tablet}>
      <div className={styles.column}>
        <div className={styles.scrollArea}>
          <ReferencePanel />
          <SettingsPanel />
        </div>
        <GenerateCard shortcut />
      </div>
      <div className={styles.scrollColumn}>
        <div className={styles.tabletViewer}>
          <ViewerPanel />
        </div>
        <div ref={progressRef}>
          <ProgressCard />
        </div>
        <ModelStatsCard />
        <div ref={tuningRef}>
          <TuningCard />
        </div>
      </div>
    </main>
  )
}

function MobilePanel({ tab, active, children }: { tab: MobileTab; active: MobileTab; children: ReactNode }) {
  return (
    <div
      id={mobileTabPanelId(tab)}
      role="tabpanel"
      aria-labelledby={mobileTabId(tab)}
      hidden={tab !== active}
      className={styles.mobilePanel}
    >
      {children}
    </div>
  )
}

function MobileLayout() {
  const tab = useUiStore((s) => s.mobileTab)
  const showGenerate = tab === 'inputs' || tab === 'settings'
  return (
    <>
      <main className={styles.mobile}>
        <MobilePanel tab="inputs" active={tab}>
          <ReferencePanel active={tab === 'inputs'} />
        </MobilePanel>
        <MobilePanel tab="settings" active={tab}>
          <SettingsPanel />
        </MobilePanel>
        <MobilePanel tab="viewer" active={tab}>
          <div className={styles.mobileViewer}>
            <ViewerPanel active={tab === 'viewer'} />
          </div>
          <ProgressCard />
          <ModelStatsCard />
          <TuningCard />
        </MobilePanel>
        <MobilePanel tab="history" active={tab}>
          <section aria-label="Job history" className={styles.mobileHistory}>
            <h2 className={styles.mobileHeading}>History</h2>
            <HistoryList onOpened={() => useUiStore.getState().setMobileTab('viewer')} />
          </section>
        </MobilePanel>
      </main>
      <div hidden={!showGenerate}>
        <GenerateCard shortcut variant="bar" focusViewerOnSubmit />
      </div>
      <MobileTabBar />
    </>
  )
}

/** Responsive application layout: desktop 3 columns, tablet 2 columns, mobile tabs. */
export function AppShell({ breakpoint }: { breakpoint: Breakpoint }) {
  if (breakpoint === 'desktop') return <DesktopLayout />
  if (breakpoint === 'tablet') return <TabletLayout />
  return <MobileLayout />
}
