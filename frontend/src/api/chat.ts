import { API_BASE_URL, getStoredToken } from './client'
import { apiRequest } from './client'
import { parseSseStream } from '../lib/sse'
import type {
  ChatMessage,
  ChatSession,
  ChatSessionCreated,
  ChatSseEvent,
  SearchMode,
} from '../types'

export function createChatSession(title?: string): Promise<ChatSessionCreated> {
  return apiRequest<ChatSessionCreated>('/chat/session', {
    method: 'POST',
    body: { title },
  })
}

export function listChatSessions(): Promise<ChatSession[]> {
  return apiRequest<ChatSession[]>('/chat/sessions')
}

export function getChatMessages(sessionId: string): Promise<ChatMessage[]> {
  return apiRequest<ChatMessage[]>(`/chat/session/${sessionId}/messages`)
}

export function deleteChatSession(sessionId: string): Promise<void> {
  return apiRequest<void>(`/chat/session/${sessionId}`, {
    method: 'DELETE',
  })
}

export async function streamChatMessage(
  payload: { session_id: string; content: string; search_mode: SearchMode },
  onEvent: (event: ChatSseEvent) => void,
): Promise<void> {
  const headers = new Headers({ 'Content-Type': 'application/json' })
  const token = getStoredToken()
  if (token) headers.set('Authorization', `Bearer ${token}`)

  const response = await fetch(`${API_BASE_URL}/chat/stream`, {
    method: 'POST',
    headers,
    body: JSON.stringify(payload),
  })

  if (!response.ok || !response.body) {
    throw new Error(`Chat stream failed with status ${response.status}`)
  }
  await parseSseStream<ChatSseEvent>(response.body, onEvent)
}
