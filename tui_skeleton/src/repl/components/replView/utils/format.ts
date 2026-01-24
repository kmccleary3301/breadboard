import type { SessionFileInfo } from "../../../../api/types.js"
import { COLUMN_SEPARATOR, DASH_GLYPH, GLYPHS } from "../theme.js"

export const formatBytes = (bytes: number): string => {
  if (bytes < 1_000) return `${bytes} B`
  if (bytes < 1_000_000) return `${(bytes / 1_000).toFixed(1)} KB`
  if (bytes < 1_000_000_000) return `${(bytes / 1_000_000).toFixed(2)} MB`
  return `${(bytes / 1_000_000_000).toFixed(2)} GB`
}

export const formatCostUsd = (value: number): string => {
  if (value >= 1) return `$${value.toFixed(2)}`
  if (value >= 0.1) return `$${value.toFixed(3)}`
  return `$${value.toFixed(4)}`
}

export const formatLatency = (ms: number): string => {
  if (ms < 1000) return `${Math.round(ms)}ms`
  if (ms < 10_000) return `${(ms / 1000).toFixed(2)}s`
  return `${(ms / 1000).toFixed(1)}s`
}

export const formatDuration = (ms: number): string => {
  const totalSeconds = Math.max(0, Math.round(ms / 1000))
  const minutes = Math.floor(totalSeconds / 60)
  const seconds = totalSeconds % 60
  const hours = Math.floor(minutes / 60)
  if (hours > 0) {
    const remMinutes = minutes % 60
    return `${hours}h ${remMinutes}m`
  }
  if (minutes > 0) return `${minutes}m ${seconds}s`
  return `${seconds}s`
}

export const formatTokenCount = (count: number): string => {
  if (!Number.isFinite(count)) return "0"
  const rounded = Math.max(0, Math.round(count))
  if (rounded >= 10000) return `${Math.round(rounded / 1000)}k`
  if (rounded >= 1000) return `${(rounded / 1000).toFixed(1)}k`
  return `${rounded}`
}

export const formatCell = (value: string, width: number, align: "left" | "right" = "left"): string => {
  if (width <= 0) return ""
  let output = value
  if (output.length > width) {
    output = width === 1 ? GLYPHS.ellipsis : `${output.slice(0, width - 1)}${GLYPHS.ellipsis}`
  }
  if (align === "right") return output.padStart(width, " ")
  return output.padEnd(width, " ")
}

export const truncateLine = (value: string, width: number): string => {
  if (width <= 0) return ""
  if (value.length <= width) return value
  return width === 1 ? GLYPHS.ellipsis : `${value.slice(0, width - 1)}${GLYPHS.ellipsis}`
}

export const formatFileListLines = (files: SessionFileInfo[]): string[] => {
  if (files.length === 0) {
    return ["(empty)"]
  }
  const sorted = [...files].sort((a, b) => a.path.localeCompare(b.path))
  const typeWidth = Math.max(
    "Type".length,
    ...sorted.map((entry) => entry.type.length),
  )
  const pathWidth = Math.max(
    "Path".length,
    ...sorted.map((entry) => entry.path.length),
  )
  const sizeEntries = sorted.map((entry) => (entry.size != null ? formatBytes(entry.size) : DASH_GLYPH))
  const sizeWidth = Math.max(
    "Size".length,
    ...sizeEntries.map((entry) => entry.length),
  )
  const header = [
    formatCell("Type", typeWidth),
    formatCell("Path", pathWidth),
    formatCell("Size", sizeWidth, "right"),
  ].join(COLUMN_SEPARATOR)
  const underline = [
    "-".repeat(typeWidth),
    "-".repeat(pathWidth),
    "-".repeat(sizeWidth),
  ].join(COLUMN_SEPARATOR)
  const rows = sorted.map((entry, index) => [
    formatCell(entry.type, typeWidth),
    formatCell(entry.path, pathWidth),
    formatCell(sizeEntries[index], sizeWidth, "right"),
  ].join(COLUMN_SEPARATOR))
  return [header, underline, ...rows]
}
