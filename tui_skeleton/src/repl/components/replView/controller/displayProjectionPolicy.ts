import type { ConversationEntry, ToolLogEntry } from "../../../types.js"

const normalizeComparable = (value: string): string => value.replace(/\r\n?/g, "\n").trim()

const extractSingleLineStdoutEcho = (entry: ToolLogEntry): string | null => {
  if (entry.status === "error") return null
  const summary = typeof entry.display?.summary === "string" ? entry.display.summary : ""
  if (!summary.startsWith("stdout 1 line") || summary.includes("stderr")) return null
  const detail = entry.display?.detail
  if (!Array.isArray(detail) || detail.length !== 1) return null
  const line = typeof detail[0] === "string" ? normalizeComparable(detail[0]) : ""
  return line.length > 0 ? line : null
}

export const shouldSuppressAssistantToolEcho = (
  entry: ConversationEntry,
  toolEntries: ReadonlyArray<ToolLogEntry>,
): boolean => {
  if (entry.speaker !== "assistant" || entry.phase !== "final") return false
  const assistantText = normalizeComparable(entry.text)
  if (!assistantText || assistantText.includes("\n")) return false
  for (let i = toolEntries.length - 1; i >= 0; i -= 1) {
    const toolEntry = toolEntries[i]
    if (toolEntry.createdAt > entry.createdAt) continue
    if (entry.createdAt - toolEntry.createdAt > 15_000) break
    const echo = extractSingleLineStdoutEcho(toolEntry)
    if (echo && echo === assistantText) return true
    break
  }
  return false
}
