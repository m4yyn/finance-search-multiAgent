import type { ResearchReportReference, ResearchReportSection } from '../../types'
import type { ReactNode } from 'react'

interface ResearchReportViewProps {
  report: string
  sections: ResearchReportSection[]
  references: ResearchReportReference[]
}

export function ResearchReportView({
  report,
  sections,
  references,
}: ResearchReportViewProps) {
  const preview = report.trim()
  if (!preview && !sections.length) return null

  return (
    <div className="research-report" aria-label="Deep Research 报告预览">
      <div className="research-block-header">
        <span>Report Draft</span>
        <small>{preview.length || sections.reduce((sum, section) => sum + section.content.length, 0)} chars</small>
      </div>
      {preview ? (
        <MarkdownPreview content={preview} />
      ) : (
        <div className="report-section-list">
          {sections.map((section) => (
            <section className="report-section-preview" key={section.id}>
              <h3>{section.title}</h3>
              <MarkdownPreview content={section.content} />
            </section>
          ))}
        </div>
      )}
      {!!references.length && (
        <div className="report-reference-strip" aria-label="报告引用来源">
          {references.slice(0, 8).map((reference, index) => {
            const title = reference.title || reference.source || `来源 ${index + 1}`
            const key = `${String(reference.id ?? index)}-${reference.url ?? title}`
            return isHttpUrl(reference.url) ? (
              <a href={reference.url} key={key} rel="noreferrer" target="_blank">
                {title}
              </a>
            ) : (
              <span key={key}>{title}</span>
            )
          })}
        </div>
      )}
    </div>
  )
}

export function MarkdownPreview({ content }: { content: string }) {
  const blocks = content.split(/\n{2,}/).map((block) => block.trim()).filter(Boolean)
  return (
    <div className="report-markdown">
      {blocks.map((block, index) => {
        if (/^---+$/.test(block)) return <hr key={`${block}-${index}`} />
        if (block.startsWith('### ')) {
          return <h4 key={`${block}-${index}`}>{block.replace(/^###\s+/, '')}</h4>
        }
        if (block.startsWith('## ')) {
          return <h3 key={`${block}-${index}`}>{block.replace(/^##\s+/, '')}</h3>
        }
        const lines = block.split('\n').filter(Boolean)
        if (lines.every((line) => /^[-*]\s+/.test(line))) {
          return (
            <ul key={`${block}-${index}`}>
              {lines.map((line) => (
                <li key={line}>{renderInlineMarkdown(line.replace(/^[-*]\s+/, ''))}</li>
              ))}
            </ul>
          )
        }
        if (lines.every((line) => /^\d+\.\s+/.test(line))) {
          return (
            <ol key={`${block}-${index}`}>
              {lines.map((line) => (
                <li key={line}>{renderInlineMarkdown(line.replace(/^\d+\.\s+/, ''))}</li>
              ))}
            </ol>
          )
        }
        return <p key={`${block}-${index}`}>{renderInlineMarkdown(block)}</p>
      })}
    </div>
  )
}

function renderInlineMarkdown(text: string) {
  const parts: ReactNode[] = []
  const pattern = /\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g
  let lastIndex = 0
  let match: RegExpExecArray | null
  while ((match = pattern.exec(text)) !== null) {
    if (match.index > lastIndex) {
      parts.push(text.slice(lastIndex, match.index))
    }
    parts.push(
      <a href={match[2]} key={`${match[1]}-${match.index}`} rel="noreferrer" target="_blank">
        {match[1]}
      </a>,
    )
    lastIndex = match.index + match[0].length
  }
  if (lastIndex < text.length) {
    parts.push(text.slice(lastIndex))
  }
  return parts.length ? parts : text
}

function isHttpUrl(value?: string): value is string {
  return typeof value === 'string' && /^https?:\/\//.test(value)
}
