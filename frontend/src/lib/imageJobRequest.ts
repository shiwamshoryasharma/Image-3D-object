/**
 * The unchanged image-to-3D request (UI_DESIGN.md §0.3, §2.8): `POST /jobs` with the exact body
 * `jobStore.submit()` has always sent. Golden fixtures G1 / G1a / G1b pin its bytes, key order included.
 *
 * PURE (UI_DESIGN §1.5): relative `.ts` value imports only; no React, zustand, axios or browser globals.
 */
import type { CreateJobRequest, ReferenceMode } from '../api/types.ts'
import { toRequestSettings, type SettingsForm } from './settingsRequest.ts'

export interface ImageJobInputs {
  referenceSetId: string
  primaryImageId: string | null
  form: SettingsForm
  referenceMode: ReferenceMode
}

export function buildImageJobRequest(i: ImageJobInputs): CreateJobRequest {
  return {
    reference_set_id: i.referenceSetId,
    primary_image_id: i.primaryImageId,
    settings: toRequestSettings(i.form, i.referenceMode),
  }
}
