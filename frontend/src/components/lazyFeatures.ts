/**
 * Lazy wrappers of the feature bundle (UI_DESIGN §4.2): each one resolves a named export of `featureBundle.ts`, so
 * Rolldown emits a single chunk. Render them inside `<Suspense fallback={<Skeleton …/>}>` and only under the §4.2
 * conditions (features ready, or a stored value that needs its off switch), never on a legacy backend.
 */
import { lazy } from 'react'

const load = () => import('./featureBundle.ts')

export const LazyPromptPanel = lazy(() => load().then((m) => ({ default: m.PromptPanel })))
export const LazyPromptContent = lazy(() => load().then((m) => ({ default: m.PromptContent })))
export const LazyPromptReviewContent = lazy(() => load().then((m) => ({ default: m.PromptReviewContent })))
export const LazySegmentationPreview = lazy(() => load().then((m) => ({ default: m.SegmentationPreview })))
export const LazyPartOptionsSection = lazy(() => load().then((m) => ({ default: m.PartOptionsSection })))
export const LazySegmentationOptionsContent = lazy(() => load().then((m) => ({ default: m.SegmentationOptionsContent })))
export const LazyLicenseNotice = lazy(() => load().then((m) => ({ default: m.LicenseNotice })))
export const LazyPartsPanel = lazy(() => load().then((m) => ({ default: m.PartsPanel })))
export const LazyPartsContent = lazy(() => load().then((m) => ({ default: m.PartsContent })))
export const LazyExportContent = lazy(() => load().then((m) => ({ default: m.ExportContent })))
export const LazyExportPanel = lazy(() => load().then((m) => ({ default: m.ExportPanel })))
export const LazyPipelineSteps = lazy(() => load().then((m) => ({ default: m.PipelineSteps })))

/** Warm the chunk (e.g. on hover/focus of a destination that shows feature content). Errors surface on render. */
export function preloadFeatures(): void {
  void load().catch(() => undefined)
}
