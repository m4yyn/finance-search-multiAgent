import { render, screen } from '@testing-library/react'
import { ReportChartsView } from './ReportChartsView'
import type { ResearchChart } from '../../types'

test('ReportChartsView renders base64 report images', () => {
  const charts: ResearchChart[] = [
    {
      id: 'report-chart-1',
      title: '净息差趋势报告图',
      chart_type: 'generated',
      artifact_type: 'report_image',
      image_base64: 'iVBORw0KGgo=',
    },
  ]

  render(<ReportChartsView charts={charts} />)

  const image = screen.getByRole('img', { name: '净息差趋势报告图' })
  expect(screen.getByText('Report Charts')).toBeVisible()
  expect(image).toHaveAttribute('src', 'data:image/png;base64,iVBORw0KGgo=')
})
