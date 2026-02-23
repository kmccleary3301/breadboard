import React, { useMemo } from "react"
import { Box, Text } from "ink"
import type { StreamStats, TaskEntry, TodoItem } from "../../../types.js"
import { CHALK, COLORS, DOT_SEPARATOR, uiText } from "../theme.js"
import { formatDuration, formatTokenCount, truncateLine } from "../utils/format.js"
import { stripAnsiCodes } from "../utils/ansi.js"

type RuntimeStatusChip = {
  readonly id: string
  readonly label: string
  readonly tone: "info" | "success" | "warning" | "error"
}

type FooterV2Tone = "info" | "success" | "warning" | "error" | "muted"

export type FooterV2Model = {
  readonly phaseLine: string
  readonly summaryLine: string
  readonly tone: FooterV2Tone
}

type FooterV2Input = {
  readonly pendingResponse: boolean
  readonly disconnected: boolean
  readonly overlayActive: boolean
  readonly overlayLabel: string | null
  readonly keymap: string
  readonly status: string
  readonly runtimeStatusChips: ReadonlyArray<RuntimeStatusChip>
  readonly spinner: string
  readonly pendingStartedAtMs: number | null
  readonly lastDurationMs: number | null
  readonly nowMs: number
  readonly todos: ReadonlyArray<TodoItem>
  readonly tasks: ReadonlyArray<TaskEntry>
  readonly stats: StreamStats
  readonly width: number
}

const CANONICAL_CHIP_LABELS: Record<string, { label: string; tone: FooterV2Tone }> = {
  disconnected: { label: "disconnected", tone: "error" },
  error: { label: "error", tone: "error" },
  halted: { label: "halted", tone: "error" },
  permission: { label: "permission", tone: "warning" },
  reconnecting: { label: "reconnecting", tone: "warning" },
  tool: { label: "tool", tone: "warning" },
  done: { label: "done", tone: "success" },
  responding: { label: "responding", tone: "info" },
  thinking: { label: "thinking", tone: "info" },
  run: { label: "run", tone: "info" },
}

const normalizePhaseLabel = (raw: string): string => {
  const normalized = raw
    .trim()
    .toLowerCase()
    .replace(/[.\u2026]+$/g, "")
    .replace(/\s+/g, " ")
  return normalized
}

const normalizeTaskStatus = (raw: string | null | undefined): "running" | "failed" | "blocked" | "completed" | "pending" => {
  const status = String(raw ?? "").trim().toLowerCase()
  if (!status) return "pending"
  if (status.includes("fail") || status.includes("error") || status.includes("timeout")) return "failed"
  if (status.includes("block") || status.includes("wait")) return "blocked"
  if (status.includes("complete") || status.includes("done") || status.includes("success")) return "completed"
  if (status.includes("run") || status.includes("progress") || status.includes("start")) return "running"
  return "pending"
}

const isTodoIncomplete = (status: string | null | undefined): boolean => {
  const normalized = String(status ?? "").trim().toLowerCase()
  if (!normalized) return true
  return !["done", "completed", "complete", "canceled", "cancelled", "closed"].includes(normalized)
}

const phaseLabelFromState = (input: FooterV2Input): { label: string; tone: FooterV2Tone } => {
  if (input.disconnected) return { label: "disconnected", tone: "error" }
  const chip = input.runtimeStatusChips[0]
  if (chip) {
    const byId = CANONICAL_CHIP_LABELS[String(chip.id ?? "").trim().toLowerCase()]
    if (byId) return byId
    const normalized = normalizePhaseLabel(String(chip.label ?? ""))
    if (normalized) return { label: normalized, tone: chip.tone }
  }
  if (input.pendingResponse) return { label: "thinking", tone: "info" }
  const status = normalizePhaseLabel(input.status)
  if (!status || status === "ready" || status === "idle") return { label: "ready", tone: "muted" }
  if (status.includes("reconnect")) return { label: "reconnecting", tone: "warning" }
  if (status.includes("error") || status.includes("fail")) return { label: "error", tone: "error" }
  if (status.includes("halt") || status.includes("interrupt")) return { label: "halted", tone: "error" }
  if (status.includes("finish") || status.includes("complete") || status.includes("done")) return { label: "done", tone: "success" }
  if (status.includes("respond")) return { label: "responding", tone: "info" }
  if (status.includes("think")) return { label: "thinking", tone: "info" }
  if (status.includes("start") || status.includes("launch") || status.includes("init")) return { label: "starting", tone: "info" }
  return { label: status, tone: "info" }
}

const buildKeyHints = (input: FooterV2Input): string => {
  const todoHint = input.keymap === "claude" ? "ctrl+t todos" : "ctrl+t transcript"
  if (input.overlayActive) {
    const closeTarget = String(input.overlayLabel ?? "overlay").trim().toLowerCase()
    return uiText([`esc close ${closeTarget}`, "↑/↓ navigate", "enter select", "? shortcuts"].join(DOT_SEPARATOR))
  }
  if (input.pendingResponse) {
    return uiText(["esc interrupt", "ctrl+b tasks", todoHint, "? shortcuts"].join(DOT_SEPARATOR))
  }
  return uiText(["/ commands", "@ files", "ctrl+b tasks", todoHint, "ctrl+k model", "? shortcuts"].join(DOT_SEPARATOR))
}

const toneToColor = (tone: FooterV2Tone): string => {
  if (tone === "error") return COLORS.error
  if (tone === "warning") return COLORS.warning
  if (tone === "success") return COLORS.success
  if (tone === "info") return COLORS.info
  return COLORS.footerHint
}

const alignSummaryLine = (
  left: string,
  right: string,
  width: number,
  options?: { prioritizeLeft?: boolean },
): string => {
  const safeWidth = Math.max(24, Math.floor(width))
  const leftLen = stripAnsiCodes(left).length
  const rightLen = stripAnsiCodes(right).length
  if (leftLen + 2 + rightLen <= safeWidth) {
    const spacer = " ".repeat(Math.max(2, safeWidth - leftLen - rightLen))
    return `${left}${spacer}${right}`
  }
  if (options?.prioritizeLeft) {
    if (leftLen >= safeWidth) return truncateLine(left, safeWidth)
    const rightBudget = Math.max(0, safeWidth - leftLen - 1)
    if (rightBudget <= 0) return left
    return `${left} ${truncateLine(right, rightBudget)}`
  }
  if (rightLen >= safeWidth) {
    return truncateLine(right, safeWidth)
  }
  const leftBudget = Math.max(1, safeWidth - rightLen - 1)
  return `${truncateLine(left, leftBudget)} ${right}`
}

const compactModelLabel = (rawModel: string): string => {
  const normalized = String(rawModel ?? "").trim()
  if (!normalized) return "unknown"
  const tail = normalized.split("/").pop() ?? normalized
  return tail.length <= 18 ? tail : `${tail.slice(0, 15)}...`
}

const buildStatsParts = (input: FooterV2Input, todoPending: number, runningTasks: number, failedTasks: number): string[] => {
  const safeWidth = Math.max(24, Math.floor(input.width))
  const modelLabel = compactModelLabel(input.stats.model)
  const networkLabel = input.stats.remote ? "remote" : "local"
  const opsParts = [`r${runningTasks}`, `f${failedTasks}`, `e${formatTokenCount(input.stats.eventCount)}`]
  if (input.stats.toolCount > 0) opsParts.push(`t${formatTokenCount(input.stats.toolCount)}`)
  const statsParts: string[] = []

  // Keep a compact but always-present session identity.
  statsParts.push(`mdl ${modelLabel}`)
  if (safeWidth >= 120) statsParts.push(`net ${networkLabel}`)
  statsParts.push(`todo ${todoPending}/${input.todos.length}`)
  statsParts.push(`ops ${opsParts.join("/")}`)

  if (
    input.pendingResponse &&
    input.stats.usage?.totalTokens != null &&
    Number.isFinite(input.stats.usage.totalTokens) &&
    safeWidth >= 136
  ) {
    statsParts.push(`tok ${formatTokenCount(input.stats.usage.totalTokens)}`)
  }

  // Aggressively compact under narrow widths to reduce low-value noise.
  if (safeWidth < 96) {
    return [`mdl ${modelLabel}`, `todo ${todoPending}/${input.todos.length}`, `ops ${opsParts.join("/")}`]
  }
  return statsParts
}

export const buildFooterV2Model = (input: FooterV2Input): FooterV2Model => {
  const phase = phaseLabelFromState(input)
  const elapsedLabel = (() => {
    if (input.pendingResponse && Number.isFinite(input.pendingStartedAtMs as number)) {
      const startedAt = Number(input.pendingStartedAtMs)
      return `elapsed ${formatDuration(Math.max(0, input.nowMs - startedAt))}`
    }
    if (!input.pendingResponse && Number.isFinite(input.lastDurationMs as number)) {
      return `last ${formatDuration(Math.max(0, Number(input.lastDurationMs)))}`
    }
    return null
  })()
  const controlLabel = input.overlayActive
    ? `esc close ${String(input.overlayLabel ?? "overlay").trim().toLowerCase()}`
    : input.pendingResponse
      ? "esc interrupt"
      : "enter send"
  const phaseGlyph = input.pendingResponse ? input.spinner : CHALK.hex(COLORS.info)(uiText("•"))
  const phaseChip = CHALK.bold(`[${phase.label}]`)
  const phaseSegments: string[] = []
  if (elapsedLabel) phaseSegments.push(elapsedLabel)
  phaseSegments.push(controlLabel)
  const phaseLine = `${phaseGlyph} ${phaseChip} ${uiText(phaseSegments.join(DOT_SEPARATOR))}`

  let todoPending = 0
  for (const todo of input.todos) {
    if (isTodoIncomplete(todo.status)) todoPending += 1
  }
  let runningTasks = 0
  let failedTasks = 0
  for (const task of input.tasks) {
    const status = normalizeTaskStatus(task.status)
    if (status === "running") runningTasks += 1
    if (status === "failed") failedTasks += 1
  }
  const statsParts = buildStatsParts(input, todoPending, runningTasks, failedTasks)
  const hintLine = buildKeyHints(input)
  const focusBadge =
    input.overlayActive
      ? CHALK.hex(COLORS.info)(`FOCUS ${String(input.overlayLabel ?? "Overlay").trim() || "Overlay"}`)
      : null
  const hintWithFocus = CHALK.hex(COLORS.footerHint)(focusBadge ? `${focusBadge}${DOT_SEPARATOR}${hintLine}` : hintLine)
  const statsLine = CHALK.hex(COLORS.footerMeta)(statsParts.join(DOT_SEPARATOR))
  const summaryLine = alignSummaryLine(hintWithFocus, statsLine, input.width, {
    prioritizeLeft: input.overlayActive,
  })

  return {
    phaseLine,
    summaryLine,
    tone: phase.tone,
  }
}

export const FooterV2: React.FC<{ enabled: boolean; input: FooterV2Input }> = ({ enabled, input }) => {
  const model = useMemo(() => (enabled ? buildFooterV2Model(input) : null), [enabled, input])
  if (!enabled || !model) return null
  return (
    <Box marginTop={1} flexDirection="column">
      <Text color={toneToColor(model.tone)} wrap="truncate-end">
        {model.phaseLine}
      </Text>
      <Text wrap="truncate-end">
        {model.summaryLine}
      </Text>
    </Box>
  )
}
