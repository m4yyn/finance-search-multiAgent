import type { KnowledgeGraphEdge, KnowledgeGraphNode, KnowledgeGraphPayload } from '../../types'

interface KnowledgeGraphViewProps {
  graph: KnowledgeGraphPayload
}

interface PositionedNode extends KnowledgeGraphNode {
  x: number
  y: number
  radius: number
}

const WIDTH = 680
const HEIGHT = 300

export function KnowledgeGraphView({ graph }: KnowledgeGraphViewProps) {
  const nodes = Array.isArray(graph.nodes) ? graph.nodes.slice(0, 18) : []
  if (!nodes.length) return null

  const positionedNodes = positionNodes(nodes)
  const nodeById = new Map(positionedNodes.map((node) => [node.id, node]))
  const edges = normalizeEdges(graph.edges, nodeById)

  return (
    <div className="knowledge-map" aria-label="Deep Research 知识图谱">
      <div className="research-block-header">
        <span>Knowledge Map</span>
        <small>
          {nodes.length} nodes · {edges.length} links
        </small>
      </div>
      <svg viewBox={`0 0 ${WIDTH} ${HEIGHT}`} role="img">
        <title>Deep Research knowledge graph</title>
        {edges.map((edge, index) => {
          const source = nodeById.get(edge.source)
          const target = nodeById.get(edge.target)
          if (!source || !target) return null
          return (
            <g key={`${edge.source}-${edge.target}-${index}`}>
              <line
                className="kg-edge"
                x1={source.x}
                y1={source.y}
                x2={target.x}
                y2={target.y}
              />
              {edge.type && (
                <text
                  className="kg-edge-label"
                  x={(source.x + target.x) / 2}
                  y={(source.y + target.y) / 2 - 4}
                >
                  {truncate(edge.type, 18)}
                </text>
              )}
            </g>
          )
        })}
        {positionedNodes.map((node) => (
          <g key={node.id} className={`kg-node kg-${node.type ?? 'entity'}`}>
            <circle cx={node.x} cy={node.y} r={node.radius} />
            <text x={node.x} y={node.y + node.radius + 14}>
              {truncate(node.label ?? node.name ?? node.id, 12)}
            </text>
          </g>
        ))}
      </svg>
    </div>
  )
}

function positionNodes(nodes: KnowledgeGraphNode[]): PositionedNode[] {
  if (nodes.length === 1) {
    const onlyNode = nodes[0]
    return [
      {
        ...onlyNode,
        x: WIDTH / 2,
        y: HEIGHT / 2,
        radius: radiusForNode(onlyNode),
      },
    ]
  }

  const centerX = WIDTH / 2
  const centerY = HEIGHT / 2
  const radiusX = WIDTH * 0.36
  const radiusY = HEIGHT * 0.32
  return nodes.map((node, index) => {
    if (node.id === 'topic' || index === 0) {
      return {
        ...node,
        x: centerX,
        y: centerY,
        radius: radiusForNode(node),
      }
    }
    const angle = ((index - 1) / Math.max(1, nodes.length - 1)) * Math.PI * 2 - Math.PI / 2
    return {
      ...node,
      x: centerX + Math.cos(angle) * radiusX,
      y: centerY + Math.sin(angle) * radiusY,
      radius: radiusForNode(node),
    }
  })
}

function normalizeEdges(
  edges: KnowledgeGraphEdge[],
  nodeById: Map<string, PositionedNode>,
): KnowledgeGraphEdge[] {
  if (!Array.isArray(edges)) return []
  return edges.filter((edge) => nodeById.has(edge.source) && nodeById.has(edge.target))
}

function radiusForNode(node: KnowledgeGraphNode): number {
  const size = Number(node.size ?? 28)
  if (Number.isNaN(size)) return 9
  return Math.max(7, Math.min(18, size / 3.2))
}

function truncate(value: string, maxLength: number): string {
  return value.length <= maxLength ? value : `${value.slice(0, maxLength)}…`
}
