import type { ThinkingArtifact, ThinkingPreviewState } from "../../../types.js"

export type ThinkingPreviewModel = {
  readonly headerLine: string
  readonly lines: ReadonlyArray<string>
  readonly lifecycle: "open" | "updating" | "closed"
  readonly phase: "starting" | "responding" | "done"
  readonly frameWidth: number | null
}

type BuildThinkingPreviewOptions = {
  readonly cols?: number | null
  readonly maxLines?: number
  readonly showWhenClosed?: boolean
}

const trimLine = (line: string, maxChars = 220): string => {
  const normalized = line.replace(/\s+/g, " ").trim()
  if (!normalized) return ""
  if (normalized.length <= maxChars) return normalized
  return `${normalized.slice(0, Math.max(1, maxChars - 1))}…`
}

const linesFromText = (text: string, maxLines: number): string[] =>
  text
    .replace(/\r\n?/g, "\n")
    .split("\n")
    .map((line) => trimLine(line))
    .filter((line) => line.length > 0)
    .slice(-Math.max(1, maxLines))

export const buildThinkingPreviewModel = (
  preview: ThinkingPreviewState | null | undefined,
  artifact: ThinkingArtifact | null | undefined,
  options: BuildThinkingPreviewOptions = {},
): ThinkingPreviewModel | null => {
  const maxLines = Math.max(1, options.maxLines ?? 5)
  const cols = options.cols ?? null
  const frameWidth =
    cols != null && Number.isFinite(cols) ? Math.max(10, Math.floor(cols) - 2) : null
  if (preview) {
    if (preview.lifecycle === "closed" && options.showWhenClosed !== true) {
      return null
    }
    if (!preview.lines || preview.lines.length === 0) return null
    const phase =
      preview.lifecycle === "closed"
        ? "done"
        : preview.lifecycle === "updating"
          ? "responding"
          : "starting"
    const headerLine = `[task tree] ${phase} · ${preview.eventCount} update${preview.eventCount === 1 ? "" : "s"}`
    return {
      headerLine,
      lines: preview.lines.slice(-maxLines),
      lifecycle: preview.lifecycle,
      phase,
      frameWidth,
    }
  }
  if (!artifact || !artifact.summary) return null
  if (artifact.finalizedAt && options.showWhenClosed !== true) {
    return null
  }
  const lines = linesFromText(artifact.summary, maxLines)
  if (lines.length === 0) return null
  const phase = artifact.finalizedAt ? "done" : "responding"
  return {
    headerLine: `[task tree] ${phase}`,
    lines,
    lifecycle: artifact.finalizedAt ? "closed" : "open",
    phase,
    frameWidth,
  }
}

export const getThinkingPreviewRowCount = (model: ThinkingPreviewModel | null): number => {
  if (!model || model.lines.length === 0) return 0
  return 1 + model.lines.length
}
