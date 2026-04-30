import { useEffect, useRef } from 'react'
import { BarChart, HeatmapChart, LineChart, PieChart, ScatterChart } from 'echarts/charts'
import {
  DatasetComponent,
  GridComponent,
  LegendComponent,
  TitleComponent,
  TooltipComponent,
  TransformComponent,
} from 'echarts/components'
import { init, use } from 'echarts/core'
import type { ECharts, EChartsCoreOption } from 'echarts/core'
import { CanvasRenderer } from 'echarts/renderers'
import type { ResearchChart } from '../../types'

interface EChartsViewProps {
  charts: ResearchChart[]
}

use([
  BarChart,
  DatasetComponent,
  GridComponent,
  HeatmapChart,
  LegendComponent,
  LineChart,
  PieChart,
  ScatterChart,
  TitleComponent,
  TooltipComponent,
  TransformComponent,
  CanvasRenderer,
])

export function EChartsView({ charts }: EChartsViewProps) {
  const interactiveCharts = charts.filter((chart) => chart.echarts_option)
  if (!interactiveCharts.length) return null
  return (
    <div className="research-charts" aria-label="Deep Research 图表">
      <div className="research-block-header">
        <span>Charts</span>
        <small>{interactiveCharts.length} configs</small>
      </div>
      <div className="research-chart-grid">
        {interactiveCharts.map((chart) => (
          <ChartPanel chart={chart} key={chart.id} />
        ))}
      </div>
    </div>
  )
}

function ChartPanel({ chart }: { chart: ResearchChart }) {
  const containerRef = useRef<HTMLDivElement | null>(null)

  useEffect(() => {
    const element = containerRef.current
    if (!element) return undefined

    const instance: ECharts = init(element)
    instance.setOption((chart.echarts_option ?? {}) as EChartsCoreOption, true)

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
  }, [chart.echarts_option])

  return (
    <section className="research-chart">
      <div className="research-chart-title">
        <span>{chart.title}</span>
        <small>{chart.chart_type ?? chart.type ?? 'chart'}</small>
      </div>
      {chart.description && <p>{chart.description}</p>}
      <div className="echarts-canvas" ref={containerRef} />
    </section>
  )
}
