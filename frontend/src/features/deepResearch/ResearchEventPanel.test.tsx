import { render, screen } from '@testing-library/react'
import { ResearchEventPanel } from './ResearchEventPanel'
import type { DeepResearchEvent } from '../../types'

test('ResearchEventPanel shows steps, results, and observations', () => {
  const events: DeepResearchEvent[] = [
    {
      type: 'research_start',
      agent: 'DeepResearch',
      content: { query: '分析银行业' },
    },
    {
      type: 'research_resumed',
      agent: 'DeepResearch',
      content: { message: 'Deep Research checkpoint restored.' },
    },
    {
      type: 'research_step',
      agent: 'Architect',
      content: { title: '研究规划', status: 'completed' },
    },
    {
      type: 'search_results',
      agent: 'Scout',
      content: { results: [{ title: '银行业资产规模' }] },
    },
    {
      type: 'observation',
      agent: 'Scout',
      content: { title: '结构化数据提取', insights: ['净息差承压'] },
    },
    {
      type: 'code_result',
      agent: 'Wizard',
      content: { success: true, retries: 1, charts_count: 1 },
    },
    {
      type: 'chart',
      agent: 'Wizard',
      content: {
        chart: {
          id: 'report-chart-1',
          title: '净息差趋势报告图',
          artifact_type: 'report_image',
        },
      },
    },
    {
      type: 'section_content',
      agent: 'Writer',
      content: { section_title: '市场概况', word_count: 120 },
    },
    {
      type: 'report_draft',
      agent: 'Writer',
      content: { word_count: 800, references_count: 3 },
    },
    {
      type: 'review',
      agent: 'Critic',
      content: {
        quality_score: 8.5,
        verdict: 'pass',
        critical_count: 0,
        major_count: 0,
        minor_count: 1,
      },
    },
    {
      type: 'critic_feedback',
      agent: 'Critic',
      content: {
        severity: 'minor',
        issue_type: 'missing_source',
        description: '部分判断缺少来源。',
        suggestion: '补充监管数据引用。',
      },
    },
  ]

  render(<ResearchEventPanel events={events} />)

  expect(screen.getByText('Deep Research 启动')).toBeVisible()
  expect(screen.getByText('研究状态已恢复')).toBeVisible()
  expect(screen.getByText('研究规划')).toBeVisible()
  expect(screen.getByText('银行业资产规模')).toBeVisible()
  expect(screen.getByText('净息差承压')).toBeVisible()
  expect(screen.getByText('代码执行完成')).toBeVisible()
  expect(screen.getByText('净息差趋势报告图')).toBeVisible()
  expect(screen.getByText('市场概况')).toBeVisible()
  expect(screen.getByText('研究报告草稿')).toBeVisible()
  expect(screen.getByText('报告审核完成')).toBeVisible()
  expect(screen.getByText(/质量分 8.5\/10/)).toBeVisible()
  expect(screen.getByText('minor: missing_source')).toBeVisible()
  expect(screen.getByText(/补充监管数据引用/)).toBeVisible()
})
