import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { WorkspacePage } from './WorkspacePage'

const streamDeepResearchMock = vi.fn()

vi.mock('../auth/AuthProvider', () => ({
  useAuth: () => ({
    user: { username: 'researcher' },
    logout: vi.fn(),
  }),
}))

vi.mock('../../api/chat', () => ({
  createChatSession: vi.fn(async () => ({ session_id: 'session-1', title: '新会话' })),
  listChatSessions: vi.fn(async () => []),
  getChatMessages: vi.fn(async () => []),
  deleteChatSession: vi.fn(async () => undefined),
  streamChatMessage: vi.fn(async () => undefined),
}))

vi.mock('../../api/knowledge', () => ({
  listKnowledgeBases: vi.fn(async () => []),
  listDocuments: vi.fn(async () => []),
}))

vi.mock('../../api/deepResearch', () => ({
  streamDeepResearch: (...args: Parameters<typeof streamDeepResearchMock>) =>
    streamDeepResearchMock(...args),
}))

vi.mock('echarts/core', () => ({
  init: vi.fn(() => ({
    setOption: vi.fn(),
    dispose: vi.fn(),
    resize: vi.fn(),
  })),
  use: vi.fn(),
}))

beforeEach(() => {
  let id = 0
  Object.defineProperty(globalThis, 'crypto', {
    value: { randomUUID: vi.fn(() => `id-${++id}`) },
    configurable: true,
  })
  streamDeepResearchMock.mockImplementation(async (_payload, onEvent) => {
    onEvent({
      type: 'research_step',
      agent: 'Architect',
      content: { title: '研究规划', status: 'completed' },
    })
    onEvent({
      type: 'knowledge_graph',
      agent: 'DataAnalyst',
      content: {
        graph: {
          nodes: [{ id: 'topic', label: '银行业', size: 50 }],
          edges: [],
        },
      },
    })
    onEvent({
      type: 'charts',
      agent: 'DataAnalyst',
      content: {
        charts: [
          {
            id: 'chart-1',
            title: '资产规模',
            chart_type: 'bar',
            echarts_option: { title: { text: '资产规模' }, series: [] },
          },
        ],
      },
    })
    onEvent({
      type: 'chart',
      agent: 'Wizard',
      content: {
        chart: {
          id: 'report-chart-1',
          title: '净息差趋势报告图',
          chart_type: 'generated',
          artifact_type: 'report_image',
          image_base64: 'iVBORw0KGgo=',
          section_id: 'sec-1',
        },
      },
    })
    onEvent({
      type: 'section_content',
      agent: 'Writer',
      content: {
        section_id: 'sec-1',
        section_title: '市场概况',
        content: '银行业资产规模增长，但净息差仍需观察。',
        word_count: 20,
      },
    })
    onEvent({
      type: 'report_draft',
      agent: 'Writer',
      content: {
        content:
          '## 执行摘要\n\n银行业报告已生成，引用[测试来源](https://example.com)。\n\n## 风险与限制\n\n数据存在时点限制。',
        word_count: 62,
        references_count: 1,
      },
    })
    onEvent({
      type: 'review',
      agent: 'Critic',
      content: {
        quality_score: 8.6,
        verdict: 'pass',
        critical_count: 0,
        major_count: 0,
        minor_count: 0,
      },
    })
    onEvent({
      type: 'done',
      done: true,
      content: {
        summary: {
          facts_count: 1,
          charts_count: 2,
          report_charts_count: 1,
          report_word_count: 62,
          references_count: 1,
          quality_score: 8.6,
          unresolved_issues: 0,
          verdict: 'pass',
        },
        final_report:
          '## 执行摘要\n\n银行业报告已生成，引用[测试来源](https://example.com)。\n\n## 风险与限制\n\n数据存在时点限制。',
        references: [{ id: 1, title: '测试来源', source: '测试来源', url: 'https://example.com' }],
      },
    })
  })
})

afterEach(() => {
  vi.clearAllMocks()
})

test('WorkspacePage streams Deep Research events into side workspace and returns final report in chat', async () => {
  const user = userEvent.setup()
  const { container } = render(<WorkspacePage />)

  await user.click(screen.getByRole('button', { name: 'Deep Research' }))
  await user.type(
    screen.getByPlaceholderText('输入研究问题。Enter 发送，Shift+Enter 换行。'),
    '分析银行业',
  )
  await user.click(screen.getByRole('button', { name: '发送' }))

  await waitFor(() => {
    expect(streamDeepResearchMock).toHaveBeenCalledTimes(1)
  })
  expect(streamDeepResearchMock.mock.calls[0][0]).toMatchObject({
    session_id: 'session-1',
    content: '分析银行业',
    search_web: true,
    search_local: false,
  })
  expect(screen.getByText(/银行业报告已生成/)).toBeVisible()
  expect(screen.getAllByText(/质量分 8.6\/10/)[0]).toBeVisible()
  expect(screen.getByText('Agent Timeline')).toBeVisible()
  expect(screen.getByText('报告审核完成')).toBeVisible()
  expect(container.querySelector('.assistant-report-result')?.textContent).toContain('执行摘要')
  expect(container.querySelector('.assistant-report-result')?.textContent).not.toContain('Agent Timeline')

  await user.click(screen.getByRole('button', { name: /图谱/ }))
  expect(screen.getByText('银行业')).toBeVisible()

  await user.click(screen.getByRole('button', { name: /图表/ }))
  expect(await screen.findByText('资产规模')).toBeVisible()
  expect(screen.getByRole('img', { name: '净息差趋势报告图' })).toBeVisible()

  await user.click(screen.getByRole('button', { name: /报告/ }))
  expect(screen.getByText('Report Draft')).toBeVisible()
  expect(screen.getAllByText('执行摘要')[0]).toBeVisible()
  expect(screen.getAllByRole('link', { name: '测试来源' })[0]).toHaveAttribute(
    'href',
    'https://example.com',
  )
})

test('WorkspacePage restores Deep Research UI state from resume event', async () => {
  streamDeepResearchMock.mockImplementationOnce(async (_payload, onEvent) => {
    onEvent({
      type: 'research_resumed',
      agent: 'DeepResearch',
      content: {
        message: 'Deep Research checkpoint restored.',
        ui_state: {
          knowledge_graph: {
            nodes: [{ id: 'topic', label: '恢复银行业', size: 52 }],
            edges: [],
          },
          charts: [
            {
              id: 'resume-chart-1',
              title: '恢复图表',
              chart_type: 'bar',
              echarts_option: { title: { text: '恢复图表' }, series: [] },
            },
          ],
          streaming_report:
            "{'1 市场概况': {'内容': '恢复的报告内容，引用[恢复来源](https://example.com/resume)。'}}",
          references: [
            {
              id: 1,
              title: '恢复来源',
              source: '恢复来源',
              link: 'https://example.com/resume',
            },
          ],
        },
      },
    })
    onEvent({
      type: 'done',
      done: true,
      content: {
        summary: {
          facts_count: 2,
          charts_count: 1,
          report_word_count: 40,
          references_count: 1,
          quality_score: 8,
          unresolved_issues: 0,
        },
        final_report:
          '## 执行摘要\n\n恢复的报告内容，引用[恢复来源](https://example.com/resume)。',
        references: [
          { id: 1, title: '恢复来源', source: '恢复来源', url: 'https://example.com/resume' },
        ],
      },
    })
  })
  const user = userEvent.setup()
  render(<WorkspacePage />)

  await user.click(screen.getByRole('button', { name: 'Deep Research' }))
  await user.type(
    screen.getByPlaceholderText('输入研究问题。Enter 发送，Shift+Enter 换行。'),
    '恢复银行业研究',
  )
  await user.click(screen.getByRole('button', { name: '发送' }))

  expect(await screen.findByText('研究状态已恢复')).toBeVisible()
  await user.click(screen.getByRole('button', { name: /图谱/ }))
  expect(screen.getByText('恢复银行业')).toBeVisible()
  await user.click(screen.getByRole('button', { name: /图表/ }))
  expect(await screen.findByText('恢复图表')).toBeVisible()
  await user.click(screen.getByRole('button', { name: /报告/ }))
  expect(screen.getByText('Report Draft')).toBeVisible()
  expect(screen.getAllByRole('link', { name: '恢复来源' })[0]).toHaveAttribute(
    'href',
    'https://example.com/resume',
  )
})
