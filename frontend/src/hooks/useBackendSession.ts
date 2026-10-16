/**
 * Wires the API client to the stores and drives the backend session:
 *  1. registers reachability / 401 hooks on the axios client;
 *  2. checks `/auth/status`, retrying with backoff while the backend is unreachable;
 *  3. once authenticated (or auth disabled): loads system status, the reference set and job history, and
 *     opens the realtime WebSocket (with REST polling fallback);
 *  4. on reconnect after an outage: re-syncs everything.
 * Part-aware milestone (UI_DESIGN §2.12): backend features, run index, parts of the viewed job and the five new WS
 * events. The Auto Mesh Tuner wiring is unchanged and makes GET requests only (runtime status, runs of the viewed
 * job), locked or not: tuner mutations are reachable only from the (locked) tuner components (§3.9).
 */
import { useEffect, useState } from 'react'

import { configureClientHooks, getAuthToken } from '../api/client'
import { RealtimeClient, type PollMode } from '../api/ws'
import { useAuthStore } from '../store/authStore'
import { useFeaturesStore } from '../store/featuresStore'
import { isTerminal, useJobStore } from '../store/jobStore'
import { isRunTerminal, useMeshTunerStore } from '../store/meshTunerStore'
import { usePartStore } from '../store/partStore'
import { useReferenceStore } from '../store/referenceStore'
import { useRunStore } from '../store/runStore'
import { useSettingsStore } from '../store/settingsStore'
import { useSystemStore } from '../store/systemStore'

const AUTH_RETRY_DELAYS_MS = [1_500, 2_500, 4_000, 6_000, 8_000]

let hooksConfigured = false

function ensureClientHooks(): void {
  if (hooksConfigured) return
  hooksConfigured = true
  configureClientHooks({
    onReachability: (reachable, error) => useSystemStore.getState().setReachable(reachable, error),
    onUnauthorized: () => {
      if (useAuthStore.getState().phase === 'ready') useAuthStore.getState().expire()
    },
  })
}

/** REST poll of the Auto Mesh Tuner state: active runs always, the Blender runtime while it changes (or safety). */
async function pollMeshTuner(mode: PollMode): Promise<void> {
  const tuner = useMeshTunerStore.getState()
  const activeRuns = Object.values(tuner.runs).filter((run) => !isRunTerminal(run))
  const runtimeBusy = tuner.runtime?.state === 'downloading' || tuner.runtime?.state === 'detecting'
  await Promise.all([
    ...activeRuns.map((run) => tuner.refreshRun(run.id)),
    mode === 'safety' || runtimeBusy || !tuner.runtime ? tuner.refreshRuntime() : Promise.resolve(true),
  ])
}

/** REST poll used by the realtime client as fallback (socket down) and safety net (socket up). */
async function pollBackend(mode: PollMode): Promise<boolean> {
  const ok = await useSystemStore.getState().refresh()
  if (!ok) return false
  const { activeJobId, jobs } = useJobStore.getState()
  const active = activeJobId ? jobs[activeJobId] : undefined
  if (active && !isTerminal(active)) await useJobStore.getState().refreshJob(active.id)
  await pollMeshTuner(mode)
  if (mode === 'fallback') {
    await Promise.all([useRunStore.getState().resyncActive(), usePartStore.getState().refreshActive()])
  }
  if (mode === 'safety') {
    await Promise.all([useJobStore.getState().loadHistory(), useFeaturesStore.getState().refresh()])
  }
  return true
}

/** Load all session data (idempotent; used at start and after reconnecting). */
async function syncSessionData(): Promise<void> {
  await Promise.all([
    useSystemStore.getState().refresh(),
    useReferenceStore.getState().refresh(),
    useJobStore.getState().loadHistory(),
    useMeshTunerStore.getState().refreshRuntime(),
    useFeaturesStore.getState().refresh(),
  ])
  // Tuning runs of the model in the viewer (known only once the history has loaded): GET only, viewing past results.
  const viewedJobId = useJobStore.getState().viewedJobId
  const featuresReady = useFeaturesStore.getState().snapshot.status === 'ready'
  usePartStore.getState().resetView(viewedJobId)
  await Promise.all([
    viewedJobId ? useMeshTunerStore.getState().loadRunsForJob(viewedJobId, true) : Promise.resolve(),
    // GET routes work even when the part-aware flags are off (DESIGN §6.0); a legacy backend has none.
    featuresReady ? useRunStore.getState().loadIndex() : Promise.resolve(),
    featuresReady && viewedJobId ? usePartStore.getState().loadForJob(viewedJobId, true) : Promise.resolve(),
  ])
}

export interface BackendSession {
  /** Epoch ms of the next automatic auth/status retry while unreachable. */
  nextRetryAt: number | null
}

export function useBackendSession(): BackendSession {
  const phase = useAuthStore((s) => s.phase)
  const retrySignal = useSystemStore((s) => s.retrySignal)
  const [nextRetryAt, setNextRetryAt] = useState<number | null>(null)

  // 1+2: hooks and the initial auth check.
  useEffect(() => {
    ensureClientHooks()
    void useAuthStore.getState().check()
  }, [])

  // Retry the auth check with backoff while the backend is unreachable before the session is known.
  useEffect(() => {
    if (phase !== 'unreachable') return
    let attempt = 0
    let timer: number | undefined
    let cancelled = false
    const schedule = () => {
      const delay = AUTH_RETRY_DELAYS_MS[Math.min(attempt, AUTH_RETRY_DELAYS_MS.length - 1)]
      setNextRetryAt(Date.now() + delay)
      timer = window.setTimeout(async () => {
        attempt += 1
        await useAuthStore.getState().check()
        if (!cancelled && useAuthStore.getState().phase === 'unreachable') schedule()
      }, delay)
    }
    schedule()
    return () => {
      cancelled = true
      window.clearTimeout(timer)
      setNextRetryAt(null)
    }
  }, [phase, retrySignal])

  // Manual retry outside a running session (no realtime client to nudge): before the session is known, and on
  // the login screen, where nothing else polls — the auth status request also refreshes backend reachability.
  useEffect(() => {
    const current = useAuthStore.getState().phase
    if (retrySignal === 0 || (current !== 'unreachable' && current !== 'login')) return
    void useAuthStore.getState().check()
  }, [retrySignal])

  // 3: session data + realtime.
  useEffect(() => {
    if (phase !== 'ready') return
    void syncSessionData()

    const client = new RealtimeClient({ getToken: getAuthToken, poll: pollBackend })
    const unsubscribers = [
      client.on('status', (status) => useSystemStore.getState().setRealtime(status)),
      client.on('hello', (system) => useSystemStore.getState().setSystem(system)),
      client.on('system.updated', (system) => useSystemStore.getState().setSystem(system)),
      client.on('job.updated', (job) => useJobStore.getState().upsertJob(job)),
      client.on('blender.updated', (runtime) => useMeshTunerStore.getState().applyRuntime(runtime)),
      client.on('tuning.updated', (run) => useMeshTunerStore.getState().upsertRun(run)),
      client.on('features.updated', (features) => useFeaturesStore.getState().apply(features)),
      client.on('image_generation.updated', (run) => useRunStore.getState().upsertImageGeneration(run)),
      client.on('segmentation.updated', (run) => useRunStore.getState().upsertSegmentation(run)),
      client.on('part_analysis.updated', (run) => usePartStore.getState().upsertAnalysis(run)),
      client.on('workflow.updated', (workflow) => useRunStore.getState().upsertWorkflow(workflow)),
      client.on('auth-failure', () => {
        // A 4401 close proves the backend answered. Without marking it reachable, an outage that ends with a
        // rejected token (backend restarted with a new auth secret) leaves a stale "unreachable" banner on the
        // login screen. Expire first so the outage-recovery resync below does not fire with the dead token.
        useAuthStore.getState().expire()
        useSystemStore.getState().setReachable(true)
      }),
    ]
    client.start()

    const unsubscribeRetry = useSystemStore.subscribe((state, previous) => {
      if (state.retrySignal !== previous.retrySignal) client.retryNow()
    })

    // The viewer switched to another model: fetch its tuning runs and part analyses (cached per job by the stores).
    const unsubscribeViewed = useJobStore.subscribe((state, previous) => {
      if (state.viewedJobId === previous.viewedJobId) return
      usePartStore.getState().resetView(state.viewedJobId)
      if (!state.viewedJobId) return
      void useMeshTunerStore.getState().loadRunsForJob(state.viewedJobId)
      if (useFeaturesStore.getState().snapshot.status === 'ready') void usePartStore.getState().loadForJob(state.viewedJobId)
    })

    // `hello` arrives on every (re)connect: REST is the source of truth for events missed while disconnected.
    let connectedOnce = false
    const unsubscribeHello = client.on('hello', () => {
      if (connectedOnce) {
        void useMeshTunerStore.getState().resync()
        void useRunStore.getState().resyncActive()
        void useFeaturesStore
          .getState()
          .refresh()
          .then(() => {
            const viewed = useJobStore.getState().viewedJobId
            if (viewed && useFeaturesStore.getState().snapshot.status === 'ready') {
              void usePartStore.getState().loadForJob(viewed, true)
            }
          })
      }
      connectedOnce = true
    })

    return () => {
      unsubscribeRetry()
      unsubscribeViewed()
      unsubscribeHello()
      unsubscribers.forEach((unsubscribe) => unsubscribe())
      client.stop()
      useSystemStore.getState().setRealtime({ state: 'idle', attempt: 0, nextRetryAt: null })
    }
  }, [phase])

  // 4: after an outage, re-sync data once the backend answers again.
  useEffect(
    () =>
      useSystemStore.subscribe((state, previous) => {
        if (previous.reachable !== false || state.reachable !== true) return
        if (useAuthStore.getState().phase !== 'ready') return
        void useAuthStore.getState().check()
        void syncSessionData()
      }),
    [],
  )

  // Keep the settings form aligned with server defaults/capabilities.
  const system = useSystemStore((s) => s.system)
  useEffect(() => {
    if (system) useSettingsStore.getState().syncWithSystem(system.defaults, system.engine.capabilities)
  }, [system])

  return { nextRetryAt }
}
