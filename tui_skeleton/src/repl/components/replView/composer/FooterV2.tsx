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
  readonly mainFollowTail: boolean
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
  readonly sessionId?: string | null
  readonly transcriptViewerOpen?: boolean
  readonly transcriptSearchOpen?: boolean
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
  const safeWidth = Math.max(24, Math.floor(input.width))
  const compactShortcuts = safeWidth < 120
  const todoOrDetailHint = input.keymap === "claude" ? "ctrl+t todos" : "ctrl+o detailed"
  const transcriptHint = normalizeShortcutLabel(input.keymap === "claude" ? "ctrl+o transcript" : "ctrl+t transcript")
  const tasksHint = normalizeShortcutLabel("ctrl+b tasks")
  const modelHint = normalizeShortcutLabel("ctrl+k model")
  const recentSessionsHint = input.sessionId || input.stats.lastTurn != null ? "resume /sessions" : "/sessions recent"
  const attachHint = "@ attach"
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
      { id: "follow", label: input.mainFollowTail ? "/follow pause" : "/follow resume" },
      { id: "transcript", label: transcriptHint },
      { id: "tasks", label: tasksHint },
      { id: input.keymap === "claude" ? "todo" : "detail", label: normalizeShortcutLabel(todoOrDetailHint) },
      { id: "shortcuts", label: "? shortcuts" },
    ]
  }
  if (compactShortcuts) {
    return [
      { id: "resume", label: recentSessionsHint },
      { id: "transcript", label: transcriptHint },
      { id: "attach", label: attachHint },
      { id: "shortcuts", label: "? shortcuts" },
    ]
  }
  return [
    { id: "resume", label: recentSessionsHint },
    { id: "attach", label: attachHint },
    { id: "transcript", label: transcriptHint },
    { id: "model", label: modelHint },
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

const padVisibleEnd = (value: string, width: number): string => {
  const safeWidth = Math.max(24, Math.floor(width))
  const truncated = stripAnsiCodes(value).length > safeWidth ? truncateLine(value, safeWidth) : value
  const visibleLength = stripAnsiCodes(truncated).length
  return visibleLength >= safeWidth ? truncated : `${truncated}${" ".repeat(safeWidth - visibleLength)}`
}

const clearLineForScrollback = (value: string): string => `${value}\u001b[K`

const compactModelLabel = (rawModel: string): string => {
  const normalized = String(rawModel ?? "").trim()
  if (!normalized) return "unknown"
  const tail = normalized.split("/").pop() ?? normalized
  return tail.length <= 18 ? tail : `${tail.slice(0, 15)}...`
}

const compactSessionLabel = (rawSessionId: string | null | undefined): string | null => {
  const normalized = String(rawSessionId ?? "").trim()
  if (!normalized) return null
  const tail = normalized.length > 8 ? normalized.slice(-8) : normalized
  return tail
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
  const turnLabel = input.stats.lastTurn != null ? `turn ${input.stats.lastTurn}` : null
  const sessionLabel = compactSessionLabel(input.sessionId)
  const opsParts = [`r${runningTasks}`, `f${failedTasks}`, `e${formatTokenCount(input.stats.eventCount)}`]
  if (input.stats.toolCount > 0) opsParts.push(`t${formatTokenCount(input.stats.toolCount)}`)
  const statsItems: ControlDeckItem[] = []

  // Keep a compact but always-present session identity.
  statsItems.push({ id: "model", label: `mdl ${modelLabel}` })
  if (turnLabel) statsItems.push({ id: "turn", label: turnLabel })
  if (sessionLabel && safeWidth >= 136) statsItems.push({ id: "session", label: `sess ${sessionLabel}` })
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
      ...(turnLabel ? [{ id: "turn", label: turnLabel }] : []),
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
    : phase.id === "recovery" || phase.id === "disconnected"
      ? "use /retry or /resume"
    : input.pendingResponse
      ? "esc interrupt"
      : "enter send"
  const phaseGlyph = input.pendingResponse ? input.spinner : CHALK.hex(COLORS.info)(uiText("•"))
  const phaseChip = CHALK.bold(`[${phase.label}]`)
  const phaseSegments: string[] = []
  if (elapsedLabel) phaseSegments.push(elapsedLabel)
  if (input.pendingResponse && !input.overlayActive) phaseSegments.push(`follow ${input.mainFollowTail ? "live" : "paused"}`)
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

export const FooterV2: React.FC<{
  enabled: boolean
  input: FooterV2Input
  compactTopMargin?: boolean
  singleLine?: boolean
  padLines?: boolean
  clearLineBeforeRender?: boolean
}> = ({ enabled, input, compactTopMargin = false, singleLine = false, padLines = true, clearLineBeforeRender = false }) => {
  const model = useMemo(() => (enabled ? buildFooterV2Model(input) : null), [enabled, input])
  if (!enabled || !model) return null
  if (singleLine) {
    const line = `${model.phaseLine}  ${model.summaryLine}`
    const renderedLine = clearLineBeforeRender
      ? clearLineForScrollback(line)
      : padLines
        ? padVisibleEnd(line, input.width)
        : line
    return (
      <Box marginTop={compactTopMargin ? 0 : 1}>
        <Text color={toneToColor(model.tone)} wrap="truncate-end">
          {renderedLine}
        </Text>
      </Box>
    )
  }
  const phaseLine = clearLineBeforeRender
    ? clearLineForScrollback(model.phaseLine)
    : padLines
      ? padVisibleEnd(model.phaseLine, input.width)
      : model.phaseLine
  const summaryLine = clearLineBeforeRender
    ? clearLineForScrollback(model.summaryLine)
    : padLines
      ? padVisibleEnd(model.summaryLine, input.width)
      : model.summaryLine
  return (
    <Box marginTop={compactTopMargin ? 0 : 1} flexDirection="column">
      <Text color={toneToColor(model.tone)} wrap="truncate-end">
        {phaseLine}
      </Text>
      <Text wrap="truncate-end">
        {summaryLine}
      </Text>
    </Box>
  )
}
