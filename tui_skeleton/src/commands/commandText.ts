export const renderLabeledLine = (label: string, value: string): string => `${label}: ${value}`

export const renderSectionBlock = (title: string, lines: readonly string[], emptyText?: string): string => {
  const body = lines.length > 0 ? lines.join("\n") : (emptyText ?? "")
  return [`=== ${title} ===`, body].filter((line) => line.length > 0).join("\n")
}

export const renderOptionalJsonBlock = (label: string, value: unknown): string | null => {
  if (value == null) return null
  return `${label}: ${JSON.stringify(value)}`
}
