/**
 * Realtime client for `GET /api/v1/ws` (§8).
 *
 * - Typed events (`hello`, `job.updated`, `system.updated`, `blender.updated`, `tuning.updated`, plus the part-aware
 *   `features.updated`, `image_generation.updated`, `segmentation.updated`, `part_analysis.updated`,
 *   `workflow.updated`) via {@link RealtimeClient.on}. Unknown types are ignored.
 * - Auto-reconnect with exponential backoff + jitter; reconnects immediately when the browser comes back
 *   online or the tab becomes visible again.
 * - Heartbeat watchdog: the server pings every 20 s, so a silent socket is recycled.
 * - Polling fallback: while the socket is not open, `poll()` runs on an interval (with backoff on failure);
 *   while it is open a slow "safety" poll keeps the REST source of truth in sync.
 * - Close code 4401 → `auth-failure` event and no further reconnects.
 */
import { API_ORIGIN, API_PREFIX } from './client'
import type { PartAwareEventMap, PartAwareWsMessage } from './partAwareTypes'
import {
  WS_CLOSE_AUTH_FAILED,
  type BlenderRuntimeStatus,
  type Job,
  type SystemStatus,
  type TuningRun,
  type WsClientMessage,
  type WsServerMessage,
} from './types'

export type RealtimeState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed'

export interface RealtimeStatus {
  state: RealtimeState
  /** Consecutive failed connection attempts. */
  attempt: number
  /** Epoch ms of the next reconnect attempt, when one is scheduled. */
  nextRetryAt: number | null
}

/** Client events: the server message types (incl. the five part-aware ones) plus `status` and `auth-failure`. */
export interface RealtimeEventMap extends PartAwareEventMap {
  hello: SystemStatus
  'job.updated': Job
  'system.updated': SystemStatus
  'blender.updated': BlenderRuntimeStatus
  'tuning.updated': TuningRun
  status: RealtimeStatus
  'auth-failure': undefined
}

type Listener<T> = (payload: T) => void

export type PollMode = 'fallback' | 'safety'

export interface RealtimeOptions {
  /** Returns the current auth token (sent as `?token=`). */
  getToken: () => string | null
  /** Optional `?job_id=` filter. */
  jobId?: string
  /** REST poll used as fallback/safety net. Resolve `true` on success, `false` on failure. */
  poll?: (mode: PollMode) => Promise<boolean>
  minBackoffMs?: number
  maxBackoffMs?: number
  /** Recycle the socket if nothing was received for this long (server pings every 20 s). */
  heartbeatTimeoutMs?: number
  fallbackPollMs?: number
  maxFallbackPollMs?: number
  safetyPollMs?: number
}

const DEFAULTS = {
  minBackoffMs: 1_000,
  maxBackoffMs: 10_000,
  heartbeatTimeoutMs: 50_000,
  fallbackPollMs: 2_000,
  maxFallbackPollMs: 8_000,
  safetyPollMs: 20_000,
}

export function buildWebSocketUrl(params: { token?: string | null; jobId?: string }): string {
  const origin = API_ORIGIN || window.location.origin
  const url = new URL(`${origin}${API_PREFIX}/ws`)
  url.protocol = url.protocol === 'https:' ? 'wss:' : 'ws:'
  if (params.jobId) url.searchParams.set('job_id', params.jobId)
  if (params.token) url.searchParams.set('token', params.token)
  return url.toString()
}

/** Every frame the server may send (`WsServerMessage` stays closed; part-aware types live in `partAwareTypes.ts`). */
type ServerMessage = WsServerMessage | PartAwareWsMessage

function parseMessage(raw: unknown): ServerMessage | null {
  if (typeof raw !== 'string') return null
  try {
    const value = JSON.parse(raw) as unknown
    if (typeof value === 'object' && value !== null && typeof (value as { type?: unknown }).type === 'string') {
      return value as ServerMessage
    }
  } catch {
    // Ignore malformed frames.
  }
  return null
}

export class RealtimeClient {
  private readonly options: RealtimeOptions & typeof DEFAULTS
  private readonly listeners = new Map<keyof RealtimeEventMap, Set<Listener<never>>>()
  private socket: WebSocket | null = null
  private status: RealtimeStatus = { state: 'idle', attempt: 0, nextRetryAt: null }
  private running = false
  private reconnectTimer: ReturnType<typeof setTimeout> | undefined
  private heartbeatTimer: ReturnType<typeof setTimeout> | undefined
  private pollTimer: ReturnType<typeof setTimeout> | undefined
  private pollFailures = 0
  private polling = false

  constructor(options: RealtimeOptions) {
    this.options = { ...DEFAULTS, ...options }
  }

  get currentStatus(): RealtimeStatus {
    return this.status
  }

  on<K extends keyof RealtimeEventMap>(type: K, listener: Listener<RealtimeEventMap[K]>): () => void {
    let set = this.listeners.get(type)
    if (!set) {
      set = new Set()
      this.listeners.set(type, set)
    }
    set.add(listener as Listener<never>)
    return () => {
      set.delete(listener as Listener<never>)
    }
  }

  start(): void {
    if (this.running) return
    this.running = true
    window.addEventListener('online', this.handleWake)
    document.addEventListener('visibilitychange', this.handleVisibility)
    this.connect()
    // The caller syncs immediately; the first poll only runs if the socket has not opened by then.
    this.schedulePoll(this.options.fallbackPollMs)
  }

  stop(): void {
    this.running = false
    window.removeEventListener('online', this.handleWake)
    document.removeEventListener('visibilitychange', this.handleVisibility)
    clearTimeout(this.reconnectTimer)
    clearTimeout(this.heartbeatTimer)
    clearTimeout(this.pollTimer)
    this.closeSocket(1000, 'client stopped')
    this.setStatus({ state: 'closed', attempt: 0, nextRetryAt: null })
  }

  /** Skip the backoff wait and reconnect (and poll) right away. */
  retryNow(): void {
    if (!this.running) return
    if (this.status.state !== 'open' && this.status.state !== 'connecting') {
      clearTimeout(this.reconnectTimer)
      this.connect()
    }
    this.pollFailures = 0
    this.schedulePoll(0)
  }

  // --------------------------------------------------------------------------------------------
  // Socket lifecycle
  // --------------------------------------------------------------------------------------------

  private connect(): void {
    if (!this.running) return
    this.closeSocket(1000, 'reconnecting')
    this.setStatus({ state: this.status.attempt > 0 ? 'reconnecting' : 'connecting', attempt: this.status.attempt, nextRetryAt: null })

    let socket: WebSocket
    try {
      socket = new WebSocket(buildWebSocketUrl({ token: this.options.getToken(), jobId: this.options.jobId }))
    } catch {
      this.scheduleReconnect()
      return
    }
    this.socket = socket

    socket.onopen = () => {
      if (this.socket !== socket) return
      this.pollFailures = 0
      this.setStatus({ state: 'open', attempt: 0, nextRetryAt: null })
      this.armHeartbeat()
      this.schedulePoll(this.options.safetyPollMs)
    }

    socket.onmessage = (event: MessageEvent) => {
      if (this.socket !== socket) return
      this.armHeartbeat()
      const message = parseMessage(event.data)
      if (message) this.dispatch(message)
    }

    socket.onerror = () => {
      // `onclose` always follows; reconnect is handled there.
    }

    socket.onclose = (event: CloseEvent) => {
      if (this.socket !== socket) return
      this.socket = null
      clearTimeout(this.heartbeatTimer)
      if (event.code === WS_CLOSE_AUTH_FAILED) {
        this.running = false
        clearTimeout(this.pollTimer)
        this.setStatus({ state: 'closed', attempt: 0, nextRetryAt: null })
        this.emit('auth-failure', undefined)
        return
      }
      if (!this.running) return
      this.scheduleReconnect()
      this.schedulePoll(0)
    }
  }

  private scheduleReconnect(): void {
    if (!this.running) return
    const attempt = this.status.attempt + 1
    const base = Math.min(this.options.maxBackoffMs, this.options.minBackoffMs * 2 ** (attempt - 1))
    const delay = Math.round(base * (0.75 + Math.random() * 0.5))
    clearTimeout(this.reconnectTimer)
    this.reconnectTimer = setTimeout(() => this.connect(), delay)
    this.setStatus({ state: 'reconnecting', attempt, nextRetryAt: Date.now() + delay })
  }

  private armHeartbeat(): void {
    clearTimeout(this.heartbeatTimer)
    this.heartbeatTimer = setTimeout(() => {
      // No traffic (not even pings): assume the connection is dead and recycle it.
      this.closeSocket(4000, 'heartbeat timeout')
      this.socket = null
      this.scheduleReconnect()
      this.schedulePoll(0)
    }, this.options.heartbeatTimeoutMs)
  }

  private closeSocket(code: number, reason: string): void {
    const socket = this.socket
    if (!socket) return
    this.socket = null
    socket.onopen = null
    socket.onmessage = null
    socket.onerror = null
    socket.onclose = null
    try {
      if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) socket.close(code, reason)
    } catch {
      // Ignore: socket already closing.
    }
  }

  private send(message: WsClientMessage): void {
    if (this.socket?.readyState === WebSocket.OPEN) this.socket.send(JSON.stringify(message))
  }

  private dispatch(message: ServerMessage): void {
    switch (message.type) {
      case 'hello':
        this.emit('hello', message.data.system)
        break
      case 'job.updated':
        this.emit('job.updated', message.data)
        break
      case 'system.updated':
        this.emit('system.updated', message.data)
        break
      case 'blender.updated':
        this.emit('blender.updated', message.data)
        break
      case 'tuning.updated':
        this.emit('tuning.updated', message.data)
        break
      case 'features.updated':
        this.emit('features.updated', message.data)
        break
      case 'image_generation.updated':
        this.emit('image_generation.updated', message.data)
        break
      case 'segmentation.updated':
        this.emit('segmentation.updated', message.data)
        break
      case 'part_analysis.updated':
        this.emit('part_analysis.updated', message.data)
        break
      case 'workflow.updated':
        this.emit('workflow.updated', message.data)
        break
      case 'ping':
        this.send({ type: 'pong' })
        break
      default:
        break
    }
  }

  // --------------------------------------------------------------------------------------------
  // Polling fallback
  // --------------------------------------------------------------------------------------------

  private schedulePoll(delay: number): void {
    if (!this.options.poll || !this.running) return
    clearTimeout(this.pollTimer)
    this.pollTimer = setTimeout(() => void this.runPoll(), delay)
  }

  private async runPoll(): Promise<void> {
    const poll = this.options.poll
    if (!poll || !this.running || this.polling) return
    const mode: PollMode = this.status.state === 'open' ? 'safety' : 'fallback'
    this.polling = true
    let ok = false
    try {
      ok = await poll(mode)
    } catch {
      ok = false
    } finally {
      this.polling = false
    }
    if (!this.running) return
    this.pollFailures = ok ? 0 : this.pollFailures + 1
    if (this.status.state === 'open') {
      this.schedulePoll(this.options.safetyPollMs)
    } else {
      const backoff = Math.min(this.options.maxFallbackPollMs, this.options.fallbackPollMs * 2 ** this.pollFailures)
      this.schedulePoll(ok ? this.options.fallbackPollMs : backoff)
    }
  }

  // --------------------------------------------------------------------------------------------
  // Events
  // --------------------------------------------------------------------------------------------

  private readonly handleWake = (): void => {
    this.retryNow()
  }

  private readonly handleVisibility = (): void => {
    if (document.visibilityState === 'visible') this.retryNow()
  }

  private setStatus(status: RealtimeStatus): void {
    this.status = status
    this.emit('status', status)
  }

  private emit<K extends keyof RealtimeEventMap>(type: K, payload: RealtimeEventMap[K]): void {
    const set = this.listeners.get(type)
    if (!set) return
    for (const listener of set) {
      try {
        ;(listener as Listener<RealtimeEventMap[K]>)(payload)
      } catch (error) {
        console.error(`[realtime] listener for "${type}" failed`, error)
      }
    }
  }
}
