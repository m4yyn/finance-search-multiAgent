import { render, screen } from '@testing-library/react'
import * as echarts from 'echarts/core'
import { EChartsView } from './EChartsView'
import type { ResearchChart } from '../../types'

const setOptionMock = vi.fn()
const disposeMock = vi.fn()
const resizeMock = vi.fn()

vi.mock('echarts/core', () => ({
  init: vi.fn(() => ({
    setOption: setOptionMock,
    dispose: disposeMock,
    resize: resizeMock,
  })),
  use: vi.fn(),
}))

afterEach(() => {
  vi.clearAllMocks()
})

test('EChartsView initializes and disposes chart instances', () => {
  const charts: ResearchChart[] = [
    {
      id: 'chart-1',
      title: '资产规模',
      chart_type: 'bar',
      description: '银行业资产规模对比',
      echarts_option: {
        title: { text: '资产规模' },
        series: [{ type: 'bar', data: [1] }],
      },
    },
  ]

  const { unmount } = render(<EChartsView charts={charts} />)

  expect(screen.getByText('资产规模')).toBeVisible()
  expect(echarts.init).toHaveBeenCalledTimes(1)
  expect(setOptionMock).toHaveBeenCalledWith(
    expect.objectContaining({
      title: expect.objectContaining({ text: '资产规模' }),
      grid: expect.objectContaining({ containLabel: true }),
    }),
    true,
  )

  unmount()
  expect(disposeMock).toHaveBeenCalledTimes(1)
})

test('EChartsView shows fallback for incomplete chart options', () => {
  const charts: ResearchChart[] = [
    {
      id: 'chart-1',
      title: '单点趋势',
      chart_type: 'line',
      echarts_option: {
        title: { text: '单点趋势' },
        xAxis: { type: 'category', data: ['2024'] },
        series: [{ type: 'line', data: [7] }],
      },
    },
  ]

  render(<EChartsView charts={charts} />)

  expect(screen.getByText('单点趋势')).toBeVisible()
  expect(screen.getByText('当前图表数据点不足，已跳过不完整渲染。')).toBeVisible()
  expect(echarts.init).not.toHaveBeenCalled()
})
