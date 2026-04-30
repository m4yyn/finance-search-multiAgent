import {
  Bot,
  FileText,
  Globe2,
  Laptop,
  LogOut,
  MessageSquare,
  Plus,
  Send,
  Sparkles,
  Trash2,
} from 'lucide-react'
import { Suspense, lazy, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as chatApi from '../../api/chat'
import * as deepResearchApi from '../../api/deepResearch'
import * as knowledgeApi from '../../api/knowledge'
import { KnowledgeGraphView } from '../deepResearch/KnowledgeGraphView'
import { ReportChartsView } from '../deepResearch/ReportChartsView'
import { ResearchEventPanel } from '../deepResearch/ResearchEventPanel'
import { ResearchReportView } from '../deepResearch/ResearchReportView'
import { useAuth } from '../auth/AuthProvider'
import { KnowledgePanel } from '../knowledge/KnowledgePanel'
import { toSearchMode, toggleSearchMode, type SearchToggle } from './searchMode'
import type {
  ChatMessage,
  ChatReference,
  ChatSession,
  DeepResearchEvent,
  DocumentRecord,
  KnowledgeGraphPayload,
  KnowledgeBase,
  ResearchChart,
  ResearchReportReference,
  ResearchReportSection,
} from '../../types'

interface UiMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  references?: ChatReference[]
  researchMode?: boolean
  researchEvents?: DeepResearchEvent[]
  researchGraph?: KnowledgeGraphPayload | null
  researchCharts?: ResearchChart[]
  researchReport?: string
  researchSections?: ResearchReportSection[]
  researchReferences?: ResearchReportReference[]
  streaming?: boolean
}

const SAMPLE_PROMPTS = [
  '解释一下普通聊天、本地搜索和网络搜索分别适合什么场景',
  '用本地搜索分析我上传资料里的公司盈利能力',
  '搜索 2026 年 4 月 A 股市场的公开信息',
  '帮我设计一份上市公司年报分析框架',
]

const EChartsView = lazy(() =>
  import('../deepResearch/EChartsView').then((module) => ({
    default: module.EChartsView,
  })),
)

export function WorkspacePage() {
  const { user, logout } = useAuth()
  const [sidebarTab, setSidebarTab] = useState<'chat' | 'files'>('chat')
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<UiMessage[]>([])
  const [input, setInput] = useState('')
  const [searchToggle, setSearchToggle] = useState<SearchToggle>(null)
  const [deepResearchMode, setDeepResearchMode] = useState(false)
  const [streaming, setStreaming] = useState(false)
  const [deletingSessionId, setDeletingSessionId] = useState<string | null>(null)
  const [error, setError] = useState('')
  const [knowledgeBases, setKnowledgeBases] = useState<KnowledgeBase[]>([])
  const [selectedKbId, setSelectedKbId] = useState<string | null>(null)
  const [documents, setDocuments] = useState<DocumentRecord[]>([])
  const [documentsLoading, setDocumentsLoading] = useState(false)
  const messagesRef = useRef<HTMLDivElement | null>(null)
  const suppressNextHistoryLoadRef = useRef<string | null>(null)

  const searchMode = toSearchMode(searchToggle)

  const refreshSessions = useCallback(async () => {
    const nextSessions = await chatApi.listChatSessions()
    setSessions(nextSessions)
    if (!activeSessionId && nextSessions.length) {
      setActiveSessionId(nextSessions[0].id)
    }
  }, [activeSessionId])

  const refreshKnowledgeBases = useCallback(async () => {
    const nextBases = await knowledgeApi.listKnowledgeBases()
    setKnowledgeBases(nextBases)
    setSelectedKbId((current) => {
      if (current && nextBases.some((kb) => kb.id === current)) return current
      return nextBases[0]?.id ?? null
    })
  }, [])

  const setDocumentsStable = useCallback((nextDocuments: DocumentRecord[]) => {
    setDocuments(nextDocuments)
  }, [])

  useEffect(() => {
    void refreshSessions()
    void refreshKnowledgeBases()
  }, [refreshSessions, refreshKnowledgeBases])

  useEffect(() => {
    if (!activeSessionId) {
      setMessages([])
      return
    }
    if (suppressNextHistoryLoadRef.current === activeSessionId) {
      suppressNextHistoryLoadRef.current = null
      return
    }
    let active = true
    chatApi.getChatMessages(activeSessionId).then((history) => {
      if (!active) return
      setMessages(history.map(fromServerMessage))
    })
    return () => {
      active = false
    }
  }, [activeSessionId])

  useEffect(() => {
    if (!messagesRef.current?.scrollTo) return
    messagesRef.current.scrollTo({
      top: messagesRef.current.scrollHeight,
      behavior: 'smooth',
    })
  }, [messages, streaming])

  useEffect(() => {
    if (!selectedKbId) return
    const hasRunning = documents.some((document) =>
      ['pending', 'processing'].includes(document.status),
    )
    if (!hasRunning) return
    const timer = window.setInterval(async () => {
      setDocumentsLoading(true)
      try {
        setDocuments(await knowledgeApi.listDocuments(selectedKbId))
      } finally {
        setDocumentsLoading(false)
      }
    }, 3000)
    return () => window.clearInterval(timer)
  }, [selectedKbId, documents])

  async function createSession() {
    setError('')
    const created = await chatApi.createChatSession()
    await refreshSessions()
    setActiveSessionId(created.session_id)
    setMessages([])
  }

  async function deleteSession(sessionId: string) {
    if (deletingSessionId || (streaming && sessionId === activeSessionId)) return
    setError('')
    setDeletingSessionId(sessionId)
    try {
      await chatApi.deleteChatSession(sessionId)
      const remainingSessions = sessions.filter((session) => session.id !== sessionId)
      setSessions(remainingSessions)
      if (sessionId === activeSessionId) {
        const nextSessionId = remainingSessions[0]?.id ?? null
        setActiveSessionId(nextSessionId)
        setMessages([])
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除研究记录失败')
    } finally {
      setDeletingSessionId(null)
    }
  }

  async function sendMessage(prompt?: string) {
    const content = (prompt ?? input).trim()
    if (!content || streaming) return
    setError('')
    setInput('')
    setStreaming(true)

    try {
      let sessionId = activeSessionId
      if (!sessionId) {
        const created = await chatApi.createChatSession()
        sessionId = created.session_id
        suppressNextHistoryLoadRef.current = sessionId
        setActiveSessionId(sessionId)
        await refreshSessions()
      }

      const userMessage: UiMessage = {
        id: crypto.randomUUID(),
        role: 'user',
        content,
      }
      const assistantId = crypto.randomUUID()
      setMessages((current) => [
        ...current,
        userMessage,
        {
          id: assistantId,
          role: 'assistant',
          content: deepResearchMode ? 'Deep Research 正在执行…' : '',
          streaming: true,
          researchMode: deepResearchMode,
          researchEvents: deepResearchMode ? [] : undefined,
          researchGraph: deepResearchMode ? null : undefined,
          researchCharts: deepResearchMode ? [] : undefined,
          researchReport: deepResearchMode ? '' : undefined,
          researchSections: deepResearchMode ? [] : undefined,
          researchReferences: deepResearchMode ? [] : undefined,
        },
      ])

      if (deepResearchMode) {
        await streamDeepResearchMessage(sessionId, content, assistantId)
      } else {
        await chatApi.streamChatMessage(
          {
            session_id: sessionId,
            content,
            search_mode: searchMode,
          },
          (event) => {
            if (event.type === 'delta' && event.delta) {
              setMessages((current) =>
                current.map((message) =>
                  message.id === assistantId
                    ? { ...message, content: message.content + event.delta }
                    : message,
                ),
              )
            }
            if (event.type === 'done') {
              setMessages((current) =>
                current.map((message) =>
                  message.id === assistantId
                    ? {
                        ...message,
                        id: event.message_id ?? message.id,
                        streaming: false,
                        references: event.references ?? [],
                      }
                    : message,
                ),
              )
            }
            if (event.type === 'error') {
              throw new Error(event.error || '流式回答失败')
            }
          },
        )
      }
      await refreshSessions()
    } catch (err) {
      setError(err instanceof Error ? err.message : '发送失败')
      setMessages((current) =>
        current.map((message) =>
          message.streaming
            ? {
                ...message,
                streaming: false,
                content: message.content || '回答生成失败。',
              }
            : message,
        ),
      )
    } finally {
      setStreaming(false)
    }
  }

  async function streamDeepResearchMessage(
    sessionId: string,
    content: string,
    assistantId: string,
  ) {
    const searchFlags = getDeepResearchSearchFlags(searchToggle)
    await deepResearchApi.streamDeepResearch(
      {
        session_id: sessionId,
        content,
        search_web: searchFlags.search_web,
        search_local: searchFlags.search_local,
      },
      (event) => {
        appendResearchEvent(assistantId, event)
        if (event.type === 'done') {
          const summary = asRecord(asRecord(event.content).summary)
          const chartsCount = Number(summary.charts_count ?? 0)
          const reportChartsCount = Number(summary.report_charts_count ?? 0)
          const factsCount = Number(summary.facts_count ?? 0)
          const referencesCount = Number(summary.references_count ?? 0)
          const reportWordCount = Number(summary.report_word_count ?? 0)
          const hasQualityScore = summary.quality_score !== undefined && summary.quality_score !== null
          const qualityScore = hasQualityScore ? Number(summary.quality_score) : null
          const unresolvedIssues = Number(summary.unresolved_issues ?? 0)
          const reportChartText = reportChartsCount
            ? `，其中 ${reportChartsCount} 个可用于报告`
            : ''
          const reportText = reportWordCount
            ? `，报告 ${reportWordCount} 字符，引用 ${referencesCount} 个来源`
            : ''
          const reviewText =
            qualityScore !== null && Number.isFinite(qualityScore) ? `，质量分 ${qualityScore}/10` : ''
          const issueText = unresolvedIssues ? `，未解决问题 ${unresolvedIssues} 个` : ''
          setMessages((current) =>
            current.map((message) =>
              message.id === assistantId
                ? {
                    ...message,
                    streaming: false,
                    content: `Deep Research 已完成：沉淀 ${factsCount} 条事实，生成 ${chartsCount} 个图表${reportChartText}${reportText}${reviewText}${issueText}。`,
                  }
                : message,
            ),
          )
        }
        if (event.type === 'error') {
          throw new Error(event.error || 'Deep Research 执行失败')
        }
      },
    )
  }

  function appendResearchEvent(assistantId: string, event: DeepResearchEvent) {
    setMessages((current) =>
      current.map((message) => {
        if (message.id !== assistantId) return message
        const nextEvents = [...(message.researchEvents ?? []), event]
        const resumeState = getResumeUiState(event)
        const nextGraph = resumeState?.graph ?? getEventKnowledgeGraph(event) ?? message.researchGraph ?? null
        const eventCharts = resumeState?.charts ?? getEventCharts(event)
        const eventSection = getEventSection(event)
        const eventReport = resumeState?.report ?? getEventReport(event)
        const eventReferences = resumeState?.references ?? getEventReferences(event)
        const nextCharts = eventCharts
          ? mergeResearchCharts(message.researchCharts ?? [], eventCharts)
          : message.researchCharts ?? []
        const nextSections = eventSection
          ? mergeResearchSections(message.researchSections ?? [], [eventSection])
          : message.researchSections ?? []
        const nextReferences = eventReferences
          ? mergeResearchReferences(message.researchReferences ?? [], eventReferences)
          : message.researchReferences ?? []
        return {
          ...message,
          researchEvents: nextEvents,
          researchGraph: nextGraph,
          researchCharts: nextCharts,
          researchReport: eventReport ?? message.researchReport ?? '',
          researchSections: nextSections,
          researchReferences: nextReferences,
        }
      }),
    )
  }

  const activeSession = useMemo(
    () => sessions.find((session) => session.id === activeSessionId),
    [sessions, activeSessionId],
  )

  return (
    <div className="workspace">
      <aside className="sidebar">
        <div className="brand-row sidebar-brand">
          <div className="logo-mark">FR</div>
          <div>
            <div className="brand-title">Finance Research</div>
            <div className="brand-sub">Multi-Agent 深度研究</div>
          </div>
        </div>

        <div className="sidebar-tabs">
          <button
            className={sidebarTab === 'chat' ? 'active' : ''}
            onClick={() => setSidebarTab('chat')}
          >
            <MessageSquare size={14} /> Chat
          </button>
          <button
            className={sidebarTab === 'files' ? 'active' : ''}
            onClick={() => setSidebarTab('files')}
          >
            <FileText size={14} /> 文件
          </button>
        </div>

        {sidebarTab === 'chat' ? (
          <>
            <button className="new-chat" onClick={() => void createSession()}>
              <Plus size={14} /> 新建研究
            </button>
            <div className="sidebar-label">研究记录</div>
            <div className="session-list">
              {sessions.map((session) => (
                <div
                  key={session.id}
                  className={`session-item ${session.id === activeSessionId ? 'active' : ''}`}
                >
                  <button
                    className="session-select"
                    onClick={() => setActiveSessionId(session.id)}
                    type="button"
                  >
                    <span>{session.title}</span>
                    <small>{formatDate(session.updated_at)}</small>
                  </button>
                  <button
                    className="session-delete"
                    onClick={() => void deleteSession(session.id)}
                    type="button"
                    aria-label={`删除研究记录 ${session.title}`}
                    title="删除研究记录"
                    disabled={
                      deletingSessionId === session.id ||
                      (streaming && session.id === activeSessionId)
                    }
                  >
                    {deletingSessionId === session.id ? (
                      <span className="mini-spinner" />
                    ) : (
                      <Trash2 size={14} />
                    )}
                  </button>
                </div>
              ))}
              {!sessions.length && <div className="empty-note">暂无研究记录</div>}
            </div>
          </>
        ) : (
          <KnowledgePanel
            knowledgeBases={knowledgeBases}
            selectedKbId={selectedKbId}
            documents={documents}
            loading={documentsLoading}
            onRefresh={refreshKnowledgeBases}
            onSelectKb={setSelectedKbId}
            onDocumentsChange={setDocumentsStable}
          />
        )}

        <div className="sidebar-footer">
          <div className="avatar-circle">{user?.username.slice(0, 1) ?? '研'}</div>
          <div className="avatar-name">{user?.username ?? '研究员账户'}</div>
          <button className="logout-btn" onClick={() => void logout()} aria-label="退出登录">
            <LogOut size={15} />
          </button>
        </div>
      </aside>

      <main className="main">
        <header className="topbar">
          <div>
            <h1>{activeSession?.title ?? 'Finance Research Assistant'}</h1>
            <p>实时检索 · 本地知识库 · 联网搜索 · 报告工作流</p>
          </div>
          <div className="topbar-actions">
            {searchToggle && (
              <span className="mode-pill">
                {searchToggle === 'web' ? '网络搜索' : '本地搜索'}
              </span>
            )}
            {deepResearchMode && <span className="mode-pill research-pill">Deep Research</span>}
            <span className="status-badge">
              <span className="live-dot" /> API Connected
            </span>
          </div>
        </header>

        <section className="messages" ref={messagesRef}>
          {!messages.length ? (
            <div className="empty-state">
              <div className="empty-logo">FR</div>
              <h2>Finance Research Assistant</h2>
              <p>为投研分析师准备的金融资料检索、知识库问答和研究报告生成入口。</p>
              <div className="sample-list">
                {SAMPLE_PROMPTS.map((prompt) => (
                  <button key={prompt} onClick={() => void sendMessage(prompt)}>
                    {prompt}
                  </button>
                ))}
              </div>
            </div>
          ) : (
            messages.map((message) => (
              <MessageBubble key={message.id} message={message} />
            ))
          )}
        </section>

        {error && <div className="chat-error">{error}</div>}

        <footer className="composer">
          <div className="mode-row">
            <div className="search-toggle">
              <button
                className={searchToggle === 'local' ? 'active' : ''}
                onClick={() => setSearchToggle((current) => toggleSearchMode(current, 'local'))}
                type="button"
              >
                <Laptop size={14} /> 本地搜索
              </button>
              <button
                className={searchToggle === 'web' ? 'active' : ''}
                onClick={() => setSearchToggle((current) => toggleSearchMode(current, 'web'))}
                type="button"
              >
                <Globe2 size={14} /> 网络搜索
              </button>
            </div>
            <button
              className={`deep-research ${deepResearchMode ? 'active' : ''}`}
              type="button"
              aria-pressed={deepResearchMode}
              onClick={() => setDeepResearchMode((current) => !current)}
              disabled={streaming}
            >
              <Sparkles size={14} /> Deep Research
            </button>
          </div>

          <div className="input-box">
            <textarea
              value={input}
              onChange={(event) => setInput(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter' && !event.shiftKey) {
                  event.preventDefault()
                  void sendMessage()
                }
              }}
              placeholder="输入研究问题。Enter 发送，Shift+Enter 换行。"
              disabled={streaming}
            />
            <button
              className="send-btn"
              disabled={!input.trim() || streaming}
              onClick={() => void sendMessage()}
              aria-label="发送"
            >
              {streaming ? <span className="mini-spinner" /> : <Send size={16} />}
            </button>
          </div>
        </footer>
      </main>
    </div>
  )
}

function MessageBubble({ message }: { message: UiMessage }) {
  const interactiveCharts = (message.researchCharts ?? []).filter(
    (chart) => chart.echarts_option,
  )
  const reportCharts = (message.researchCharts ?? []).filter((chart) => chart.image_base64)
  const hasReportPreview = Boolean(
    message.researchReport?.trim() || message.researchSections?.length,
  )

  if (message.role === 'user') {
    return (
      <div className="msg-row user-row">
        <div className="user-bubble">{message.content}</div>
      </div>
    )
  }
  return (
    <div className="msg-row assistant-row">
      <div className="assistant-avatar">
        <Bot size={15} />
      </div>
      <div className="assistant-body">
        <div className="assistant-text">
          {message.content || (message.streaming ? '正在生成回答…' : '')}
          {message.streaming && <span className="cursor" />}
        </div>
        {message.researchMode && (
          <div className="research-output">
            <ResearchEventPanel events={message.researchEvents ?? []} />
            {message.researchGraph && <KnowledgeGraphView graph={message.researchGraph} />}
            {!!interactiveCharts.length && (
              <Suspense fallback={<div className="empty-note">图表加载中…</div>}>
                <EChartsView charts={interactiveCharts} />
              </Suspense>
            )}
            {!!reportCharts.length && <ReportChartsView charts={reportCharts} />}
            {hasReportPreview && (
              <ResearchReportView
                report={message.researchReport ?? ''}
                sections={message.researchSections ?? []}
                references={message.researchReferences ?? []}
              />
            )}
          </div>
        )}
        {!!message.references?.length && <ReferenceList references={message.references} />}
      </div>
    </div>
  )
}

function ReferenceList({ references }: { references: ChatReference[] }) {
  return (
    <div className="references">
      <div className="reference-title">引用来源</div>
      {references.map((reference) => (
        <a
          className="reference-item"
          key={`${reference.index}-${reference.url ?? reference.chunk_id}`}
          href={reference.url ?? undefined}
          target={reference.url ? '_blank' : undefined}
          rel="noreferrer"
        >
          <span className="reference-index">[{reference.index}]</span>
          <span>
            <strong>{reference.filename || reference.site_name || '本地资料'}</strong>
            <small>
              {reference.source_type === 'web'
                ? reference.site_name || reference.display_url || reference.url
                : `chunk ${reference.chunk_index ?? reference.chunk_id ?? '-'}`}
            </small>
          </span>
        </a>
      ))}
    </div>
  )
}

function fromServerMessage(message: ChatMessage): UiMessage {
  return {
    id: message.id,
    role: message.role === 'user' ? 'user' : 'assistant',
    content: message.content,
  }
}

function formatDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return ''
  return new Intl.DateTimeFormat('zh-CN', {
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  }).format(date)
}

function getDeepResearchSearchFlags(searchToggle: SearchToggle): {
  search_web: boolean
  search_local: boolean
} {
  if (searchToggle === 'local') {
    return { search_web: false, search_local: true }
  }
  return { search_web: true, search_local: false }
}

function getEventKnowledgeGraph(event: DeepResearchEvent): KnowledgeGraphPayload | null {
  const content = asRecord(event.content)
  const graph = asRecord(content.graph ?? content.knowledge_graph)
  return normalizeKnowledgeGraph(graph)
}

function normalizeKnowledgeGraph(graph: Record<string, unknown>): KnowledgeGraphPayload | null {
  const nodes = graph.nodes
  const edges = graph.edges
  if (!Array.isArray(nodes) || !Array.isArray(edges)) return null
  return {
    nodes: nodes
      .map((node) => asRecord(node))
      .filter((node) => typeof node.id === 'string')
      .map((node) => ({
        id: String(node.id),
        label: typeof node.label === 'string' ? node.label : undefined,
        name: typeof node.name === 'string' ? node.name : undefined,
        type: typeof node.type === 'string' ? node.type : undefined,
        importance: typeof node.importance === 'number' ? node.importance : undefined,
        size: typeof node.size === 'number' ? node.size : undefined,
        summary: typeof node.summary === 'string' ? node.summary : undefined,
      })),
    edges: edges
      .map((edge) => asRecord(edge))
      .filter((edge) => typeof edge.source === 'string' && typeof edge.target === 'string')
      .map((edge) => ({
        source: String(edge.source),
        target: String(edge.target),
        type: typeof edge.type === 'string' ? edge.type : undefined,
        relation: typeof edge.relation === 'string' ? edge.relation : undefined,
        weight: typeof edge.weight === 'number' ? edge.weight : undefined,
        description: typeof edge.description === 'string' ? edge.description : undefined,
      })),
  }
}

function getEventCharts(event: DeepResearchEvent): ResearchChart[] | null {
  const content = asRecord(event.content)
  const rawCharts = Array.isArray(content.charts)
    ? content.charts
    : content.chart
      ? [content.chart]
      : event.type === 'chart' && typeof content.image_base64 === 'string'
        ? [content]
        : null
  return normalizeResearchCharts(rawCharts)
}

function normalizeResearchCharts(rawCharts: unknown[] | null): ResearchChart[] | null {
  if (!rawCharts) return null
  return rawCharts
    .map((chart) => asRecord(chart))
    .filter((chart) => typeof chart.id === 'string' && typeof chart.title === 'string')
    .map((chart) => ({
      id: String(chart.id),
      title: String(chart.title),
      description: typeof chart.description === 'string' ? chart.description : undefined,
      chart_type: typeof chart.chart_type === 'string' ? chart.chart_type : undefined,
      type: typeof chart.type === 'string' ? chart.type : undefined,
      artifact_type:
        typeof chart.artifact_type === 'string' ? chart.artifact_type : undefined,
      section_id: typeof chart.section_id === 'string' ? chart.section_id : null,
      data: asOptionalRecord(chart.data),
      echarts_option: asOptionalRecord(chart.echarts_option),
      image_base64:
        typeof chart.image_base64 === 'string' ? chart.image_base64 : undefined,
      code: typeof chart.code === 'string' ? chart.code : undefined,
      metadata: asOptionalRecord(chart.metadata),
    }))
}

function getEventSection(event: DeepResearchEvent): ResearchReportSection | null {
  if (event.type !== 'section_content') return null
  const content = asRecord(event.content)
  if (typeof content.content !== 'string') return null
  const id = typeof content.section_id === 'string' ? content.section_id : 'section'
  return {
    id,
    title: typeof content.section_title === 'string' ? content.section_title : id,
    content: content.content,
    word_count: typeof content.word_count === 'number' ? content.word_count : undefined,
    key_points: Array.isArray(content.key_points)
      ? content.key_points.map(String).filter(Boolean)
      : undefined,
  }
}

function getEventReport(event: DeepResearchEvent): string | null {
  const content = asRecord(event.content)
  if (event.type === 'report_draft' && typeof content.content === 'string') {
    return content.content
  }
  if (event.type === 'done' && typeof content.final_report === 'string') {
    return content.final_report
  }
  return null
}

function getEventReferences(event: DeepResearchEvent): ResearchReportReference[] | null {
  const content = asRecord(event.content)
  const rawReferences =
    Array.isArray(content.references)
      ? content.references
      : event.type === 'report_draft' && Array.isArray(content.report_references)
        ? content.report_references
        : null
  return normalizeResearchReferences(rawReferences)
}

function getResumeUiState(event: DeepResearchEvent): {
  graph?: KnowledgeGraphPayload | null
  charts?: ResearchChart[] | null
  report?: string
  references?: ResearchReportReference[] | null
} | null {
  if (event.type !== 'research_resumed') return null
  const content = asRecord(event.content)
  const uiState = asRecord(content.ui_state)
  if (!Object.keys(uiState).length) return null
  const report =
    typeof uiState.final_report === 'string'
      ? uiState.final_report
      : typeof uiState.streaming_report === 'string'
        ? uiState.streaming_report
        : undefined
  return {
    graph: normalizeKnowledgeGraph(asRecord(uiState.knowledge_graph)),
    charts: normalizeResearchCharts(Array.isArray(uiState.charts) ? uiState.charts : null),
    report,
    references: normalizeResearchReferences(
      Array.isArray(uiState.references) ? uiState.references : null,
    ),
  }
}

function normalizeResearchReferences(rawReferences: unknown[] | null): ResearchReportReference[] | null {
  if (!rawReferences) return null
  return rawReferences
    .map((reference) => asRecord(reference))
    .filter((reference) => reference.source || reference.title || reference.url || reference.link)
    .map((reference) => ({
      id:
        typeof reference.id === 'string' || typeof reference.id === 'number'
          ? reference.id
          : undefined,
      source: typeof reference.source === 'string' ? reference.source : undefined,
      title: typeof reference.title === 'string' ? reference.title : undefined,
      url:
        typeof reference.url === 'string'
          ? reference.url
          : typeof reference.link === 'string'
            ? reference.link
            : undefined,
      author: typeof reference.author === 'string' ? reference.author : undefined,
      date: typeof reference.date === 'string' ? reference.date : undefined,
    }))
}

function mergeResearchCharts(
  existingCharts: ResearchChart[],
  incomingCharts: ResearchChart[],
): ResearchChart[] {
  const byId = new Map(existingCharts.map((chart) => [chart.id, chart]))
  for (const chart of incomingCharts) {
    byId.set(chart.id, chart)
  }
  return Array.from(byId.values())
}

function mergeResearchSections(
  existingSections: ResearchReportSection[],
  incomingSections: ResearchReportSection[],
): ResearchReportSection[] {
  const byId = new Map(existingSections.map((section) => [section.id, section]))
  for (const section of incomingSections) {
    byId.set(section.id, section)
  }
  return Array.from(byId.values())
}

function mergeResearchReferences(
  existingReferences: ResearchReportReference[],
  incomingReferences: ResearchReportReference[],
): ResearchReportReference[] {
  const byKey = new Map(
    existingReferences.map((reference) => [
      `${reference.source ?? reference.title ?? ''}|${reference.url ?? ''}`,
      reference,
    ]),
  )
  for (const reference of incomingReferences) {
    const key = `${reference.source ?? reference.title ?? ''}|${reference.url ?? ''}`
    byKey.set(key, reference)
  }
  return Array.from(byKey.values())
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}

function asOptionalRecord(value: unknown): Record<string, unknown> | undefined {
  return value !== null && typeof value === 'object'
    ? (value as Record<string, unknown>)
    : undefined
}
