import { apiRequest } from './client'
import type { TokenResponse, User } from '../types'

export interface LoginPayload {
  username_or_email: string
  password: string
}

export interface RegisterPayload {
  username: string
  email: string
  password: string
}

export function login(payload: LoginPayload): Promise<TokenResponse> {
  return apiRequest<TokenResponse>('/auth/login', {
    method: 'POST',
    body: payload,
    auth: false,
  })
}

export function register(payload: RegisterPayload): Promise<User> {
  return apiRequest<User>('/auth/register', {
    method: 'POST',
    body: payload,
    auth: false,
  })
}

export function getMe(): Promise<User> {
  return apiRequest<User>('/auth/me')
}

export function logout(): Promise<void> {
  return apiRequest<void>('/auth/logout', { method: 'POST' })
}
