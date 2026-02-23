import React, { useMemo } from "react"
import { Box, Text } from "ink"
import type { StreamStats, TaskEntry, TodoItem } from "../../../types.js"
import { CHALK, COLORS, DOT_SEPARATOR, uiText } from "../theme.js"
import { formatDuration, formatTokenCount, truncateLine } from "../utils/format.js"
import { stripAnsiCodes } from "../utils/ansi.js"
import type { PhaseLineState } from "../controller/runtimeStatusChip.js"

type FooterV2Tone = "info" | "success" | "warning" | "error" | "muted"

export type FooterV2Model = {
  readonly phaseLine: string
  readonly summaryLine: string
  readonly tone: FooterV2Tone
}

type ControlDeckItem = {
  readonly id: string
  readonly label: string
}

type ControlDeckState = {
  readonly hints: ReadonlyArray<ControlDeckItem>
  readonly stats: ReadonlyArray<ControlDeckItem>
  readonly focusLabel: string | null
}

type FooterV2Input = {
  readonly pendingResponse: boolean
  readonly overlayActive: boolean
  readonly overlayLabel: string | null
  readonly keymap: string
  readonly phaseLineState: PhaseLineState | null
  readonly spinner: string
  readonly pendingStartedAtMs: number | null
  readonly lastDurationMs: number | null
  readonly nowMs: number
  readonly todos: ReadonlyArray<TodoItem>
  readonly tasks: ReadonlyArray<TaskEntry>
  readonly stats: StreamStats
  readonly width: number
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

const resolveShortcutControlPrefix = (): "ctrl" | "cmd" => {
  const raw = String(process.env.BREADBOARD_TUI_SHORTCUT_PLATFORM ?? process.platform).trim().toLowerCase()
  if (["darwin", "mac", "macos", "osx"].includes(raw)) return "cmd"
  return "ctrl"
}

const normalizeShortcutLabel = (value: string): string =>
  value.replace(/\bctrl\+/g, `${resolveShortcutControlPrefix()}+`)

const buildKeyHintItems = (input: FooterV2Input): ReadonlyArray<ControlDeckItem> => {
  const compactShortcuts = Math.max(24, Math.floor(input.width)) < 120
  const todoHint = input.keymap === "claude" ? "ctrl+t todos" : "ctrl+t transcript"
  if (input.overlayActive) {
    const closeTarget = String(input.overlayLabel ?? "overlay").trim().toLowerCase()
    return [
      { id: "close", label: uiText(`esc close ${closeTarget}`) },
      { id: "navigate", label: "↑/↓ navigate" },
      { id: "select", label: "enter select" },
      { id: "shortcuts", label: "? shortcuts" },
    ]
  }
  if (input.pendingResponse) {
    return [
      { id: "interrupt", label: "esc interrupt" },
      { id: "tasks", label: normalizeShortcutLabel("ctrl+b tasks") },
      { id: "todo", label: normalizeShortcutLabel(todoHint) },
      { id: "shortcuts", label: "? shortcuts" },
    ]
  }
  if (compactShortcuts) {
    return [
      { id: "tasks", label: normalizeShortcutLabel("ctrl+b tasks") },
      { id: "todo", label: normalizeShortcutLabel(todoHint) },
      { id: "model", label: normalizeShortcutLabel("ctrl+k model") },
      { id: "shortcuts", label: "? shortcuts" },
    ]
  }
  return [
    { id: "commands", label: "/ commands" },
    { id: "files", label: "@ files" },
    { id: "tasks", label: normalizeShortcutLabel("ctrl+b tasks") },
    { id: "todo", label: normalizeShortcutLabel(todoHint) },
    { id: "model", label: normalizeShortcutLabel("ctrl+k model") },
    { id: "shortcuts", label: "? shortcuts" },
  ]
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

const buildStatsItems = (
  input: FooterV2Input,
  todoPending: number,
  runningTasks: number,
  failedTasks: number,
): ReadonlyArray<ControlDeckItem> => {
  const safeWidth = Math.max(24, Math.floor(input.width))
  const modelLabel = compactModelLabel(input.stats.model)
  const networkLabel = input.stats.remote ? "remote" : "local"
  const opsParts = [`r${runningTasks}`, `f${failedTasks}`, `e${formatTokenCount(input.stats.eventCount)}`]
  if (input.stats.toolCount > 0) opsParts.push(`t${formatTokenCount(input.stats.toolCount)}`)
  const statsItems: ControlDeckItem[] = []

  // Keep a compact but always-present session identity.
  statsItems.push({ id: "model", label: `mdl ${modelLabel}` })
  if (safeWidth >= 120) statsItems.push({ id: "network", label: `net ${networkLabel}` })
  statsItems.push({ id: "todo", label: `todo ${todoPending}/${input.todos.length}` })
  statsItems.push({ id: "ops", label: `ops ${opsParts.join("/")}` })

  if (
    input.pendingResponse &&
    input.stats.usage?.totalTokens != null &&
    Number.isFinite(input.stats.usage.totalTokens) &&
    safeWidth >= 136
  ) {
    statsItems.push({ id: "tokens", label: `tok ${formatTokenCount(input.stats.usage.totalTokens)}` })
  }

  // Aggressively compact under narrow widths to reduce low-value noise.
  if (safeWidth < 96) {
    return [
      { id: "model", label: `mdl ${modelLabel}` },
      { id: "todo", label: `todo ${todoPending}/${input.todos.length}` },
      { id: "ops", label: `ops ${opsParts.join("/")}` },
    ]
  }
  return statsItems
}

const buildControlDeckState = (
  input: FooterV2Input,
  todoPending: number,
  runningTasks: number,
  failedTasks: number,
): ControlDeckState => {
  const focusLabel =
    input.overlayActive ? String(input.overlayLabel ?? "Overlay").trim() || "Overlay" : null
  return {
    hints: buildKeyHintItems(input),
    stats: buildStatsItems(input, todoPending, runningTasks, failedTasks),
    focusLabel,
  }
}

export const buildFooterV2Model = (input: FooterV2Input): FooterV2Model => {
  const phase = input.phaseLineState ?? { id: "ready", label: "ready", tone: "muted" as const }
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
  const controlDeck = buildControlDeckState(input, todoPending, runningTasks, failedTasks)
  const hintLine = uiText(controlDeck.hints.map((item) => item.label).join(DOT_SEPARATOR))
  const focusBadge = controlDeck.focusLabel ? CHALK.hex(COLORS.info)(`FOCUS ${controlDeck.focusLabel}`) : null
  const hintWithFocus = CHALK.hex(COLORS.footerHint)(focusBadge ? `${focusBadge}${DOT_SEPARATOR}${hintLine}` : hintLine)
  const statsLine = CHALK.hex(COLORS.footerMeta)(controlDeck.stats.map((item) => item.label).join(DOT_SEPARATOR))
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
