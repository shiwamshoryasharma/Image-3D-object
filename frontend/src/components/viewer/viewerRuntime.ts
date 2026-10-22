/**
 * Runtime state of the 3D viewer (download progress, ready, error). Written from inside the R3F canvas,
 * read by the DOM overlays. The "secondary" model is the Auto Tune result mounted next to the original; the
 * "parts" model is the assembled parts GLB of the viewed part set (UI_DESIGN §6.6).
 *
 * Parts API for the parts panel (three-free, eager; see progress/F8.md):
 * - `focusPart(partId)` fits the camera to a part (queued until the parts model is indexed; mounts it);
 * - `selectPartViewerStatus(state, analysisId, partId)` → `'unknown' | 'ok' | 'not_found' | 'highlight_unavailable'`;
 * - `hasRegionAttribute` (null until indexed or without regions), `unmatchedPartIds`, `regionUnavailablePartIds`.
 */
import { useSyncExternalStore } from 'react'
import { create } from 'zustand'

import { viewportStore } from '../../hooks/useViewport'
import { isLowMemory } from '../../lib/viewport'

export type ViewerLoadStatus = 'idle' | 'loading' | 'ready' | 'error'

/** What the viewer can do with one part row (a primitive for `useViewerRuntime` selectors). */
export type PartViewerStatus = 'unknown' | 'ok' | 'not_found' | 'highlight_unavailable'

export interface PartsIndexReport {
  analysisId: string
  /** Binding parts with no node / host in the GLB ("Not found in the 3D file"). */
  unmatched: readonly string[]
  /** Regions / deferred parts whose host mesh has no region attribute ("Highlight unavailable for this region"). */
  regionUnavailable: readonly string[]
  /** `null` = the set has no regions. */
  hasRegionAttribute: boolean | null
}

export interface ViewerFocusRequest {
  partId: string
  seq: number
}

interface ViewerRuntimeState {
  status: ViewerLoadStatus
  loadedBytes: number
  totalBytes: number
  error: string | null
  secondaryStatus: ViewerLoadStatus
  secondaryLoadedBytes: number
  secondaryTotalBytes: number
  secondaryError: string | null
  /** Registered by the lazy viewer while a secondary model is mounted (clears the loader cache and reloads it). */
  retrySecondary: (() => void) | null
  partsStatus: ViewerLoadStatus
  partsLoadedBytes: number
  partsTotalBytes: number
  partsError: string | null
  /** Analysis whose parts GLB is indexed; `null` until then. */
  partsAnalysisId: string | null
  unmatchedPartIds: readonly string[]
  regionUnavailablePartIds: readonly string[]
  hasRegionAttribute: boolean | null
  /** Pending `focusPart` request (served and cleared by the parts model). */
  focusRequest: ViewerFocusRequest | null
  /** Registered by the lazy viewer while a parts model is mounted. */
  retryParts: (() => void) | null
  start: () => void
  setBytes: (loaded: number, total: number) => void
  setReady: () => void
  setError: (message: string) => void
  startSecondary: () => void
  resetSecondary: () => void
  setSecondaryBytes: (loaded: number, total: number) => void
  setSecondaryReady: () => void
  setSecondaryError: (message: string) => void
  setRetrySecondary: (retry: (() => void) | null) => void
  startParts: () => void
  resetParts: () => void
  setPartsBytes: (loaded: number, total: number) => void
  setPartsReady: () => void
  setPartsError: (message: string) => void
  setPartsIndex: (report: PartsIndexReport | null) => void
  setRetryParts: (retry: (() => void) | null) => void
  /** Fit the camera to a part of the viewed part set. */
  focusPart: (partId: string) => void
  /** Clear the request `seq` (or any request when omitted). */
  clearFocusRequest: (seq?: number) => void
}

const NO_IDS: readonly string[] = Object.freeze([])
const NO_INDEX = {
  partsAnalysisId: null,
  unmatchedPartIds: NO_IDS,
  regionUnavailablePartIds: NO_IDS,
  hasRegionAttribute: null,
} as const
let focusSeq = 0

export const useViewerRuntime = create<ViewerRuntimeState>()((set) => ({
  status: 'idle',
  loadedBytes: 0,
  totalBytes: 0,
  error: null,
  secondaryStatus: 'idle',
  secondaryLoadedBytes: 0,
  secondaryTotalBytes: 0,
  secondaryError: null,
  retrySecondary: null,
  partsStatus: 'idle',
  partsLoadedBytes: 0,
  partsTotalBytes: 0,
  partsError: null,
  ...NO_INDEX,
  focusRequest: null,
  retryParts: null,
  start: () => set({ status: 'loading', loadedBytes: 0, totalBytes: 0, error: null }),
  setBytes: (loadedBytes, totalBytes) => set({ loadedBytes, totalBytes }),
  setReady: () => set({ status: 'ready', error: null }),
  setError: (error) => set({ status: 'error', error }),
  startSecondary: () =>
    set({ secondaryStatus: 'loading', secondaryLoadedBytes: 0, secondaryTotalBytes: 0, secondaryError: null }),
  resetSecondary: () =>
    set({ secondaryStatus: 'idle', secondaryLoadedBytes: 0, secondaryTotalBytes: 0, secondaryError: null }),
  setSecondaryBytes: (secondaryLoadedBytes, secondaryTotalBytes) => set({ secondaryLoadedBytes, secondaryTotalBytes }),
  setSecondaryReady: () => set({ secondaryStatus: 'ready', secondaryError: null }),
  setSecondaryError: (secondaryError) => set({ secondaryStatus: 'error', secondaryError }),
  setRetrySecondary: (retrySecondary) => set({ retrySecondary }),
  startParts: () => set({ partsStatus: 'loading', partsLoadedBytes: 0, partsTotalBytes: 0, partsError: null, ...NO_INDEX }),
  resetParts: () => set({ partsStatus: 'idle', partsLoadedBytes: 0, partsTotalBytes: 0, partsError: null, ...NO_INDEX }),
  setPartsBytes: (partsLoadedBytes, partsTotalBytes) => set({ partsLoadedBytes, partsTotalBytes }),
  setPartsReady: () => set({ partsStatus: 'ready', partsError: null }),
  setPartsError: (partsError) => set({ partsStatus: 'error', partsError }),
  setPartsIndex: (report) =>
    set(
      report
        ? {
            partsAnalysisId: report.analysisId,
            unmatchedPartIds: report.unmatched.length ? Object.freeze([...report.unmatched]) : NO_IDS,
            regionUnavailablePartIds: report.regionUnavailable.length ? Object.freeze([...report.regionUnavailable]) : NO_IDS,
            hasRegionAttribute: report.hasRegionAttribute,
          }
        : NO_INDEX,
    ),
  setRetryParts: (retryParts) => set({ retryParts }),
  focusPart: (partId) => set({ focusRequest: { partId, seq: ++focusSeq } }),
  clearFocusRequest: (seq) =>
    set((state) => (state.focusRequest && (seq === undefined || state.focusRequest.seq === seq) ? { focusRequest: null } : state)),
}))

/** Viewer status of one part row (a primitive; `'unknown'` until the parts model of `analysisId` is indexed). */
export function selectPartViewerStatus(
  state: Pick<ViewerRuntimeState, 'partsAnalysisId' | 'unmatchedPartIds' | 'regionUnavailablePartIds'>,
  analysisId: string,
  partId: string,
): PartViewerStatus {
  if (state.partsAnalysisId !== analysisId) return 'unknown'
  if (state.unmatchedPartIds.includes(partId)) return 'not_found'
  if (state.regionUnavailablePartIds.includes(partId)) return 'highlight_unavailable'
  return 'ok'
}

const lowMemorySnapshot = (): boolean => isLowMemory(viewportStore.getSnapshot())
const lowMemoryServerSnapshot = (): boolean => isLowMemory(viewportStore.getServerSnapshot())

/**
 * `isLowMemory(useViewport())` (UI_DESIGN §3.5) as a primitive: re-renders only when the memory class changes.
 * Low memory: the tuned model is mounted only once shown, and the parts model only while the parts view is on.
 */
export function useLowMemory(): boolean {
  return useSyncExternalStore(viewportStore.subscribe, lowMemorySnapshot, lowMemoryServerSnapshot)
}
