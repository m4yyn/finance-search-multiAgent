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
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import * as chatApi from '../../api/chat'
import * as knowledgeApi from '../../api/knowledge'
import { useAuth } from '../auth/AuthProvider'
import { KnowledgePanel } from '../knowledge/KnowledgePanel'
import { toSearchMode, toggleSearchMode, type SearchToggle } from './searchMode'
import type {
  ChatMessage,
  ChatReference,
  ChatSession,
  DocumentRecord,
  KnowledgeBase,
} from '../../types'

interface UiMessage {
  id: string
  role: 'user' | 'assistant'
  content: string
  references?: ChatReference[]
  streaming?: boolean
}

const SAMPLE_PROMPTS = [
  '解释一下普通聊天、本地搜索和网络搜索分别适合什么场景',
  '用本地搜索分析我上传资料里的公司盈利能力',
  '搜索 2026 年 4 月 A 股市场的公开信息',
  '帮我设计一份上市公司年报分析框架',
]

export function WorkspacePage() {
  const { user, logout } = useAuth()
  const [sidebarTab, setSidebarTab] = useState<'chat' | 'files'>('chat')
  const [sessions, setSessions] = useState<ChatSession[]>([])
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null)
  const [messages, setMessages] = useState<UiMessage[]>([])
  const [input, setInput] = useState('')
  const [searchToggle, setSearchToggle] = useState<SearchToggle>(null)
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
    messagesRef.current?.scrollTo({
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
        { id: assistantId, role: 'assistant', content: '', streaming: true },
      ])

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
      await refreshSessions()
    } catch (err) {
      setError(err instanceof Error ? err.message : '发送失败')
      setMessages((current) =>
        current.map((message) =>
          message.streaming
            ? { ...message, streaming: false, content: message.content || '回答生成失败。' }
            : message,
        ),
      )
    } finally {
      setStreaming(false)
    }
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
            <button className="deep-research" type="button" disabled>
              <Sparkles size={14} /> Deep Research · Coming soon
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
