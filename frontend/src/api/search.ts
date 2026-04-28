import { apiRequest } from './client'
import type { WebSearchResponse } from '../types'

export function searchWeb(query: string, count = 5): Promise<WebSearchResponse> {
  return apiRequest<WebSearchResponse>('/search/web', {
    method: 'POST',
    body: { query, count },
  })
}
