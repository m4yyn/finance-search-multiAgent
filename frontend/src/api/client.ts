export const API_BASE_URL =
  import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:8000/api/v1'

export const TOKEN_STORAGE_KEY = 'finance-research-token'

export class ApiError extends Error {
  status: number
  details: unknown

  constructor(status: number, message: string, details?: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.details = details
  }
}

export function getStoredToken(): string | null {
  return window.localStorage.getItem(TOKEN_STORAGE_KEY)
}

export function setStoredToken(token: string): void {
  window.localStorage.setItem(TOKEN_STORAGE_KEY, token)
}

export function clearStoredToken(): void {
  window.localStorage.removeItem(TOKEN_STORAGE_KEY)
}

interface RequestOptions extends Omit<RequestInit, 'body'> {
  body?: unknown
  auth?: boolean
}

export async function apiRequest<T>(
  path: string,
  options: RequestOptions = {},
): Promise<T> {
  const headers = new Headers(options.headers)
  const token = getStoredToken()

  if (!(options.body instanceof FormData) && options.body !== undefined) {
    headers.set('Content-Type', 'application/json')
  }
  if (options.auth !== false && token) {
    headers.set('Authorization', `Bearer ${token}`)
  }

  const response = await fetch(`${API_BASE_URL}${path}`, {
    ...options,
    headers,
    body:
      options.body instanceof FormData
        ? options.body
        : options.body === undefined
          ? undefined
          : JSON.stringify(options.body),
  })

  if (response.status === 401) {
    clearStoredToken()
  }

  if (!response.ok) {
    let details: unknown
    try {
      details = await response.json()
    } catch {
      details = await response.text()
    }
    const message =
      typeof details === 'object' &&
      details !== null &&
      'detail' in details &&
      typeof details.detail === 'string'
        ? details.detail
        : `Request failed with status ${response.status}`
    throw new ApiError(response.status, message, details)
  }

  if (response.status === 204) {
    return undefined as T
  }
  return (await response.json()) as T
}
