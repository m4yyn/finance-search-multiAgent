export type SearchMode = 'none' | 'local' | 'web'

export interface User {
  id: string
  username: string
  email: string
  is_active: boolean
  is_superuser: boolean
  created_at: string
  updated_at: string
}

export interface TokenResponse {
  access_token: string
  token_type: string
}

export interface ChatSession {
  id: string
  user_id: string
  title: string
  created_at: string
  updated_at: string
  is_active: boolean
}

export interface ChatSessionCreated {
  session_id: string
  title: string
}

export interface ChatMessage {
  id: string
  session_id: string
  role: 'user' | 'assistant' | 'system'
  content: string
  tokens: number | null
  created_at: string
}

export interface ChatReference {
  index: number
  content: string
  filename: string
  score: number
  source_type: 'local' | 'web'
  kb_id?: string | null
  document_id?: string | null
  chunk_id?: string | null
  chunk_index?: number | null
  page_number?: number | null
  row_number?: number | null
  sheet_name?: string | null
  row_start?: number | null
  row_end?: number | null
  url?: string | null
  site_name?: string | null
  site_icon?: string | null
  date_published?: string | null
  display_url?: string | null
}

export interface ChatSseEvent {
  type: 'delta' | 'done' | 'error'
  session_id: string
  message_id?: string
  delta?: string
  done?: boolean
  error?: string
  references?: ChatReference[]
}

export interface KnowledgeBase {
  id: string
  user_id: string
  name: string
  description: string | null
  collection_name: string
  created_at: string
  updated_at: string
}

export type DocumentStatus = 'pending' | 'processing' | 'success' | 'failed'

export interface DocumentRecord {
  id: string
  kb_id: string
  filename: string
  file_size: number
  mime_type: string
  status: DocumentStatus
  chunk_count: number | null
  error_message: string | null
  created_at: string
  updated_at: string
}

export interface WebSearchResult {
  index: number
  title: string
  url: string
  snippet: string
  summary: string
  site_name: string | null
  site_icon: string | null
  date_published: string | null
  display_url: string | null
}

export interface WebSearchResponse {
  query: string
  count: number
  freshness: string
  summary: boolean
  cached: boolean
  results: WebSearchResult[]
}

export interface DeepResearchEvent {
  type: string
  session_id?: string
  agent?: string
  phase?: string | null
  timestamp?: string
  content?: unknown
  metadata?: Record<string, unknown>
  done?: boolean
  error?: string | null
  checkpoint_id?: string | null
  status?: 'running' | 'completed' | 'failed' | string | null
}

export interface ResearchStepEvent {
  step_id?: string
  step_type?: string
  title?: string
  subtitle?: string
  status?: 'running' | 'completed' | 'failed' | string
  started_at?: string
  completed_at?: string
  stats?: Record<string, unknown>
}

export interface KnowledgeGraphNode {
  id: string
  label?: string
  display_label?: string
  name?: string
  type?: string
  importance?: number
  size?: number
  summary?: string
}

export interface KnowledgeGraphEdge {
  source: string
  target: string
  type?: string
  relation?: string
  weight?: number
  description?: string
}

export interface KnowledgeGraphPayload {
  nodes: KnowledgeGraphNode[]
  edges: KnowledgeGraphEdge[]
}

export interface ResearchChart {
  id: string
  title: string
  description?: string
  chart_type?: string
  type?: string
  artifact_type?: string
  section_id?: string | null
  data?: Record<string, unknown>
  echarts_option?: Record<string, unknown>
  image_base64?: string
  code?: string
  metadata?: Record<string, unknown>
}

export interface ResearchReportSection {
  id: string
  title: string
  content: string
  word_count?: number
  key_points?: string[]
}

export interface ResearchReportReference {
  id?: string | number
  source?: string
  title?: string
  url?: string
  author?: string
  date?: string
}
