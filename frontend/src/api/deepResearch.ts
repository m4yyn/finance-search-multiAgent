import { API_BASE_URL, getStoredToken } from './client'
import { parseSseStream } from '../lib/sse'
import type { DeepResearchEvent } from '../types'

interface DeepResearchStreamPayload {
  session_id: string
  content: string
  search_web: boolean
  search_local: boolean
  resume?: boolean
}

export async function streamDeepResearch(
  payload: DeepResearchStreamPayload,
  onEvent: (event: DeepResearchEvent) => void,
): Promise<void> {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const token = getStoredToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`${API_BASE_URL}/deep-research/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })

  if (!response.ok || !response.body) {
    throw new Error(`Deep Research stream failed with status ${response.status}`)
  }
  await parseSseStream<DeepResearchEvent>(response.body, onEvent)
}
