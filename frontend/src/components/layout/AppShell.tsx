import { Suspense, useEffect, useRef, type ReactNode } from 'react'

import type { Breakpoint } from '../../hooks/useBreakpoint'
import { useFeatures, useFeaturesStatus } from '../../hooks/useFeatures'
import { useTunerLocked } from '../../hooks/useTunerLocked'
import { effectiveSource } from '../../lib/generatePlan.ts'
import { scrollBehavior } from '../../lib/motion'
import { useInputStore } from '../../store/inputStore'
import { isTerminal, selectActiveJob, useJobStore } from '../../store/jobStore'
import { isRunTerminal, selectLatestRun, useMeshTunerStore } from '../../store/meshTunerStore'
import { useNavigationStore } from '../../store/navigationStore'
import { useUiStore, type MobileTab } from '../../store/uiStore'
import { Skeleton } from '../common/Skeleton'
import { GenerateCard } from '../generation/GenerateCard'
import { ProgressCard } from '../generation/ProgressCard'
import { HistoryList } from '../history/HistoryList'
import { InputSourceCard } from '../input/InputSourceCard'
import { LazyPartsPanel, LazyPromptPanel } from '../lazyFeatures'
import { ReferencePanel } from '../references/ReferencePanel'
import { SettingsPanel } from '../settings/SettingsPanel'
import { LazyTuningCard } from '../tunerLock/lazyTuner.ts'
import { TunerLockFrame } from '../tunerLock/TunerLockFrame'
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
    if (activeRunningId) ref.current?.scrollIntoView({ behavior: scrollBehavior(), block: 'nearest' })
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
    target?.scrollIntoView({ behavior: scrollBehavior(), block: 'nearest' })
  }, [activeRunId])
  return ref
}

/**
 * The paused Auto Tune card in today's place (UI_DESIGN §3.9): lazy (one tuner chunk, preloaded at boot) and, while
 * the backend reports `mesh_tuning.enabled=false`, inside the disabled lock frame with its notice.
 */
function TuningSlot() {
  const locked = useTunerLocked()
  return (
    <TunerLockFrame variant="card" locked={locked}>
      <Suspense fallback={null}>
        <LazyTuningCard />
      </Suspense>
    </TunerLockFrame>
  )
}

/** Placeholder of a part-aware card while its lazy chunk (or `/features`) loads; marked as an addition (UI1). */
function AdditionSkeleton({ name, height }: { name: string; height: number }) {
  return (
    <div data-ui-addition={name} aria-busy="true">
      <Skeleton shape="block" height={height} />
    </div>
  )
}

/**
 * Prompt card (P2) from the feature chunk. While `/features` is still loading, a stored prompt source shows only a
 * placeholder: a legacy backend (whose 404 is still pending) must never download the feature chunk (UI1, UI11).
 */
function PromptSlot() {
  const status = useFeaturesStatus()
  if (status === 'loading') return <AdditionSkeleton name="prompt" height={220} />
  return (
    <Suspense fallback={<AdditionSkeleton name="prompt" height={220} />}>
      <LazyPromptPanel />
    </Suspense>
  )
}

/**
 * Input column (UI_DESIGN §4.2 placements): the eager source card (it hides itself on a legacy backend) on top, then
 * the references editor, or the prompt card while the effective source is the prompt.
 */
function InputSlot({ active }: { active?: boolean }) {
  const features = useFeatures()
  const source = useInputStore((s) => s.source)
  const prompt = effectiveSource(source, features) === 'prompt'
  return (
    <>
      <InputSourceCard />
      {prompt ? <PromptSlot /> : <ReferencePanel active={active} />}
    </>
  )
}

/** Parts card (P3) after the model card, only once `/features` is ready (never on a legacy backend). */
function PartsSlot() {
  const ready = useFeaturesStatus() === 'ready'
  if (!ready) return null
  return (
    <Suspense fallback={<AdditionSkeleton name="parts" height={120} />}>
      <LazyPartsPanel />
    </Suspense>
  )
}

function DesktopLayout() {
  const progressRef = useRevealProgress()
  const tuningRef = useRevealTuning()
  return (
    <main className={styles.desktop}>
      <div className={styles.scrollColumn}>
        <InputSlot />
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
          <PartsSlot />
          <div ref={tuningRef}>
            <TuningSlot />
          </div>
        </div>
        <GenerateCard />
      </div>
    </main>
  )
}

/**
 * Two columns (landscape); in portrait (P9) the viewer spans the top and the two columns sit below it. The DOM
 * order is today's (inputs, settings, generate, viewer, cards), so only the visual placement changes.
 */
function TabletLayout() {
  const progressRef = useRevealProgress()
  const tuningRef = useRevealTuning()
  return (
    <main className={styles.tablet}>
      <div className={styles.column}>
        <div className={styles.scrollArea}>
          <InputSlot />
          <SettingsPanel />
        </div>
        <GenerateCard />
      </div>
      <div className={styles.scrollColumn}>
        <div className={styles.tabletViewer}>
          <ViewerPanel />
        </div>
        <div className={styles.tabletCards}>
          <div ref={progressRef}>
            <ProgressCard />
          </div>
          <ModelStatsCard />
          <PartsSlot />
          <div ref={tuningRef}>
            <TuningSlot />
          </div>
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

/**
 * P11: `<html data-default-bar>` tells the toast stack (and `--shell-inset-bottom`) whether the sticky generate bar
 * sits above the tab bar; removed when the phone layout unmounts.
 */
function useDefaultBarAttribute(shown: boolean) {
  useEffect(() => {
    const root = document.documentElement
    root.dataset.defaultBar = shown ? 'on' : 'off'
    return () => {
      delete root.dataset.defaultBar
    }
  }, [shown])
}

function MobileLayout() {
  const tab = useUiStore((s) => s.mobileTab)
  const showGenerate = tab === 'inputs' || tab === 'settings'
  useDefaultBarAttribute(showGenerate)
  return (
    <>
      <main className={styles.mobile}>
        <MobilePanel tab="inputs" active={tab}>
          <InputSlot active={tab === 'inputs'} />
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
          <PartsSlot />
          <TuningSlot />
        </MobilePanel>
        <MobilePanel tab="history" active={tab}>
          <section aria-label="Job history" className={styles.mobileHistory}>
            <h2 className={styles.mobileHeading}>History</h2>
            <HistoryList onOpened={() => useNavigationStore.getState().requestReveal('viewer')} />
          </section>
        </MobilePanel>
      </main>
      <div hidden={!showGenerate}>
        <GenerateCard variant="bar" />
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
