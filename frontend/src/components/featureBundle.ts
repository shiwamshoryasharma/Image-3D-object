/**
 * The post-`/features` content as ONE lazy chunk (UI_DESIGN §4.2, §8.1 (b)). Never import this module statically:
 * every consumer goes through `lazyFeatures.ts`, so a legacy backend (or a session where none of these render)
 * never downloads the chunk.
 */
export { ExportContent } from './export/ExportContent'
export { ExportPanel } from './export/ExportPanel'
export { PipelineSteps } from './generation/PipelineSteps'
export { PromptContent } from './input/PromptContent'
export { PromptPanel } from './input/PromptPanel'
export { PromptReviewContent } from './input/PromptReviewContent'
export { SegmentationPreview } from './input/SegmentationPreview'
export { PartsContent } from './parts/PartsContent'
export { PartsPanel } from './parts/PartsPanel'
export { LicenseNotice } from './settings/LicenseNotice'
export { PartOptionsSection } from './settings/PartOptionsSection'
export { SegmentationOptionsContent } from './settings/SegmentationOptionsContent'
