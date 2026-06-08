export interface RenderSimpleTableOptions {
  readonly separatorChar?: string
  readonly trimTrailingWhitespace?: boolean
}

export const renderSimpleTable = (
  headers: readonly string[],
  rows: ReadonlyArray<ReadonlyArray<string>>,
  options: RenderSimpleTableOptions = {},
): string => {
  const separatorChar = options.separatorChar ?? "-"
  const trimTrailingWhitespace = options.trimTrailingWhitespace ?? false
  const widths = headers.map((header, index) => Math.max(header.length, ...rows.map((row) => row[index]?.length ?? 0)))
  const formatRow = (cells: ReadonlyArray<string>) => {
    const rendered = cells.map((cell, index) => cell.padEnd(widths[index], " ")).join("  ")
    return trimTrailingWhitespace ? rendered.trimEnd() : rendered
  }
  return [
    formatRow(headers),
    formatRow(widths.map((width) => "".padEnd(width, separatorChar))),
    ...rows.map((row) => formatRow(row)),
  ].join("\n")
}
