/**
 * Node shell ↔ shared stores (UI_DESIGN §4.3.6–§4.3.7): the bound params and facts the built-in nodes read, the
 * write-through of the derived `PipelineConfig`, and `pipelineBlock`.
 * Built-in node parameters are never stored in the graph: they come from the same stores every mode edits.
 */
import { useFeaturesStore } from '../../store/featuresStore'
import { useInputStore } from '../../store/inputStore'
import { useJobStore } from '../../store/jobStore'
import { useNavigationStore } from '../../store/navigationStore'
import { usePartOptionsStore } from '../../store/partOptionsStore'
import { useReferenceStore } from '../../store/referenceStore'
import { useSettingsStore } from '../../store/settingsStore'
import { useSystemStore } from '../../store/systemStore'
import { registryFor, registryNow, useWorkflowStore } from '../../store/workflowStore'
import { applyPipelineConfig } from '../../actions/pipeline'
import { effectivePartsEnabled, effectiveSource } from '../../lib/generatePlan.ts'
import { toRequestSettings } from '../../lib/settingsRequest.ts'
import {
  buildExportParams,
  buildGeneration,
  buildPartAnalysisOptions,
  buildSegmentationOptions,
} from '../../lib/workflowRequest.ts'
import {
  configFromStores,
  deriveConfig,
  firstError,
  mergeIssues,
  sameConfig,
  validateGraph,
  type BoundFacts,
  type BoundParams,
  type GraphIssue,
  type NodeRegistry,
  type ValidationContext,
  type WorkflowDoc,
} from '../../workflow/engine/index.ts'
import type { ValidationReport } from '../../api/partAwareTypes.ts'
import type { FeaturesSnapshot } from '../../lib/featureFlags.ts'

// ------------------------------------------------------------------------------------------------
// Snapshot readers (event handlers / subscriptions)
// ------------------------------------------------------------------------------------------------
export function factsFromStores(): BoundFacts {
  const set = useReferenceStore.getState().set
  const jobs = useJobStore.getState()
  const viewed = jobs.viewedJobId ? jobs.jobs[jobs.viewedJobId] : undefined
  return {
    imageCount: set?.images.length ?? 0,
    referenceSetId: set?.id ?? null,
    promptLength: useInputStore.getState().prompt.prompt.trim().length,
    promptMax: useFeaturesStore.getState().snapshot.prompt.maxChars,
    viewedJobCompleted: viewed?.status === 'completed',
  }
}

/** A `ValidationContext` that keeps the full snapshot (the shell also reads `workflow.available`). */
export interface NodeValidationContext extends ValidationContext {
  readonly features: FeaturesSnapshot
}

export function contextFromStores(): NodeValidationContext {
  return { features: useFeaturesStore.getState().snapshot, bound: factsFromStores() }
}

/** The values compiled into the server graph, built with the §2.8 builders (fixed key lists, no catalog filter). */
export function boundFromStores(): BoundParams {
  const features = useFeaturesStore.getState().snapshot
  const ctx = { features }
  const set = useReferenceStore.getState().set
  const jobs = useJobStore.getState()
  const viewed = jobs.viewedJobId ? jobs.jobs[jobs.viewedJobId] : undefined
  const prompt = useInputStore.getState().prompt
  const parts = usePartOptionsStore.getState().form
  const referenceMode = useSystemStore.getState().system?.defaults.reference_mode ?? 'primary_only'
  return {
    input_image: { reference_set_id: set?.id ?? null, primary_image_id: set?.primary_image_id ?? null },
    prompt: prompt.prompt.trim() ? buildGeneration(prompt, 1, ctx) : null,
    job_result: { job_id: viewed?.status === 'completed' ? viewed.id : null },
    segmentation: buildSegmentationOptions(parts, ctx) ?? {},
    image_to_3d: toRequestSettings(useSettingsStore.getState().form, referenceMode),
    part_extraction: buildPartAnalysisOptions(parts, ctx),
    export: buildExportParams(parts),
  }
}

/** Client issues ∪ the server report computed for this revision (server issues first and authoritative). */
export function mergedIssues(
  doc: WorkflowDoc,
  r: NodeRegistry,
  ctx: ValidationContext,
  serverReport: { revision: number; report: ValidationReport } | null,
): GraphIssue[] {
  const server = serverReport && serverReport.revision === doc.revision ? serverReport.report.issues : []
  return mergeIssues(validateGraph(doc, r, ctx), server)
}

// ------------------------------------------------------------------------------------------------
// Write-through (registered by NodeShell while mounted; UI10)
// ------------------------------------------------------------------------------------------------
let writingThrough = false

function writeThrough(): void {
  const r = registryNow()
  const { doc, serverReport } = useWorkflowStore.getState()
  const ctx = contextFromStores()
  const { config } = deriveConfig(doc, r, ctx)
  if (config) {
    const features = ctx.features
    const form = usePartOptionsStore.getState().form
    const current = configFromStores(config.source, effectiveSource(useInputStore.getState().source, features), {
      enabled: effectivePartsEnabled(form, features),
      previewSegmentation: form.previewSegmentation,
    })
    // Only real changes reach the stores (a legacy backend's forced-off parts never overwrite the saved switch).
    if (!sameConfig(config, current)) {
      writingThrough = true
      try {
        applyPipelineConfig(config)
      } finally {
        writingThrough = false
      }
    }
  }
  useNavigationStore.getState().setPipelineBlock(firstError(mergedIssues(doc, r, ctx, serverReport))?.message ?? null)
}

/** Store edits made outside the graph (Inspector part switch, "Use as Input Image") reconcile the graph. */
function syncGraph(): void {
  if (!writingThrough) useWorkflowStore.getState().syncFromStores()
}

export function startWriteThrough(): () => void {
  const stops = [
    useWorkflowStore.subscribe((s, p) => {
      if (s.doc !== p.doc || s.serverReport !== p.serverReport) writeThrough()
    }),
    useInputStore.subscribe((s, p) => {
      if (s.source !== p.source) syncGraph()
      if (s.prompt !== p.prompt) writeThrough()
    }),
    usePartOptionsStore.subscribe((s, p) => {
      if (s.form.enabled !== p.form.enabled || s.form.previewSegmentation !== p.form.previewSegmentation) syncGraph()
    }),
    useReferenceStore.subscribe((s, p) => {
      if (s.set !== p.set) writeThrough()
    }),
    useJobStore.subscribe((s, p) => {
      const viewed = s.viewedJobId ? s.jobs[s.viewedJobId]?.status : null
      const before = p.viewedJobId ? p.jobs[p.viewedJobId]?.status : null
      if (s.viewedJobId !== p.viewedJobId || viewed !== before) writeThrough()
    }),
    useFeaturesStore.subscribe((s, p) => {
      if (s.snapshot === p.snapshot && s.catalog === p.catalog) return
      syncGraph()
      writeThrough()
    }),
  ]
  writeThrough()
  return () => {
    for (const stop of stops) stop()
    useNavigationStore.getState().setPipelineBlock(null)
  }
}

// ------------------------------------------------------------------------------------------------
// Hooks
// ------------------------------------------------------------------------------------------------
export function useNodeRegistry(): NodeRegistry {
  const catalog = useFeaturesStore((s) => s.catalog)
  const nodes = useFeaturesStore((s) => s.snapshot.nodes)
  return registryFor(catalog, nodes)
}

/** The validation context, from primitive selectors (re-validates when a bound input changes). */
export function useValidationContext(): NodeValidationContext {
  const features = useFeaturesStore((s) => s.snapshot)
  const imageCount = useReferenceStore((s) => s.set?.images.length ?? 0)
  const referenceSetId = useReferenceStore((s) => s.set?.id ?? null)
  const promptLength = useInputStore((s) => s.prompt.prompt.trim().length)
  const viewedJobCompleted = useJobStore((s) => (s.viewedJobId ? s.jobs[s.viewedJobId]?.status === 'completed' : false))
  return {
    features,
    bound: { imageCount, referenceSetId, promptLength, promptMax: features.prompt.maxChars, viewedJobCompleted },
  }
}
