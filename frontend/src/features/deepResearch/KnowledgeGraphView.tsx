import { useEffect, useMemo, useRef } from 'react'
import { GraphChart } from 'echarts/charts'
import {
  LegendComponent,
  TitleComponent,
  TooltipComponent,
} from 'echarts/components'
import { init, use } from 'echarts/core'
import type { ECharts, EChartsCoreOption } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import type { KnowledgeGraphEdge, KnowledgeGraphNode, KnowledgeGraphPayload } from '../../types'

interface KnowledgeGraphViewProps {
  graph: KnowledgeGraphPayload
}

use([GraphChart, LegendComponent, TitleComponent, TooltipComponent, CanvasRenderer])

export function KnowledgeGraphView({ graph }: KnowledgeGraphViewProps) {
  const normalized = useMemo(() => normalizeGraph(graph), [graph])
  const containerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const element = containerRef.current
    if (!element || !normalized.nodes.length) return undefined

    const instance: ECharts = init(element)
    instance.setOption(buildGraphOption(normalized), true)

    const resize = () => instance.resize()
    let observer: ResizeObserver | null = null
    if (typeof ResizeObserver !== 'undefined') {
      observer = new ResizeObserver(resize)
      observer.observe(element)
    } else {
      window.addEventListener('resize', resize)
    }

    return () => {
      if (observer) {
        observer.disconnect()
      } else {
        window.removeEventListener('resize', resize)
      }
      instance.dispose()
    }
  }, [normalized])

  if (!normalized.nodes.length) return null

  return (
    <div className="knowledge-map" aria-label="Deep Research 知识图谱">
      <div className="research-block-header">
        <span>Knowledge Map</span>
        <small>
          {normalized.nodes.length} nodes · {normalized.edges.length} links
        </small>
      </div>
      <div className="knowledge-map-canvas" ref={containerRef} />
      <div className="knowledge-node-strip" aria-label="知识图谱节点">
        {normalized.nodes.slice(0, 10).map((node) => (
          <span className={`kg-chip kg-${node.type ?? 'entity'}`} key={node.id}>
            {node.displayLabel}
          </span>
        ))}
      </div>
    </div>
  )
}

interface NormalizedNode extends KnowledgeGraphNode {
  displayLabel: string
  symbolSize: number
}

interface NormalizedGraph {
  nodes: NormalizedNode[]
  edges: KnowledgeGraphEdge[]
}

function normalizeGraph(graph: KnowledgeGraphPayload): NormalizedGraph {
  const seen = new Set<string>()
  const nodes = (Array.isArray(graph.nodes) ? graph.nodes : [])
    .filter((node) => node && typeof node.id === 'string')
    .filter((node) => {
      if (seen.has(node.id)) return false
      seen.add(node.id)
      return true
    })
    .slice(0, 28)
    .map((node) => {
      const label = node.display_label ?? node.label ?? node.name ?? node.id
      const importance = typeof node.importance === 'number' ? node.importance : 5
      const rawSize = typeof node.size === 'number' ? node.size : 20 + importance * 3
      return {
        ...node,
        displayLabel: truncate(label, 14),
        symbolSize: Math.max(18, Math.min(54, rawSize)),
      }
    })
  const nodeIds = new Set(nodes.map((node) => node.id))
  const seenEdges = new Set<string>()
  const edges = (Array.isArray(graph.edges) ? graph.edges : [])
    .filter(
      (edge) =>
        edge &&
        nodeIds.has(edge.source) &&
        nodeIds.has(edge.target) &&
        edge.source !== edge.target,
    )
    .filter((edge) => {
      const key = `${edge.source}|${edge.target}|${edge.type ?? edge.relation ?? ''}`
      if (seenEdges.has(key)) return false
      seenEdges.add(key)
      return true
    })
    .slice(0, 48)
  return { nodes, edges }
}

function buildGraphOption(graph: NormalizedGraph): EChartsCoreOption {
  const categories = Array.from(new Set(graph.nodes.map((node) => node.type ?? 'entity'))).map(
    (name) => ({ name }),
  )
  return {
    tooltip: {
      trigger: 'item',
      formatter: (params: unknown) => {
        const item = params as { data?: Record<string, unknown>; dataType?: string }
        const data = item.data ?? {}
        if (item.dataType === 'edge') {
          return String(data.description || data.name || '关系')
        }
        return [
          `<strong>${String(data.name ?? '')}</strong>`,
          data.type ? `类型：${String(data.type)}` : '',
          data.summary ? String(data.summary) : '',
        ]
          .filter(Boolean)
          .join('<br/>')
      },
    },
    series: [
      {
        type: 'graph',
        layout: 'force',
        roam: true,
        draggable: true,
        categories,
        animationDuration: 500,
        animationEasingUpdate: 'cubicOut',
        force: {
          repulsion: 180,
          edgeLength: [72, 150],
          gravity: 0.08,
          friction: 0.45,
          layoutAnimation: true,
        },
        label: {
          show: true,
          position: 'bottom',
          formatter: '{b}',
          color: '#2a1f12',
          fontSize: 11,
        },
        edgeLabel: {
          show: false,
        },
        lineStyle: {
          color: 'source',
          opacity: 0.32,
          width: 1.2,
          curveness: 0.08,
        },
        emphasis: {
          focus: 'adjacency',
          lineStyle: {
            opacity: 0.76,
            width: 2,
          },
          edgeLabel: {
            show: true,
            formatter: '{c}',
            fontSize: 10,
          },
        },
        data: graph.nodes.map((node) => ({
          id: node.id,
          name: node.displayLabel,
          value: node.importance ?? 5,
          symbolSize: node.symbolSize,
          category: node.type ?? 'entity',
          type: node.type,
          summary: node.summary,
          itemStyle: { color: colorForNode(node.type) },
        })),
        links: graph.edges.map((edge) => ({
          source: edge.source,
          target: edge.target,
          value: edge.type ?? edge.relation ?? '',
          name: edge.type ?? edge.relation ?? '',
          description: edge.description,
          lineStyle: {
            width: Math.max(1, Math.min(3, Number(edge.weight ?? 2) / 3)),
          },
        })),
      },
    ],
  }
}

function colorForNode(type?: string): string {
  if (type === 'topic') return '#8b5014'
  if (type === 'policy' || type === 'risk') return '#c04444'
  if (type === 'indicator' || type === 'opportunity') return '#3f7d56'
  if (type === 'company' || type === 'industry') return '#2f6f8f'
  return '#8f7659'
}

function truncate(value: string, maxLength: number): string {
  return value.length <= maxLength ? value : `${value.slice(0, maxLength)}…`
}
