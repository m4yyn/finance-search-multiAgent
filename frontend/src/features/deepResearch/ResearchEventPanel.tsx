import {
  Activity,
  AlertTriangle,
  CheckCircle2,
  CircleDashed,
  Code2,
  Database,
  FileText,
  Image,
  Search,
  Wrench,
} from 'lucide-react'
import type { DeepResearchEvent } from '../../types'

interface ResearchEventPanelProps {
  events: DeepResearchEvent[]
}

export function ResearchEventPanel({ events }: ResearchEventPanelProps) {
  const visibleEvents = events.slice(-12)
  if (!visibleEvents.length) return null

  return (
    <div className="research-events" aria-label="Deep Research 执行事件">
      <div className="research-events-header">
        <span>Agent Timeline</span>
        <small>{events.length} events</small>
      </div>
      <div className="research-event-list">
        {visibleEvents.map((event, index) => (
          <ResearchEventItem
            event={event}
            key={`${event.type}-${event.timestamp ?? index}-${index}`}
          />
        ))}
      </div>
    </div>
  )
}

function ResearchEventItem({ event }: { event: DeepResearchEvent }) {
  const content = asRecord(event.content)
  const status = getStatus(event, content)
  const title = getTitle(event, content)
  const detail = getDetail(event, content)
  const Icon = getIcon(event.type, status)

  return (
    <div className={`research-event event-${status}`}>
      <div className="research-event-icon">
        <Icon size={14} />
      </div>
      <div className="research-event-body">
        <div className="research-event-title">
          <span>{title}</span>
          {event.agent && <small>{event.agent}</small>}
        </div>
        {detail && <p>{detail}</p>}
        {event.type === 'search_results' && <SearchResultsMini content={content} />}
        {event.type === 'observation' && <ObservationMini content={content} />}
      </div>
    </div>
  )
}

function SearchResultsMini({ content }: { content: Record<string, unknown> }) {
  const results = Array.isArray(content.results) ? content.results.slice(0, 3) : []
  if (!results.length) return null
  return (
    <div className="research-mini-list">
      {results.map((item, index) => {
        const result = asRecord(item)
        return (
          <span key={`${String(result.title ?? 'result')}-${index}`}>
            {String(result.title ?? result.source ?? '检索结果')}
          </span>
        )
      })}
    </div>
  )
}

function ObservationMini({ content }: { content: Record<string, unknown> }) {
  const insights = Array.isArray(content.insights) ? content.insights.slice(0, 2) : []
  if (!insights.length) return null
  return (
    <div className="research-mini-list">
      {insights.map((item, index) => (
        <span key={`${String(item)}-${index}`}>{String(item)}</span>
      ))}
    </div>
  )
}

function getStatus(event: DeepResearchEvent, content: Record<string, unknown>): string {
  if (event.type === 'error') return 'failed'
  if (event.type === 'warning') return 'warning'
  const status = content.status ?? event.status
  if (typeof status === 'string') return status
  if (event.type === 'done') return 'completed'
  if (
    ['section_content', 'report_draft', 'revision_complete', 'review', 'critic_feedback'].includes(
      event.type,
    )
  ) {
    return 'completed'
  }
  return 'running'
}

function getTitle(event: DeepResearchEvent, content: Record<string, unknown>): string {
  if (typeof content.title === 'string') return content.title
  if (event.type === 'checkpoint_saved') return 'Checkpoint saved'
  if (event.type === 'research_start') return 'Deep Research 启动'
  if (event.type === 'research_resumed') return '研究状态已恢复'
  if (event.type === 'phase') return typeof event.phase === 'string' ? `阶段：${event.phase}` : '阶段切换'
  if (event.type === 'search_results') return '检索结果'
  if (event.type === 'knowledge_graph') return '知识图谱更新'
  if (event.type === 'charts') return '图表配置生成'
  if (event.type === 'chart') {
    const chart = asRecord(content.chart)
    return typeof chart.title === 'string' ? chart.title : '报告图表生成'
  }
  if (event.type === 'code') return 'Python 代码生成'
  if (event.type === 'code_fix') return '代码自动修复'
  if (event.type === 'code_result') {
    return content.success === false ? '代码执行失败' : '代码执行完成'
  }
  if (event.type === 'section_content') {
    return typeof content.section_title === 'string' ? content.section_title : '章节草稿生成'
  }
  if (event.type === 'report_draft') return '研究报告草稿'
  if (event.type === 'revision_complete') return '报告修订完成'
  if (event.type === 'review') return '报告审核完成'
  if (event.type === 'critic_feedback') {
    const severity = typeof content.severity === 'string' ? content.severity : 'issue'
    const issueType = typeof content.issue_type === 'string' ? content.issue_type : '审核问题'
    return `${severity}: ${issueType}`
  }
  if (event.type === 'warning') return '流程警告'
  if (event.type === 'done') return 'Deep Research 完成'
  if (event.type === 'error') return '执行失败'
  return event.type
}

function getDetail(event: DeepResearchEvent, content: Record<string, unknown>): string {
  if (event.type === 'review') {
    const score =
      typeof content.quality_score === 'number' ? `质量分 ${content.quality_score}/10` : '质量分待确认'
    const verdict = typeof content.verdict === 'string' ? `，结论 ${content.verdict}` : ''
    const critical = typeof content.critical_count === 'number' ? content.critical_count : 0
    const major = typeof content.major_count === 'number' ? content.major_count : 0
    const minor = typeof content.minor_count === 'number' ? content.minor_count : 0
    return `${score}${verdict}，问题 ${critical}/${major}/${minor}`
  }
  if (event.type === 'critic_feedback') {
    const description =
      typeof content.description === 'string' ? content.description : '审核发现待处理问题'
    const suggestion = typeof content.suggestion === 'string' ? ` 建议：${content.suggestion}` : ''
    return `${description}${suggestion}`
  }
  if (event.type === 'warning') {
    if (typeof content.message === 'string') return content.message
    if (typeof content.content === 'string') return content.content
  }
  if (event.type === 'research_start') {
    const query = typeof content.query === 'string' ? content.query : ''
    return query ? `开始研究：${query}` : '开始 Deep Research 流程'
  }
  if (event.type === 'research_resumed') {
    return typeof content.message === 'string' ? content.message : '已从 checkpoint 恢复研究状态'
  }
  if (event.type === 'section_content') {
    const count = typeof content.word_count === 'number' ? content.word_count : 0
    return count ? `章节草稿已生成，${count} 字符` : '章节草稿已生成'
  }
  if (event.type === 'report_draft') {
    const words = typeof content.word_count === 'number' ? `报告 ${content.word_count} 字符` : '报告已生成'
    const refs =
      typeof content.references_count === 'number' ? `，引用 ${content.references_count} 个来源` : ''
    return `${words}${refs}`
  }
  if (event.type === 'revision_complete') {
    const changes =
      typeof content.changes_count === 'number' ? `完成 ${content.changes_count} 处修订` : '修订已完成'
    return changes
  }
  if (typeof content.subtitle === 'string') return content.subtitle
  if (typeof content.content === 'string') return content.content
  if (typeof content.message === 'string') return content.message
  if (typeof event.error === 'string') return event.error
  if (event.type === 'chart') {
    const chart = asRecord(content.chart)
    const title = typeof chart.title === 'string' ? chart.title : content.title
    return typeof title === 'string' ? `已生成报告图表：${title}` : '已生成报告图表'
  }
  if (event.type === 'code' && typeof content.purpose === 'string') {
    return content.purpose
  }
  if (event.type === 'code_fix') {
    if (typeof content.fix_description === 'string') return content.fix_description
    if (typeof content.error_analysis === 'string') return content.error_analysis
  }
  if (event.type === 'code_result') {
    const retries = typeof content.retries === 'number' ? `，重试 ${content.retries} 次` : ''
    if (content.success === false) {
      const error = typeof content.error === 'string' ? `：${content.error}` : ''
      return `执行失败${retries}${error}`
    }
    const charts = typeof content.charts_count === 'number' ? `，生成 ${content.charts_count} 张图` : ''
    return `执行成功${retries}${charts}`
  }
  if (event.type === 'checkpoint_saved' && typeof event.phase === 'string') {
    return `阶段 ${event.phase} 已保存`
  }
  return ''
}

function getIcon(type: string, status: string) {
  if (type === 'warning' || type === 'critic_feedback') return AlertTriangle
  if (status === 'completed') return CheckCircle2
  if (status === 'failed') return AlertTriangle
  if (type === 'search_results' || type === 'search_progress') return Search
  if (type === 'chart') return Image
  if (type === 'code' || type === 'code_result') return Code2
  if (type === 'code_fix') return Wrench
  if (type === 'section_content' || type === 'report_draft' || type === 'revision_complete') {
    return FileText
  }
  if (type === 'knowledge_graph' || type === 'charts') return Database
  if (type === 'thought') return Activity
  return CircleDashed
}

function asRecord(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === 'object' ? (value as Record<string, unknown>) : {}
}
