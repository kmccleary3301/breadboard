import { renderLabeledLine, renderSectionBlock } from "./commandText.js"

export interface ReportSection {
  readonly title: string
  readonly lines: readonly string[]
  readonly emptyText?: string
}

export const renderOptionalReportLine = (
  label: string,
  value: string | number | boolean | null | undefined,
): string | null => {
  if (value === null || value === undefined || value === "") return null
  return renderLabeledLine(label, String(value))
}

export const renderReportDocument = (
  title: string,
  options: {
    readonly lines?: readonly string[]
    readonly sections?: readonly ReportSection[]
  } = {},
): string => {
  const parts: string[] = [title]
  const lines = options.lines ?? []
  if (lines.length > 0) {
    parts.push(lines.join("\n"))
  }
  for (const section of options.sections ?? []) {
    const block = renderSectionBlock(section.title, section.lines, section.emptyText)
    if (block.length > 0) {
      parts.push(block)
    }
  }
  return parts.join("\n\n")
}
