/** Auth endpoints (§7.1). Public: status, login, logout. */
import { apiClient } from './client'
import type { AuthStatus, LoginRequest, LoginResponse } from './types'

export async function getAuthStatus(): Promise<AuthStatus> {
  const response = await apiClient.get<AuthStatus>('/auth/status')
  return response.data
}

export async function login(credentials: LoginRequest): Promise<LoginResponse> {
  const response = await apiClient.post<LoginResponse>('/auth/login', credentials)
  return response.data
}

export async function logout(): Promise<void> {
  await apiClient.post('/auth/logout')
}
