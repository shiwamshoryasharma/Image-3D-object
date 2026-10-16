/**
 * Store glue for the generation plan and availability (UI_DESIGN §2.9, §2.10). Not a store: it creates no state and
 * is value-imported only by `actions/` and `hooks/` (§1.4). The pure decisions live in `lib/generatePlan.ts` and
 * `lib/generateAvailability.ts`; this module only maps the stores onto their inputs.
 */
import type { ReferenceSet, SystemStatus } from '../api/types'
import { generateAvailability, type AvailabilityInputs, type GenerateAvailability } from '../lib/generateAvailability.ts'
import { planGeneration, type GenerationPlan, type PlanInputs, type PlanReferences } from '../lib/generatePlan.ts'
import type { FeaturesSnapshot } from '../lib/featureFlags.ts'
import type { InputSource, PartOptionsForm, PromptForm } from '../lib/forms.ts'
import type { SettingsForm } from '../lib/settingsRequest.ts'
import { useFeaturesStore } from './featuresStore'
import { useInputStore } from './inputStore'
import { selectActiveJob, useJobStore } from './jobStore'
import { useNavigationStore } from './navigationStore'
import { usePartOptionsStore } from './partOptionsStore'
import { useReferenceStore } from './referenceStore'
import { selectActiveWorkflow, selectReviewGeneration, useRunStore } from './runStore'
import { useSettingsStore } from './settingsStore'
import { useSystemStore } from './systemStore'

/** The raw store values the plan and availability read (hooks select them; actions read `getState()`). */
export interface PlanSources {
  features: FeaturesSnapshot
  source: InputSource
  prompt: PromptForm
  parts: PartOptionsForm
  referenceSet: ReferenceSet | null
  form: SettingsForm
  system: SystemStatus | null
}

export interface AvailabilitySources extends PlanSources {
  reachable: boolean | null
  uploading: boolean
  activeJobStatus: AvailabilityInputs['activeJobStatus']
  activeWorkflowStatus: AvailabilityInputs['activeWorkflowStatus']
  reviewGenerationStatus: AvailabilityInputs['reviewGenerationStatus']
  submitting: boolean
  pipelineBlock: string | null
  nodeShellActive: boolean
}

function referencesOf(set: ReferenceSet | null): PlanReferences | null {
  return set ? { id: set.id, primaryImageId: set.primary_image_id, imageCount: set.images.length } : null
}

/** Inputs of `planGeneration` from the selected store values (same reference mode rule as `jobStore.submit`). */
export function planInputsOf(s: PlanSources): PlanInputs {
  return {
    features: s.features,
    source: s.source,
    prompt: s.prompt,
    parts: s.parts,
    references: referencesOf(s.referenceSet),
    form: s.form,
    referenceMode: s.system?.defaults.reference_mode ?? 'primary_only',
  }
}

export function availabilityInputsOf(s: AvailabilitySources): AvailabilityInputs {
  return {
    reachable: s.reachable,
    system: s.system,
    plan: planInputsOf(s),
    uploading: s.uploading,
    activeJobStatus: s.activeJobStatus,
    activeWorkflowStatus: s.activeWorkflowStatus,
    reviewGenerationStatus: s.reviewGenerationStatus,
    submitting: s.submitting,
    pipelineBlock: s.pipelineBlock,
    nodeShellActive: s.nodeShellActive,
  }
}

function planSourcesNow(): PlanSources {
  const input = useInputStore.getState()
  return {
    features: useFeaturesStore.getState().snapshot,
    source: input.source,
    prompt: input.prompt,
    parts: usePartOptionsStore.getState().form,
    referenceSet: useReferenceStore.getState().set,
    form: useSettingsStore.getState().form,
    system: useSystemStore.getState().system,
  }
}

/** The plan a Generate press makes right now. */
export function planFromStores(): GenerationPlan {
  return planGeneration(planInputsOf(planSourcesNow()))
}

/** Availability right now (outside React: actions, shortcuts). */
export function availabilityFromStores(): GenerateAvailability {
  const run = useRunStore.getState()
  const navigation = useNavigationStore.getState()
  return generateAvailability(
    availabilityInputsOf({
      ...planSourcesNow(),
      reachable: useSystemStore.getState().reachable,
      uploading: useReferenceStore.getState().uploads.length > 0,
      activeJobStatus: selectActiveJob(useJobStore.getState())?.status ?? null,
      activeWorkflowStatus: selectActiveWorkflow(run)?.status ?? null,
      reviewGenerationStatus: selectReviewGeneration(run)?.status ?? null,
      submitting: useJobStore.getState().submitting || run.submitting,
      pipelineBlock: navigation.pipelineBlock,
      nodeShellActive: navigation.runOverride !== null,
    }),
  )
}
