/**
 * Error → UI text (UI_DESIGN.md §2.11) for the part-aware calls and runs. Two kinds of input share one function:
 * - API errors (`ApiError`: `status` is a number) from a create / use / cancel call;
 * - run and workflow errors (`run.error` / `workflow.error`: a `JobError` without `status`).
 *
 * `errorText` returns the sentence to show (toast body, inline message, panel text). `describeError` adds what the
 * calling store should do with it: an inline prompt error, a `/features` refresh after a capability or version-skew
 * error, the server's graph issues for the Node Problems panel, a run refresh for a stale candidate.
 *
 * The image-to-3D path (`jobStore.submit()`) keeps its own texts; nothing here changes them.
 *
 * PURE (UI_DESIGN §1.5): relative `.ts` value imports only; no React, zustand, axios or browser globals.
 */
import { GRAPH_ISSUE_CODES, type GraphIssueOut, type StepFailedDetails } from '../api/partAwareTypes.ts'
import { RUN_KIND_LABELS, stepLabel, type RunLabelKind } from './featureMeta.ts'

export interface ErrorLike {
  /** HTTP status of an API error (0 = no response); absent for run / workflow errors. */
  readonly status?: number
  readonly code: string
  readonly message: string
  readonly details?: unknown
}

/** What failed: a run kind (labels run errors, picks the queue text) or the `…/candidates/{n}/use` call. */
export type ErrorSubject = RunLabelKind | 'candidate'

export interface ErrorContext {
  readonly subject?: ErrorSubject
  /** Overrides the run label of `subject` in `${label}: ${message}` texts. */
  readonly label?: string
}

export interface ErrorReport {
  readonly text: string
  /** `prompt` → show `text` under the prompt field too (`aria-describedby`). */
  readonly field: 'prompt' | null
  /** Capability / flag / version-skew errors: call `featuresStore.refresh()`. */
  readonly refreshFeatures: boolean
  /** Version skew or a request-builder bug: log the request body (hash) to the dev console. */
  readonly logBody: boolean
  /** `invalid_workflow` → the server's graph issues (Node Problems panel, `origin: 'server'`). */
  readonly issues: readonly GraphIssueOut[] | null
  /** The run or candidate changed under the user: refresh the run. */
  readonly refreshRun: boolean
  /** Not an error at all (e.g. "no analysis yet"): no toast. */
  readonly expected: boolean
}

/** One pydantic-style item of a 422 `validation_error` `details` list. */
export interface ValidationIssue {
  readonly loc: readonly (string | number)[]
  readonly type: string
  readonly msg: string
  readonly ctx: Readonly<Record<string, unknown>> | null
}

/** Run / workflow error codes with a fixed text (DESIGN §6.8); every other code reads `${label}: ${message}`. */
export const RUN_ERROR_TEXT: Readonly<Record<string, string>> = Object.freeze({
  out_of_memory: 'Ran out of GPU memory. Close other GPU apps and retry.',
  insufficient_vram: 'Not enough free GPU memory for this step.',
  interrupted: 'Interrupted by a server restart.',
  run_timeout: 'The step took too long and was stopped.',
  queue_full: 'A queue was full when the next step started.',
})

export const ERROR_TEXT = Object.freeze({
  promptControlCharacters: 'Remove control characters from the prompt.',
  promptRejected: 'The prompt was rejected.',
  shuttingDown: 'The server is shutting down.',
  jobQueueFull: 'The generation queue is full.',
  runQueueFull: 'The run queue is full.',
  candidateGone: 'That image is no longer available.',
  noAnalysis: 'No parts yet.',
  fallback: 'Something went wrong.',
})

type Json = Record<string, unknown>

const isObject = (value: unknown): value is Json => typeof value === 'object' && value !== null && !Array.isArray(value)
const str = (value: unknown): string | null => (typeof value === 'string' ? value : null)

const RUN_CHILD_KINDS: readonly string[] = ['image_generation', 'segmentation', 'job', 'part_analysis']

/** `error.details` of a workflow `step_failed` error (DESIGN §4.7.4), checked field by field. */
export function isStepFailedDetails(value: unknown): value is StepFailedDetails {
  if (!isObject(value) || typeof value.step !== 'string' || value.step === '') return false
  if (typeof value.child_kind !== 'string' || !RUN_CHILD_KINDS.includes(value.child_kind)) return false
  if (value.child_id !== null && typeof value.child_id !== 'string') return false
  const child = value.child_error
  return isObject(child) && typeof child.code === 'string' && typeof child.message === 'string'
}

/** The items of a 422 `details` list; anything malformed is skipped. */
export function validationIssues(details: unknown): readonly ValidationIssue[] {
  const out: ValidationIssue[] = []
  for (const item of Array.isArray(details) ? details : []) {
    if (!isObject(item)) continue
    const loc = Array.isArray(item.loc) ? item.loc.filter((p): p is string | number => typeof p === 'string' || typeof p === 'number') : []
    out.push({ loc, type: str(item.type) ?? '', msg: str(item.msg) ?? '', ctx: isObject(item.ctx) ? item.ctx : null })
  }
  return out
}

/** `["body","source","generation","prompt"]` → `source.generation.prompt`. */
export function locText(loc: readonly (string | number)[]): string {
  const parts = loc[0] === 'body' ? loc.slice(1) : loc
  return parts.length > 0 ? parts.join('.') : 'body'
}

function humanize(name: string): string {
  const words = name.replace(/[_\s]+/g, ' ').trim()
  return words ? words.charAt(0).toUpperCase() + words.slice(1) : 'A value'
}

function formatAllowed(value: unknown): string | null {
  if (!Array.isArray(value) || value.length === 0) return null
  return value
    .map((item) => (Array.isArray(item) ? item.join('×') : String(item)))
    .join(', ')
}

const sentence = (text: string): string => {
  const trimmed = text.trim()
  if (!trimmed) return ''
  const capitalised = trimmed.charAt(0).toUpperCase() + trimmed.slice(1)
  return /[.!?…]$/.test(capitalised) ? capitalised : `${capitalised}.`
}

function runLabel(ctx: ErrorContext | undefined): string {
  if (ctx?.label) return ctx.label
  const subject = ctx?.subject
  return subject && subject !== 'candidate' ? RUN_KIND_LABELS[subject] : RUN_KIND_LABELS.workflow
}

const report = (text: string, extra: Partial<ErrorReport> = {}): ErrorReport => ({
  text,
  field: null,
  refreshFeatures: false,
  logBody: false,
  issues: null,
  refreshRun: false,
  expected: false,
  ...extra,
})

/** Text of a run or workflow `error` (no HTTP status). */
function runErrorText(e: ErrorLike, ctx: ErrorContext | undefined): string {
  const label = runLabel(ctx)
  if (e.code === 'step_failed' && isStepFailedDetails(e.details)) {
    return `${stepLabel(e.details.step)} failed: ${e.details.child_error.message}`
  }
  if (Object.hasOwn(RUN_ERROR_TEXT, e.code)) return RUN_ERROR_TEXT[e.code]
  return `${label}: ${e.message || e.code}`
}

function graphIssues(details: unknown): GraphIssueOut[] {
  const issues = isObject(details) && Array.isArray(details.issues) ? details.issues : []
  const known: readonly string[] = GRAPH_ISSUE_CODES
  return issues.filter(
    (i): i is GraphIssueOut => isObject(i) && typeof i.message === 'string' && typeof i.code === 'string' && known.includes(i.code),
  )
}

/** `details.reasons[].reason` of a 503 `capability_unavailable` (DESIGN §6.8). */
function capabilityReasons(details: unknown): string[] {
  const reasons = isObject(details) && Array.isArray(details.reasons) ? details.reasons : []
  const out: string[] = []
  for (const r of reasons) {
    const reason = isObject(r) ? str(r.reason) : null
    if (reason && !out.includes(reason)) out.push(reason)
  }
  return out
}

function validationReport(e: ErrorLike): ErrorReport {
  const issues = validationIssues(e.details)
  const extra = issues.find((i) => i.type === 'extra_forbidden')
  if (extra) {
    return report(`The backend rejected an unknown field (${locText(extra.loc)}).`, { refreshFeatures: true, logBody: true })
  }
  const conflict = issues.find((i) => i.type === 'conflicting_fields')
  if (conflict) return report(`The backend rejected the request (conflicting fields: ${locText(conflict.loc)}).`, { logBody: true })

  const prompt = issues.find((i) => i.loc[i.loc.length - 1] === 'prompt')
  if (prompt) {
    const text = prompt.type === 'invalid_characters' ? ERROR_TEXT.promptControlCharacters : sentence(prompt.msg) || ERROR_TEXT.promptRejected
    return report(text, { field: 'prompt' })
  }
  const unsupported = issues.find((i) => i.type === 'unsupported_by_model' || i.type === 'unsupported_by_engine')
  if (unsupported) {
    const field = String(unsupported.loc[unsupported.loc.length - 1] ?? 'value')
    const by = unsupported.type === 'unsupported_by_model' ? 'the model' : 'the 3D engine'
    const allowed = formatAllowed(unsupported.ctx?.allowed)
    return report(`${humanize(field)} is not supported by ${by}${allowed ? ` (allowed: ${allowed})` : ''}.`)
  }
  const first = issues.find((i) => i.msg)
  if (first) {
    const field = first.loc.slice(first.loc[0] === 'body' ? 1 : 0).findLast((p): p is string => typeof p === 'string')
    return report(sentence(field ? `${humanize(field)}: ${first.msg}` : first.msg))
  }
  return report(sentence(e.message) || ERROR_TEXT.fallback)
}

/** Text of an API error, plus what the caller should do about it (UI_DESIGN §2.11 table). */
function apiErrorReport(e: ErrorLike, ctx: ErrorContext | undefined): ErrorReport {
  const status = e.status ?? 0
  if (ctx?.subject === 'candidate' && (status === 404 || status === 409)) {
    return report(ERROR_TEXT.candidateGone, { refreshRun: true })
  }
  if (status === 404 && isObject(e.details) && e.details.reason === 'none') {
    return report(ERROR_TEXT.noAnalysis, { expected: true })
  }
  if (status === 422 && e.code === 'invalid_workflow') {
    const issues = graphIssues(e.details)
    return report(issues[0]?.message ?? (sentence(e.message) || ERROR_TEXT.fallback), { issues })
  }
  if (status === 422 && e.code === 'validation_error') return validationReport(e)
  if (status === 429 && e.code === 'queue_full') {
    return report(ctx?.subject === 'job' ? ERROR_TEXT.jobQueueFull : ERROR_TEXT.runQueueFull)
  }
  if (status === 503 && e.code === 'engine_unavailable') return report(ERROR_TEXT.shuttingDown)
  if (status === 503 && (e.code === 'feature_disabled' || e.code === 'capability_unavailable' || e.code === 'model_unavailable')) {
    const reasons = capabilityReasons(e.details)
    const base = sentence(e.message) || ERROR_TEXT.fallback
    return report(reasons.length > 0 ? `${base} ${reasons.map(sentence).join(' ')}` : base, { refreshFeatures: true })
  }
  return report(sentence(e.message) || ERROR_TEXT.fallback)
}

/** Text and follow-up actions for an API error or a run / workflow error. */
export function describeError(e: ErrorLike, ctx?: ErrorContext): ErrorReport {
  if (typeof e.status !== 'number') return report(runErrorText(e, ctx))
  return apiErrorReport(e, ctx)
}

/** The sentence to show for an API error or a run / workflow error. */
export function errorText(e: ErrorLike, ctx?: ErrorContext): string {
  return describeError(e, ctx).text
}
