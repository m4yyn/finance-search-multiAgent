import { apiRequest } from './client'
import type { DocumentRecord, KnowledgeBase } from '../types'

export function createKnowledgeBase(payload: {
  name: string
  description?: string
}): Promise<KnowledgeBase> {
  return apiRequest<KnowledgeBase>('/knowledge/bases', {
    method: 'POST',
    body: payload,
  })
}

export function listKnowledgeBases(): Promise<KnowledgeBase[]> {
  return apiRequest<KnowledgeBase[]>('/knowledge/bases')
}

export function deleteKnowledgeBase(kbId: string): Promise<void> {
  return apiRequest<void>(`/knowledge/bases/${kbId}`, { method: 'DELETE' })
}

export function listDocuments(kbId: string): Promise<DocumentRecord[]> {
  return apiRequest<DocumentRecord[]>(`/knowledge/bases/${kbId}/documents`)
}

export function uploadDocument(
  kbId: string,
  file: File,
): Promise<DocumentRecord> {
  const formData = new FormData()
  formData.append('file', file)
  return apiRequest<DocumentRecord>(`/knowledge/bases/${kbId}/documents`, {
    method: 'POST',
    body: formData,
  })
}

export function deleteDocument(kbId: string, docId: string): Promise<void> {
  return apiRequest<void>(`/knowledge/bases/${kbId}/documents/${docId}`, {
    method: 'DELETE',
  })
}
