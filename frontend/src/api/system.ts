/** System endpoints (§7.2). */
import { apiClient } from './client'
import type { HealthResponse, SystemStatus } from './types'

export async function getHealth(): Promise<HealthResponse> {
  const response = await apiClient.get<HealthResponse>('/health')
  return response.data
}

export async function getSystemStatus(): Promise<SystemStatus> {
  const response = await apiClient.get<SystemStatus>('/system')
  return response.data
}

/** Starts a background model load (202). Returns the current system status. */
export async function loadEngine(): Promise<SystemStatus> {
  const response = await apiClient.post<SystemStatus>('/system/engine/load')
  return response.data
}

/** Unloads the model (409 while a job is running). */
export async function unloadEngine(): Promise<SystemStatus> {
  const response = await apiClient.post<SystemStatus>('/system/engine/unload')
  return response.data
}
