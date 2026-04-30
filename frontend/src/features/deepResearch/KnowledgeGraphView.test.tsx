import { render, screen } from '@testing-library/react'
import * as echarts from 'echarts/core'
import { KnowledgeGraphView } from './KnowledgeGraphView'
import type { KnowledgeGraphPayload } from '../../types'

const setOptionMock = vi.fn()
const disposeMock = vi.fn()

vi.mock('echarts/core', () => ({
  init: vi.fn(() => ({
    setOption: setOptionMock,
    dispose: disposeMock,
    resize: vi.fn(),
  })),
  use: vi.fn(),
}))

afterEach(() => {
  vi.clearAllMocks()
})

test('KnowledgeGraphView renders graph canvas and node strip', () => {
  const graph: KnowledgeGraphPayload = {
    nodes: [
      { id: 'topic', label: '银行业', type: 'topic', size: 50 },
      { id: 'nim', label: '净息差', type: 'indicator', size: 44 },
    ],
    edges: [{ source: 'topic', target: 'nim', type: 'constrains' }],
  }

  render(<KnowledgeGraphView graph={graph} />)

  expect(screen.getByLabelText('Deep Research 知识图谱')).toBeVisible()
  expect(screen.getByText('银行业')).toBeVisible()
  expect(screen.getByText('净息差')).toBeVisible()
  expect(echarts.init).toHaveBeenCalledTimes(1)
  expect(setOptionMock).toHaveBeenCalledWith(
    expect.objectContaining({
      series: [
        expect.objectContaining({
          type: 'graph',
          layout: 'force',
        }),
      ],
    }),
    true,
  )
})
