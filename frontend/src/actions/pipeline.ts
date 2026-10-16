/**
 * Node workflow write-through onto the shared stores (UI_DESIGN §4.3.6). Called by the Node shell after every
 * topology/mode change with the derived `PipelineConfig`; no-op writes are skipped by the stores.
 * Layer (§1.4): stores, api, lib; no React.
 */
import type { PipelineConfig } from '../workflow/engine/types.ts'
import { useInputStore } from '../store/inputStore'
import { useNavigationStore } from '../store/navigationStore'
import { usePartOptionsStore } from '../store/partOptionsStore'
import { toast } from '../store/toastStore'

let partsToastShown = false

/**
 * `source` image/prompt → `inputStore.setSource` (not for `job`); `partOptions.enabled = partExtraction` and, only
 * when parts are on, `previewSegmentation = segmentation` (with parts off the stored preference is kept, it is inert).
 * Clears the pipeline block. The first change of `partOptions.enabled` in a session shows an info toast.
 */
export function applyPipelineConfig(config: PipelineConfig): void {
  if (config.source !== 'job') useInputStore.getState().setSource(config.source)
  const partOptions = usePartOptionsStore.getState()
  const enabledChanged = partOptions.form.enabled !== config.partExtraction
  partOptions.update(
    config.partExtraction ? { enabled: true, previewSegmentation: config.segmentation } : { enabled: false },
  )
  if (enabledChanged && !partsToastShown) {
    partsToastShown = true
    toast.info(`Part-aware output turned ${config.partExtraction ? 'on' : 'off'} from the Node workflow`)
  }
  useNavigationStore.getState().setPipelineBlock(null)
}
