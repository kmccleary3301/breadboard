export const buildManagedViewportClearSequence = (rowsAboveCursor: number): string => {
  const moveUpRows = Math.max(0, Math.floor(rowsAboveCursor))
  const moveUp = moveUpRows > 0 ? `\u001b[${moveUpRows}A` : ""
  return `\r${moveUp}\r\u001b[J`
}

export const buildBottomAnchoredClearSequence = (rowsAboveBottom: number): string => {
  const moveUpRows = Math.max(0, Math.floor(rowsAboveBottom))
  const moveUp = moveUpRows > 0 ? `\u001b[${moveUpRows}A` : ""
  return `\u001b[999B\r${moveUp}\u001b[J`
}

export const buildViewportBottomAnchoredClearSequence = (rowsAboveBottom: number, terminalRows: number): string => {
  const bottomRow = Math.max(1, Math.floor(terminalRows))
  const moveUpRows = Math.max(0, Math.floor(rowsAboveBottom))
  const moveUp = moveUpRows > 0 ? `\u001b[${moveUpRows}A` : ""
  return `\u001b[${bottomRow};1H${moveUp}\u001b[J`
}

export const buildLineAboveActiveBandClearSequence = (rowsAboveCursor: number): string => {
  const moveUpRows = Math.max(1, Math.floor(rowsAboveCursor))
  return `\u001b7\r\u001b[${moveUpRows}A\u001b[2K\u001b8`
}

export const buildLineRangeAboveActiveBandClearSequence = (rowsAboveCursor: number, lineCount: number): string => {
  const startRows = Math.max(1, Math.floor(rowsAboveCursor))
  const count = Math.max(1, Math.floor(lineCount))
  return Array.from({ length: count }, (_, index) => buildLineAboveActiveBandClearSequence(startRows + index)).join("")
}
