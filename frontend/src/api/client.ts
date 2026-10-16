/**
 * Shared axios instance for the Img3D API (`/api/v1`).
 *
 * - Adds `Authorization: Bearer <token>` when a token is stored.
 * - Normalizes every failure into {@link ApiError} `{status, code, message, details, requestId}`.
 * - Reports backend reachability and 401s through hooks registered by the stores (keeps this module
 *   free of store imports and therefore free of import cycles).
 */
import axios, { type AxiosResponse, type RawAxiosResponseHeaders, type AxiosResponseHeaders } from 'axios'

import { STORAGE_KEYS, readString, writeString } from '../lib/storage'
import type { ApiErrorBody, ApiErrorPayload } from './types'

// ------------------------------------------------------------------------------------------------
// Base URLs
// ------------------------------------------------------------------------------------------------

/** Backend origin ('' = same origin, which the Vite dev proxy and the production static mount use). */
export const API_ORIGIN: string = (import.meta.env.VITE_API_BASE_URL ?? '').replace(/\/+$/, '')
export const API_PREFIX = '/api/v1'
export const API_BASE_URL = `${API_ORIGIN}${API_PREFIX}`

// ------------------------------------------------------------------------------------------------
// Auth token
// ------------------------------------------------------------------------------------------------

let authToken: string | null = readString(STORAGE_KEYS.authToken)

export function getAuthToken(): string | null {
  return authToken
}

export function setAuthToken(token: string | null): void {
  authToken = token
  writeString(STORAGE_KEYS.authToken, token)
}

/**
 * Resolve a path returned by the API (e.g. `/api/v1/jobs/…/model.glb`) to a URL usable by `<img>`,
 * loaders or `<a>`. Appends `?token=` when a token is stored because those consumers cannot send headers.
 */
export function resolveApiUrl(path: string, options: { withToken?: boolean } = {}): string {
  const { withToken = true } = options
  const absolute = /^[a-z][a-z\d+.-]*:\/\//i.test(path) ? path : `${API_ORIGIN}${path.startsWith('/') ? '' : '/'}${path}`
  const token = withToken ? authToken : null
  if (!token) return absolute
  const separator = absolute.includes('?') ? '&' : '?'
  return `${absolute}${separator}token=${encodeURIComponent(token)}`
}

// ------------------------------------------------------------------------------------------------
// Errors
// ------------------------------------------------------------------------------------------------

/** Client-side error codes (server codes are listed in the contract §4). */
export const CLIENT_ERROR_CODES = {
  unreachable: 'backend_unreachable',
  timeout: 'timeout',
  cancelled: 'request_cancelled',
  invalidResponse: 'invalid_response',
  unknown: 'unknown_error',
} as const

export interface ApiErrorInit {
  status: number
  code: string
  message: string
  details?: unknown
  requestId?: string | null
}

export class ApiError extends Error {
  readonly status: number
  readonly code: string
  readonly details: unknown
  readonly requestId: string | null

  constructor(init: ApiErrorInit) {
    super(init.message)
    this.name = 'ApiError'
    this.status = init.status
    this.code = init.code
    this.details = init.details ?? null
    this.requestId = init.requestId ?? null
  }

  /** The backend could not be reached at all (connection refused, proxy error, gateway timeout). */
  get isUnreachable(): boolean {
    return this.code === CLIENT_ERROR_CODES.unreachable
  }

  get isCancelled(): boolean {
    return this.code === CLIENT_ERROR_CODES.cancelled
  }
}

/** HTTP statuses that, without an Img3D error envelope, mean a proxy/gateway could not reach the backend. */
const GATEWAY_STATUSES = new Set([500, 502, 503, 504])

function isErrorEnvelope(value: unknown): value is ApiErrorBody {
  if (typeof value !== 'object' || value === null || !('error' in value)) return false
  const inner = (value as { error: unknown }).error
  return typeof inner === 'object' && inner !== null && 'code' in inner && 'message' in inner
}

function readRequestId(headers: RawAxiosResponseHeaders | AxiosResponseHeaders | undefined): string | null {
  if (!headers) return null
  const value = (headers as Record<string, unknown>)['x-request-id']
  return typeof value === 'string' && value ? value : null
}

function unreachableError(status = 0): ApiError {
  return new ApiError({
    status,
    code: CLIENT_ERROR_CODES.unreachable,
    message: 'Cannot reach the Img3D backend. Make sure it is running.',
  })
}

function fromResponse(response: AxiosResponse<unknown>, data: unknown): ApiError {
  const headerId = readRequestId(response.headers)
  if (isErrorEnvelope(data)) {
    const payload: ApiErrorPayload = data.error
    return new ApiError({
      status: response.status,
      code: payload.code,
      message: payload.message,
      details: payload.details ?? null,
      requestId: payload.request_id ?? headerId,
    })
  }
  if (GATEWAY_STATUSES.has(response.status)) return unreachableError(response.status)
  return new ApiError({
    status: response.status,
    code: `http_${response.status}`,
    message: response.statusText || `Request failed with status ${response.status}`,
    requestId: headerId,
  })
}

async function parseBlobBody(data: unknown): Promise<unknown> {
  if (typeof Blob === 'undefined' || !(data instanceof Blob)) return data
  try {
    const text = await data.text()
    return text ? (JSON.parse(text) as unknown) : null
  } catch {
    return null
  }
}

/** Normalize anything thrown by axios (or our own code) into an {@link ApiError}. */
export async function toApiError(error: unknown): Promise<ApiError> {
  if (error instanceof ApiError) return error
  if (axios.isCancel(error)) {
    return new ApiError({ status: 0, code: CLIENT_ERROR_CODES.cancelled, message: 'Request was cancelled' })
  }
  if (axios.isAxiosError(error)) {
    if (error.code === 'ERR_CANCELED') {
      return new ApiError({ status: 0, code: CLIENT_ERROR_CODES.cancelled, message: 'Request was cancelled' })
    }
    const response = error.response
    if (!response) {
      if (error.code === 'ECONNABORTED' || error.code === 'ETIMEDOUT') {
        return new ApiError({ status: 0, code: CLIENT_ERROR_CODES.timeout, message: 'The request timed out' })
      }
      return unreachableError()
    }
    return fromResponse(response, await parseBlobBody(response.data))
  }
  if (error instanceof Error) {
    return new ApiError({ status: 0, code: CLIENT_ERROR_CODES.unknown, message: error.message })
  }
  return new ApiError({ status: 0, code: CLIENT_ERROR_CODES.unknown, message: 'Unexpected error' })
}

/** Best-effort human readable message for any thrown value. */
export function errorMessage(error: unknown, fallback = 'Something went wrong'): string {
  if (error instanceof ApiError || error instanceof Error) return error.message || fallback
  return fallback
}

// ------------------------------------------------------------------------------------------------
// Hooks registered by the application (stores)
// ------------------------------------------------------------------------------------------------

export interface ClientHooks {
  /** Called on 401 responses from protected endpoints. */
  onUnauthorized?: (error: ApiError) => void
  /** Called whenever a request proves the backend reachable (`true`) or unreachable (`false`). */
  onReachability?: (reachable: boolean, error?: ApiError) => void
}

let clientHooks: ClientHooks = {}

export function configureClientHooks(hooks: ClientHooks): void {
  clientHooks = { ...clientHooks, ...hooks }
}

/** Auth endpoints whose 401 must not trigger the global "session expired" flow (logout with a stale token). */
const PUBLIC_AUTH_PATHS = ['/auth/login', '/auth/status', '/auth/logout']

function isPublicAuthRequest(url: string | undefined): boolean {
  return !!url && PUBLIC_AUTH_PATHS.some((path) => url.endsWith(path))
}

// ------------------------------------------------------------------------------------------------
// Axios instance
// ------------------------------------------------------------------------------------------------

export const apiClient = axios.create({
  baseURL: API_BASE_URL,
  timeout: 30_000,
  headers: { Accept: 'application/json' },
})

apiClient.interceptors.request.use((config) => {
  if (authToken) config.headers.set('Authorization', `Bearer ${authToken}`)
  return config
})

apiClient.interceptors.response.use(
  (response) => {
    clientHooks.onReachability?.(true)
    return response
  },
  async (error: unknown) => {
    const apiError = await toApiError(error)
    if (apiError.isUnreachable) {
      clientHooks.onReachability?.(false, apiError)
    } else if (apiError.status > 0) {
      clientHooks.onReachability?.(true)
    }
    const url = axios.isAxiosError(error) ? error.config?.url : undefined
    if (apiError.status === 401 && !isPublicAuthRequest(url)) {
      clientHooks.onUnauthorized?.(apiError)
    }
    return Promise.reject(apiError)
  },
)

/** Request options for absolute API paths returned by the backend (`/api/v1/...`). */
export const absolutePathConfig = { baseURL: API_ORIGIN } as const
