/**
 * Workflows (`wf_`, part-aware DESIGN §4.7, §6.5): the server-side chain behind image/prompt → 3D (+ parts) and
 * the Node UI (catalog, validation, graph runs).
 */
import { apiClient } from './client'
import type {
  CatalogOut,
  ItemsOut,
  RunStatus,
  ValidationReport,
  WorkflowGraph,
  WorkflowOut,
  WorkflowRequest,
} from './partAwareTypes'

/** `not_modified` = the catalog for the `If-None-Match` ETag is still current (304, empty body). */
export type WorkflowCatalogResult = { kind: 'ok'; data: CatalogOut; etag: string | null } | { kind: 'not_modified' }

/** `GET /workflows/{id}/graph`: the graph and opaque layout a Node UI run was created from. */
export interface WorkflowGraphOut {
  graph: WorkflowGraph
  layout: Record<string, unknown> | null
}

export interface ListWorkflowsParams {
  /** 1..100 (server default 20). */
  limit?: number
  status?: RunStatus
  job_id?: string
  image_generation_id?: string
}

const workflowPath = (workflowId: string) => `/workflows/${encodeURIComponent(workflowId)}`

/** A 304 is a success here: with the default 2xx check it would reach the error interceptor as `ApiError http_304`. */
const acceptNotModified = (status: number): boolean => (status >= 200 && status < 300) || status === 304

/**
 * Node catalog, revalidated with `If-None-Match` when an ETag is known. The returned ETag comes from the response
 * header, falling back to the body's `etag` (the header is not readable cross-origin unless exposed).
 */
export async function getWorkflowCatalog(etag: string | null): Promise<WorkflowCatalogResult> {
  const response = await apiClient.get<CatalogOut>('/workflows/catalog', {
    headers: etag ? { 'If-None-Match': etag } : undefined,
    validateStatus: acceptNotModified,
  })
  if (response.status === 304) return { kind: 'not_modified' }
  const header = response.headers.etag
  return {
    kind: 'ok',
    data: response.data,
    etag: typeof header === 'string' && header ? header : (response.data.etag ?? null),
  }
}

/** Authoritative server check of a simple-form or graph request; never creates anything. 422 only when malformed. */
export async function validateWorkflow(body: WorkflowRequest, signal?: AbortSignal): Promise<ValidationReport> {
  const response = await apiClient.post<ValidationReport>('/workflows/validate', body, { signal })
  return response.data
}

/**
 * Start a workflow (202). 404 source; 409 job source not completed; 422 `validation_error` / `invalid_workflow`;
 * 429 `queue_full`; 503 `feature_disabled` / `capability_unavailable`.
 */
export async function createWorkflow(body: WorkflowRequest): Promise<WorkflowOut> {
  const response = await apiClient.post<WorkflowOut>('/workflows', body)
  return response.data
}

/** Newest first. */
export async function listWorkflows(params: ListWorkflowsParams = {}): Promise<WorkflowOut[]> {
  const response = await apiClient.get<ItemsOut<WorkflowOut>>('/workflows', { params })
  return response.data.items
}

export async function getWorkflow(workflowId: string): Promise<WorkflowOut> {
  const response = await apiClient.get<WorkflowOut>(workflowPath(workflowId))
  return response.data
}

/** 404 when the workflow was created from the simple form (no graph stored). */
export async function getWorkflowGraph(workflowId: string): Promise<WorkflowGraphOut> {
  const response = await apiClient.get<WorkflowGraphOut>(`${workflowPath(workflowId)}/graph`)
  return response.data
}

/** 202; cancels the active child. 409 when the workflow is already terminal. */
export async function cancelWorkflow(workflowId: string): Promise<WorkflowOut> {
  const response = await apiClient.post<WorkflowOut>(`${workflowPath(workflowId)}/cancel`)
  return response.data
}

/** 204; removes only the workflow record (children stay in history). 409 while active. Not called by the v1 UI. */
export async function deleteWorkflow(workflowId: string): Promise<void> {
  await apiClient.delete(workflowPath(workflowId))
}
