import { useEffect, useMemo, useRef } from 'react'
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
  const normalizedOption = useMemo(() => normalizeChartOption(chart), [chart])

  useEffect(() => {
    const element = containerRef.current
    if (!element || !normalizedOption) return undefined

    const instance: ECharts = init(element)
    instance.setOption(normalizedOption as EChartsCoreOption, true)

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
  }, [normalizedOption])

  return (
    <section className="research-chart">
      <div className="research-chart-title">
        <span>{chart.title}</span>
        <small>{chart.chart_type ?? chart.type ?? 'chart'}</small>
      </div>
      {chart.description && <p>{chart.description}</p>}
      {normalizedOption ? (
        <div className="echarts-canvas chart-loading" ref={containerRef} />
      ) : (
        <div className="chart-empty-state">
          当前图表数据点不足，已跳过不完整渲染。
        </div>
      )}
    </section>
  )
}

function normalizeChartOption(chart: ResearchChart): Record<string, unknown> | null {
  const option = chart.echarts_option
  if (!option) return null
  const next = cloneOption(option)
  const series = normalizeSeries(next.series)
  if (!series.length) return null
  const chartType = String(chart.chart_type ?? chart.type ?? series[0].type ?? '').toLowerCase()
  const categories = getCategoryAxisData(next)

  if (chartType === 'line' && !series.some((item) => dataLength(item.data) >= 2)) {
    return null
  }
  if (chartType === 'pie' && !series.some((item) => dataLength(item.data) >= 2)) {
    return null
  }
  if (chartType === 'bar' && categories.length) {
    if (
      series.length > 1 &&
      series.every((item) => Array.isArray(item.data) && item.data.length === 1) &&
      series.length === categories.length
    ) {
      next.series = [
        {
          type: 'bar',
          name: chart.title,
          data: series.map((item) => (Array.isArray(item.data) ? item.data[0] : null)),
        },
      ]
    } else if (
      !series.some((item) => Array.isArray(item.data) && item.data.length === categories.length)
    ) {
      return null
    } else {
      next.series = series.filter(
        (item) => Array.isArray(item.data) && item.data.length === categories.length,
      )
    }
  } else {
    next.series = series
  }

  next.title = normalizeTitle(next.title, chart.title)
  next.tooltip = next.tooltip ?? { trigger: chartType === 'pie' ? 'item' : 'axis' }
  if (chartType !== 'pie') {
    next.grid = {
      left: 36,
      right: 18,
      top: 62,
      bottom: 44,
      containLabel: true,
      ...(isRecord(next.grid) ? next.grid : {}),
    }
  }
  if (!next.legend && (normalizeSeries(next.series).length > 1 || chartType === 'pie')) {
    next.legend = { bottom: 0, type: 'scroll' }
  }
  if (normalizeSeries(next.series).length === 1 && chartType !== 'pie') {
    delete next.legend
  }
  return next
}

function cloneOption(option: Record<string, unknown>): Record<string, unknown> {
  try {
    return JSON.parse(JSON.stringify(option)) as Record<string, unknown>
  } catch {
    return { ...option }
  }
}

function normalizeSeries(series: unknown): Array<Record<string, unknown>> {
  if (Array.isArray(series)) {
    return series.filter(isRecord)
  }
  return isRecord(series) ? [series] : []
}

function getCategoryAxisData(option: Record<string, unknown>): unknown[] {
  for (const axisName of ['xAxis', 'yAxis']) {
    const axis = option[axisName]
    const axes = Array.isArray(axis) ? axis : [axis]
    for (const axisItem of axes) {
      if (isRecord(axisItem) && axisItem.type === 'category' && Array.isArray(axisItem.data)) {
        return axisItem.data
      }
    }
  }
  return []
}

function dataLength(data: unknown): number {
  return Array.isArray(data) ? data.length : 0
}

function normalizeTitle(title: unknown, fallback: string): Record<string, unknown> {
  if (isRecord(title)) {
    return { left: 'center', top: 4, textStyle: { fontSize: 15, fontWeight: 700 }, ...title }
  }
  return {
    text: fallback,
    left: 'center',
    top: 4,
    textStyle: { fontSize: 15, fontWeight: 700 },
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object'
}
