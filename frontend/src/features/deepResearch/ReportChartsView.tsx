import type { ResearchChart } from '../../types'

interface ReportChartsViewProps {
  charts: ResearchChart[]
}

export function ReportChartsView({ charts }: ReportChartsViewProps) {
  const reportCharts = charts.filter((chart) => chart.image_base64)
  if (!reportCharts.length) return null

  return (
    <div className="report-charts" aria-label="Deep Research 报告图表">
      <div className="research-block-header">
        <span>Report Charts</span>
        <small>{reportCharts.length} images</small>
      </div>
      <div className="report-chart-list">
        {reportCharts.map((chart) => (
          <figure className="report-chart" key={chart.id}>
            <img
              alt={chart.title}
              src={`data:image/png;base64,${chart.image_base64}`}
            />
            <figcaption>
              <span>{chart.title}</span>
              <small>{chart.chart_type ?? chart.type ?? 'generated'}</small>
            </figcaption>
            {chart.description && <p>{chart.description}</p>}
          </figure>
        ))}
      </div>
    </div>
  )
}
