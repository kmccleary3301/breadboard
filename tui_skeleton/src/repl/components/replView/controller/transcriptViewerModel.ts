import type { TranscriptItem, TranscriptToolItem } from "../../../transcriptModel.js"
import { projectTranscriptItem } from "../../../transcript/projection.js"
import { stripAnsiCodes } from "../utils/ansi.js"
import type { ToolDisplayPayload } from "../../../types.js"

export type TranscriptAnchorKey = "assistant" | "user" | "tool" | "diff" | "error" | "warning"

export interface TranscriptToolTarget {
  readonly line: number
  readonly itemId: string
  readonly title: string
  readonly summaryLines: ReadonlyArray<string>
  readonly detailLines: ReadonlyArray<string>
  readonly artifactPath: string | null
  readonly artifactPreviewLines: ReadonlyArray<string>
  readonly artifactPreviewNote: string | null
}

export interface TranscriptViewerModel {
  readonly lines: ReadonlyArray<string>
  readonly toolLines: ReadonlyArray<number>
  readonly toolTargets: ReadonlyArray<TranscriptToolTarget>
  readonly anchors: Readonly<Record<TranscriptAnchorKey, number | null>>
}

const EMPTY_ANCHORS: Record<TranscriptAnchorKey, number | null> = {
  assistant: null,
  user: null,
  tool: null,
  diff: null,
  error: null,
  warning: null,
}

const normalizeNewlines = (text: string) => text.replace(/\r\n?/g, "\n")
const isWarningText = (value: string): boolean => /\bwarn(?:ing)?\b/i.test(stripAnsiCodes(value))
const isErrorText = (value: string): boolean => /\berror\b/i.test(stripAnsiCodes(value))
const normalizeDisplayLines = (value: string | ReadonlyArray<string> | null | undefined): string[] => {
  if (Array.isArray(value)) {
    return value
      .map((line) => (typeof line === "string" ? line : ""))
      .filter((line) => line.length > 0)
  }
  if (typeof value !== "string") return []
  return normalizeNewlines(value)
    .split("\n")
    .map((line) => line.trimEnd())
}

const resolveToolTarget = (item: TranscriptToolItem, line: number): TranscriptToolTarget => {
  const display = (item.display ?? null) as ToolDisplayPayload | null
  const artifactRef = display?.detail_artifact ?? null
  const title =
    typeof display?.title === "string" && display.title.trim().length > 0
      ? display.title.trim()
      : normalizeNewlines(item.text).split("\n")[0]?.trim() || "Tool"
  const summaryLines = normalizeDisplayLines(display?.summary ?? null)
  const detailLines = normalizeDisplayLines(display?.detail ?? null)
  const previewLines = normalizeDisplayLines(artifactRef?.preview?.lines ?? null)
  const artifactPath =
    typeof artifactRef?.path === "string" && artifactRef.path.trim().length > 0 ? artifactRef.path.trim() : null
  const artifactPreviewNote =
    typeof artifactRef?.preview?.note === "string" && artifactRef.preview.note.trim().length > 0
      ? artifactRef.preview.note.trim()
      : null
  return {
    line,
    itemId: item.id,
    title,
    summaryLines,
    detailLines,
    artifactPath,
    artifactPreviewLines: previewLines,
    artifactPreviewNote,
  }
}

export const buildTranscriptViewerModel = (items: ReadonlyArray<TranscriptItem>): TranscriptViewerModel => {
  const lines: string[] = []
  const toolLines: number[] = []
  const toolTargets: TranscriptToolTarget[] = []
  const anchors: Record<TranscriptAnchorKey, number | null> = { ...EMPTY_ANCHORS }

  for (const item of items) {
    const projection = projectTranscriptItem(item)
    if (item.kind === "message") {
      const start = lines.length
      lines.push(...projection.viewerLines)
      if (item.speaker === "assistant") anchors.assistant = start
      if (item.speaker === "user") anchors.user = start
      if (item.speaker === "system") {
        if (isErrorText(item.text)) anchors.error = start
        if (isWarningText(item.text)) anchors.warning = start
      }
      lines.push("")
      continue
    }

    const start = lines.length
    lines.push(...projection.viewerLines)
    if (item.kind === "tool") {
      toolLines.push(start)
      toolTargets.push(resolveToolTarget(item, start))
      anchors.tool = start
      if (projection.role === "diff") anchors.diff = start
      if (item.toolKind === "error" || item.status === "error" || isErrorText(item.text)) anchors.error = start
      if (isWarningText(item.text)) anchors.warning = start
    } else {
      if (item.systemKind === "error" || item.status === "error" || isErrorText(item.text)) anchors.error = start
      if (item.systemKind === "warning" || isWarningText(item.text)) anchors.warning = start
    }
    lines.push("")
  }

  while (lines.length > 0 && lines[lines.length - 1] === "") {
    lines.pop()
  }

  return { lines, toolLines, toolTargets, anchors }
}
