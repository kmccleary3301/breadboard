import { formatBytes } from "./format.js"

export const formatSizeDetail = (bytes: number | null | undefined): string | null => {
  if (bytes == null) return null
  return formatBytes(bytes)
}
