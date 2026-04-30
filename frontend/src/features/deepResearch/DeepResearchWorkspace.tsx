import { Suspense, lazy, useMemo, useState } from 'react'
import { Activity, BarChart3, FileText, GitBranch, PanelRightClose, PanelRightOpen } from 'lucide-react'
import type {
  DeepResearchEvent,
  KnowledgeGraphPayload,
  ResearchChart,
  ResearchReportReference,
  ResearchReportSection,
} from '../../types'
import { KnowledgeGraphView } from './KnowledgeGraphView'
import { ReportChartsView } from './ReportChartsView'
import { ResearchEventPanel } from './ResearchEventPanel'
import { ResearchReportView } from './ResearchReportView'

interface DeepResearchWorkspaceProps {
  events: DeepResearchEvent[]
  graph: KnowledgeGraphPayload | null
  charts: ResearchChart[]
  report: string
  sections: ResearchReportSection[]
  references: ResearchReportReference[]
  streaming: boolean
  open: boolean
  onToggleOpen: () => void
}

type ResearchTab = 'timeline' | 'graph' | 'charts' | 'report'

const EChartsView = lazy(() =>
  import('./EChartsView').then((module) => ({
    default: module.EChartsView,
  })),
)

export function DeepResearchWorkspace({
  events,
  graph,
  charts,
  report,
  sections,
  references,
  streaming,
  open,
  onToggleOpen,
}: DeepResearchWorkspaceProps) {
  const [activeTab, setActiveTab] = useState<ResearchTab>('timeline')
  const interactiveCharts = charts.filter((chart) => chart.echarts_option)
  const reportCharts = charts.filter((chart) => chart.image_base64)
  const hasReport = Boolean(report.trim() || sections.length)
  const hasContent =
    events.length || graph?.nodes.length || interactiveCharts.length || reportCharts.length || hasReport

  const tabs = useMemo(
    () => [
      { id: 'timeline' as const, label: '进度', count: events.length, icon: Activity },
      { id: 'graph' as const, label: '图谱', count: graph?.nodes.length ?? 0, icon: GitBranch },
      {
        id: 'charts' as const,
        label: '图表',
        count: interactiveCharts.length + reportCharts.length,
        icon: BarChart3,
      },
      { id: 'report' as const, label: '报告', count: sections.length || (hasReport ? 1 : 0), icon: FileText },
    ],
    [events.length, graph?.nodes.length, hasReport, interactiveCharts.length, reportCharts.length, sections.length],
  )

  if (!hasContent) return null

  if (!open) {
    return (
      <aside className="research-workspace research-workspace-collapsed" aria-label="Deep Research 工作区已收起">
        <button className="research-panel-toggle" onClick={onToggleOpen} type="button">
          <PanelRightOpen size={16} />
          <span>Research</span>
          {streaming && <span className="agent-running-dot" />}
        </button>
      </aside>
    )
  }

  return (
    <aside className="research-workspace" aria-label="Deep Research 工作区">
      <header className="research-workspace-header">
        <div>
          <span className="workspace-eyebrow">
            {streaming ? 'Agents running' : 'Research complete'}
          </span>
          <h2>Deep Research</h2>
        </div>
        <button
          className="ghost-icon research-close"
          onClick={onToggleOpen}
          type="button"
          aria-label="收起 Deep Research 工作区"
        >
          <PanelRightClose size={16} />
        </button>
      </header>

      <nav className="research-tabs" aria-label="Deep Research 内容切换">
        {tabs.map((tab) => {
          const Icon = tab.icon
          return (
            <button
              className={activeTab === tab.id ? 'active' : ''}
              key={tab.id}
              onClick={() => setActiveTab(tab.id)}
              type="button"
            >
              <Icon size={14} />
              <span>{tab.label}</span>
              <small>{tab.count}</small>
            </button>
          )
        })}
      </nav>

      <div className={`research-workspace-body ${streaming ? 'is-streaming' : ''}`}>
        {activeTab === 'timeline' && <ResearchEventPanel events={events} />}
        {activeTab === 'graph' &&
          (graph ? <KnowledgeGraphView graph={graph} /> : <EmptyPanel label="暂无知识图谱" />)}
        {activeTab === 'charts' && (
          <>
            {!!interactiveCharts.length && (
              <Suspense fallback={<ChartSkeleton />}>
                <EChartsView charts={interactiveCharts} />
              </Suspense>
            )}
            {!!reportCharts.length && <ReportChartsView charts={reportCharts} />}
            {!interactiveCharts.length && !reportCharts.length && <EmptyPanel label="暂无图表" />}
          </>
        )}
        {activeTab === 'report' &&
          (hasReport ? (
            <ResearchReportView report={report} sections={sections} references={references} />
          ) : (
            <EmptyPanel label="报告生成中" />
          ))}
      </div>
    </aside>
  )
}

function EmptyPanel({ label }: { label: string }) {
  return <div className="research-empty-panel">{label}</div>
}

function ChartSkeleton() {
  return (
    <div className="chart-skeleton" aria-label="图表加载中">
      <span />
      <span />
      <span />
    </div>
  )
}
