import {
  type TranscriptCellRole,
  type TranscriptItem,
  type TranscriptRenderMode,
  resolveTranscriptCellRole,
} from "../transcriptModel.js"

export type TranscriptWidthPolicy = "rewrap" | "truncate" | "preserve" | "detail-only"

export interface TranscriptProjection {
  readonly id: string
  readonly role: TranscriptCellRole
  readonly modes: ReadonlyArray<TranscriptRenderMode>
  readonly richSummaryLines: ReadonlyArray<string>
  readonly viewerLines: ReadonlyArray<string>
  readonly rawLines: ReadonlyArray<string>
  readonly exportLines: ReadonlyArray<string>
  readonly widthPolicy: TranscriptWidthPolicy
  readonly inspectable: boolean
}

const normalizeLines = (value: string): string[] => {
  const lines = value.replace(/\r\n?/g, "\n").split("\n")
  return lines.length > 0 ? lines : [""]
}

const compact = (value: string, limit = 120): string => {
  const normalized = value.replace(/\s+/g, " ").trim()
  if (normalized.length <= limit) return normalized
  return `${normalized.slice(0, Math.max(0, limit - 1))}…`
}

const roleLabel = (role: TranscriptCellRole): string => role.replaceAll("-", " ")

const widthPolicyForRole = (role: TranscriptCellRole): TranscriptWidthPolicy => {
  switch (role) {
    case "tool-result":
    case "tool-error":
    case "diff":
    case "command-result":
      return "detail-only"
    case "tool-call":
    case "tool-summary":
    case "approval":
    case "status":
    case "landing":
      return "truncate"
    case "interrupted":
    case "system":
      return "preserve"
    case "user-request":
    case "assistant-message":
    default:
      return "rewrap"
  }
}

const inspectableForRole = (role: TranscriptCellRole): boolean => {
  switch (role) {
    case "tool-call":
    case "tool-result":
    case "tool-error":
    case "diff":
    case "approval":
    case "command-result":
      return true
    default:
      return false
  }
}

const richPrefixForRole = (role: TranscriptCellRole, item: TranscriptItem): string => {
  if (item.kind === "message") {
    if (item.speaker === "user") return "❯"
    if (item.speaker === "assistant") return "●"
    return "•"
  }
  switch (role) {
    case "tool-call":
      return "Tool"
    case "tool-result":
      return "Result"
    case "tool-error":
      return "Error"
    case "diff":
      return "Patch"
    case "approval":
      return "Approval"
    case "command-result":
      return "Command"
    case "status":
      return "Status"
    case "interrupted":
      return "Interrupted"
    case "landing":
      return "BreadBoard"
    default:
      return "•"
  }
}

export const projectTranscriptItem = (item: TranscriptItem): TranscriptProjection => {
  const role = resolveTranscriptCellRole(item)
  const bodyLines = normalizeLines(item.text)
  const richPrefix = richPrefixForRole(role, item)
  const compactFirstLine = compact(bodyLines[0] ?? "")
  const label = roleLabel(role)
  const rawHeader = `[${role}] ${item.id}`
  return {
    id: item.id,
    role,
    modes: item.renderModes?.length ? item.renderModes : ["rich", "raw", "viewer"],
    richSummaryLines: [`${richPrefix} ${compactFirstLine}`.trimEnd()],
    viewerLines: bodyLines.map((line, index) => `${index === 0 ? `${richPrefix} ` : "  "}${line}`),
    rawLines: [rawHeader, `source=${item.source}`, `kind=${item.kind}`, ...bodyLines],
    exportLines: [`## ${label}`, "", ...bodyLines],
    widthPolicy: widthPolicyForRole(role),
    inspectable: inspectableForRole(role),
  }
}

export const projectTranscriptItems = (items: ReadonlyArray<TranscriptItem>): TranscriptProjection[] =>
  items.map(projectTranscriptItem)

export const buildTranscriptExportLines = (items: ReadonlyArray<TranscriptItem>): string[] => {
  const lines: string[] = []
  for (const projection of projectTranscriptItems(items)) {
    if (lines.length > 0) lines.push("")
    lines.push(...projection.exportLines)
  }
  return lines
}
