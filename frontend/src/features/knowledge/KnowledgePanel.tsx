import { FileText, Plus, Trash2, UploadCloud } from 'lucide-react'
import {
  useEffect,
  useRef,
  useState,
  type ChangeEvent,
  type DragEvent,
  type FormEvent,
} from 'react'
import * as knowledgeApi from '../../api/knowledge'
import type { DocumentRecord, KnowledgeBase } from '../../types'

interface KnowledgePanelProps {
  knowledgeBases: KnowledgeBase[]
  selectedKbId: string | null
  documents: DocumentRecord[]
  loading: boolean
  onRefresh: () => Promise<void>
  onSelectKb: (kbId: string) => void
  onDocumentsChange: (documents: DocumentRecord[]) => void
}

export function KnowledgePanel({
  knowledgeBases,
  selectedKbId,
  documents,
  loading,
  onRefresh,
  onSelectKb,
  onDocumentsChange,
}: KnowledgePanelProps) {
  const inputRef = useRef<HTMLInputElement | null>(null)
  const [kbName, setKbName] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const selectedKb = knowledgeBases.find((kb) => kb.id === selectedKbId)

  useEffect(() => {
    if (!selectedKbId) {
      onDocumentsChange([])
      return
    }
    let active = true
    const load = async () => {
      const nextDocuments = await knowledgeApi.listDocuments(selectedKbId)
      if (active) onDocumentsChange(nextDocuments)
    }
    void load()
    return () => {
      active = false
    }
  }, [selectedKbId, onDocumentsChange])

  async function createKb(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const name = kbName.trim()
    if (!name) return
    setBusy(true)
    setError('')
    try {
      const kb = await knowledgeApi.createKnowledgeBase({ name })
      setKbName('')
      await onRefresh()
      onSelectKb(kb.id)
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建知识库失败')
    } finally {
      setBusy(false)
    }
  }

  async function removeKb(kbId: string) {
    setBusy(true)
    setError('')
    try {
      await knowledgeApi.deleteKnowledgeBase(kbId)
      await onRefresh()
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除知识库失败')
    } finally {
      setBusy(false)
    }
  }

  async function uploadFiles(files: FileList | File[]) {
    if (!selectedKbId) {
      setError('请先创建或选择一个知识库')
      return
    }
    const accepted = Array.from(files).filter((file) =>
      /\.(pdf|xlsx|xlsm|xls)$/i.test(file.name),
    )
    if (!accepted.length) {
      setError('仅支持 PDF、XLSX、XLSM、XLS 文件')
      return
    }
    setBusy(true)
    setError('')
    try {
      const uploaded: DocumentRecord[] = []
      for (const file of accepted) {
        uploaded.push(await knowledgeApi.uploadDocument(selectedKbId, file))
      }
      onDocumentsChange([...uploaded, ...documents])
    } catch (err) {
      setError(err instanceof Error ? err.message : '上传失败')
    } finally {
      setBusy(false)
      if (inputRef.current) inputRef.current.value = ''
    }
  }

  async function removeDocument(documentId: string) {
    if (!selectedKbId) return
    setBusy(true)
    setError('')
    try {
      await knowledgeApi.deleteDocument(selectedKbId, documentId)
      onDocumentsChange(documents.filter((item) => item.id !== documentId))
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除文档失败')
    } finally {
      setBusy(false)
    }
  }

  function handleDrop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault()
    void uploadFiles(event.dataTransfer.files)
  }

  function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    if (event.target.files) void uploadFiles(event.target.files)
  }

  return (
    <div className="knowledge-panel">
      <form className="kb-create" onSubmit={createKb}>
        <input
          value={kbName}
          onChange={(event) => setKbName(event.target.value)}
          placeholder="新建知识库分类"
          disabled={busy}
        />
        <button type="submit" disabled={busy || !kbName.trim()} aria-label="新建知识库">
          <Plus size={15} />
        </button>
      </form>

      <div className="kb-list">
        {knowledgeBases.map((kb) => (
          <button
            key={kb.id}
            className={`kb-item ${kb.id === selectedKbId ? 'active' : ''}`}
            onClick={() => onSelectKb(kb.id)}
            type="button"
          >
            <span>{kb.name}</span>
            <Trash2
              size={13}
              onClick={(event) => {
                event.stopPropagation()
                void removeKb(kb.id)
              }}
            />
          </button>
        ))}
        {!knowledgeBases.length && (
          <div className="empty-note">创建分类后上传年报、财报或表格资料。</div>
        )}
      </div>

      <div
        className={`upload-zone ${!selectedKb ? 'disabled' : ''}`}
        onClick={() => selectedKb && inputRef.current?.click()}
        onDragOver={(event) => event.preventDefault()}
        onDrop={handleDrop}
      >
        <input
          ref={inputRef}
          type="file"
          multiple
          accept=".pdf,.xlsx,.xlsm,.xls"
          hidden
          onChange={handleFileChange}
        />
        <UploadCloud size={20} />
        <strong>{selectedKb ? `上传到 ${selectedKb.name}` : '先选择知识库'}</strong>
        <span>PDF · XLSX · XLSM · XLS</span>
      </div>

      {error && <div className="panel-error">{error}</div>}
      {loading && <div className="empty-note">正在同步文档状态…</div>}

      <div className="file-list">
        {documents.map((document) => (
          <div key={document.id} className="file-item">
            <div className={`file-icon ${document.status}`}>
              <FileText size={14} />
            </div>
            <div className="file-meta">
              <div className="file-name">{document.filename}</div>
              <div className="file-sub">
                {formatSize(document.file_size)} · {statusText(document)}
              </div>
            </div>
            <button
              className="ghost-icon"
              type="button"
              onClick={() => void removeDocument(document.id)}
              aria-label="删除文档"
            >
              <Trash2 size={14} />
            </button>
          </div>
        ))}
      </div>
    </div>
  )
}

function statusText(document: DocumentRecord): string {
  if (document.status === 'success') return `${document.chunk_count ?? 0} chunks`
  if (document.status === 'failed') return document.error_message || '解析失败'
  if (document.status === 'processing') return '解析入库中'
  return '等待处理'
}

function formatSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`
}
