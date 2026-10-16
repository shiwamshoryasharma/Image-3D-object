/**
 * Run and workflow progress as the UI shows it (UI_DESIGN.md §2.4, §2.7, §4.1 `ActiveRunView`): the stale guard every
 * run store applies, and one step-view shape for workflow steps and run steps.
 *
 * Progress units differ on the wire (DESIGN §4.7.5, UI BR8): `WorkflowStepOut.progress` is 0..100, while
 * `RunStepOut.progress` is 0..1 inside a run. Step views always carry a 0..100 `percent`.
 *
 * PURE (UI_DESIGN §1.5): relative `.ts` value imports only; no React, zustand, axios or browser globals.
 */
import type { RunStatus, RunStepOut, RunStepStatus, RunWarningOut, WorkflowStepOut } from '../api/partAwareTypes.ts'
import { stepLabel, warningText } from './featureMeta.ts'

const TERMINAL: ReadonlySet<RunStatus> = new Set<RunStatus>(['completed', 'failed', 'cancelled'])
const STATUS_RANK: Readonly<Record<RunStatus, number>> = Object.freeze({
  queued: 0,
  running: 1,
  completed: 2,
  failed: 2,
  cancelled: 2,
})

export function isTerminalRunStatus(status: RunStatus): boolean {
  return TERMINAL.has(status)
}

/** The fields the stale guard reads (`RunOut`, `PartAnalysisOut` and `WorkflowOut` all have them). */
export interface RunRecordLike {
  readonly status: RunStatus
  readonly progress: { readonly updated_at: string | null }
}

const timeOf = (value: string | null): number => (value === null ? Number.NaN : Date.parse(value))

/**
 * True when `next` is older than `prev` (a slow REST answer arriving after a newer WebSocket update), with the rule of
 * `jobStore.isStale`: the status rank never decreases (queued < running < terminal); a terminal record only accepts the
 * same status; for equal statuses an older `progress.updated_at` is dropped (unknown timestamps are accepted).
 */
export function isStaleRun(prev: RunRecordLike, next: RunRecordLike): boolean {
  if (STATUS_RANK[next.status] < STATUS_RANK[prev.status]) return true
  if (isTerminalRunStatus(prev.status)) return next.status !== prev.status
  if (next.status !== prev.status) return false
  return timeOf(next.progress.updated_at) < timeOf(prev.progress.updated_at)
}

// ------------------------------------------------------------------------------------------------
// Step views
// ------------------------------------------------------------------------------------------------

/** Run step statuses; a workflow step that is `done` with warnings becomes `warning` (DESIGN §4.7.5). */
export type StepViewStatus = RunStepStatus

export interface StepView {
  readonly id: string
  readonly label: string
  readonly status: StepViewStatus
  readonly message: string | null
  /** 0..100 within the step; `null` while unknown. */
  readonly percent: number | null
  readonly warningCount: number
  /** Graph node owning a workflow step (`null` for implicit steps and for run steps). */
  readonly nodeId: string | null
  /** Workflow step artifacts (node thumbnails); empty for run steps. */
  readonly artifacts: readonly { readonly role: string; readonly url: string }[]
}

const clampPercent = (value: number): number => Math.min(100, Math.max(0, value))
const finite = (value: unknown): number | null => (typeof value === 'number' && Number.isFinite(value) ? value : null)

/** Workflow steps (`WorkflowOut.steps`, progress 0..100) → step views with the shared step labels. */
export function workflowStepViews(steps: readonly WorkflowStepOut[]): readonly StepView[] {
  return steps.map((step) => {
    const warningCount = Math.max(0, Math.floor(finite(step.warning_count) ?? 0))
    const progress = finite(step.progress)
    const status: StepViewStatus = step.status === 'done' && warningCount > 0 ? 'warning' : step.status
    return {
      id: step.id,
      label: stepLabel(step.id),
      status,
      message: step.message ?? null,
      percent: progress !== null ? clampPercent(progress) : step.status === 'done' ? 100 : null,
      warningCount,
      nodeId: step.node_id,
      artifacts: Array.isArray(step.artifacts) ? step.artifacts : [],
    }
  })
}

/**
 * Run steps (`RunOut.steps`, progress 0..1) → step views. Labels come from the payload; warnings are counted per
 * `step_id`.
 */
export function runStepViews(steps: readonly RunStepOut[], warnings: readonly RunWarningOut[] = []): readonly StepView[] {
  return steps.map((step) => {
    const progress = finite(step.progress)
    const done = step.status === 'done' || step.status === 'warning'
    return {
      id: step.id,
      label: step.label || stepLabel(step.id),
      status: step.status,
      message: step.message,
      percent: progress !== null ? clampPercent(progress * 100) : done ? 100 : null,
      warningCount: warnings.filter((w) => w.step_id === step.id).length,
      nodeId: null,
      artifacts: [],
    }
  })
}

/** The step to name in a progress line: the running one, else the failed one, else `null`. */
export function currentStep(views: readonly StepView[]): StepView | null {
  return views.find((v) => v.status === 'running') ?? views.find((v) => v.status === 'failed') ?? null
}

/** Overall percent of a run or workflow (`progress.percent`, 0..100), clamped; `null` when not a number. */
export function runPercent(progress: { readonly percent: number } | null | undefined): number | null {
  const value = finite(progress?.percent)
  return value === null ? null : clampPercent(value)
}

export interface WarningView {
  readonly code: string
  readonly text: string
}

/** Run warnings → display texts (`WARNING_TEXT`, unknown codes show the server message); identical lines collapse. */
export function warningViews(warnings: readonly { readonly code: string; readonly message?: string | null }[]): readonly WarningView[] {
  const out: WarningView[] = []
  const seen = new Set<string>()
  for (const w of warnings) {
    const text = warningText(w.code, w.message)
    const key = `${w.code}\u0000${text}`
    if (seen.has(key)) continue
    seen.add(key)
    out.push({ code: w.code, text })
  }
  return out
}
