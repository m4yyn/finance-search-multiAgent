import { render, screen } from '@testing-library/react'
import { ResearchReportView } from './ResearchReportView'

test('ResearchReportView renders markdown headings and safe links', () => {
  render(
    <ResearchReportView
      report={
        '## 执行摘要\n\n银行业报告引用[测试来源](https://example.com)。\n\n- 风险仍需跟踪'
      }
      sections={[]}
      references={[{ id: 1, title: '测试来源', url: 'https://example.com' }]}
    />,
  )

  expect(screen.getByText('Report Draft')).toBeVisible()
  expect(screen.getByText('执行摘要')).toBeVisible()
  expect(screen.getAllByRole('link', { name: '测试来源' })[0]).toHaveAttribute(
    'href',
    'https://example.com',
  )
  expect(screen.getByText('风险仍需跟踪')).toBeVisible()
})
