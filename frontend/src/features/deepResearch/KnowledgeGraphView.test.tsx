import { render, screen } from '@testing-library/react'
import { KnowledgeGraphView } from './KnowledgeGraphView'
import type { KnowledgeGraphPayload } from '../../types'

test('KnowledgeGraphView renders nodes and relation labels', () => {
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
  expect(screen.getByText('constrains')).toBeVisible()
})
