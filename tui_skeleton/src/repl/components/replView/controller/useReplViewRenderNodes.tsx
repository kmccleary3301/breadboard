import React, { useCallback, useMemo } from "react"
import { Text } from "ink"
import { LiveSlot } from "../../LiveSlot.js"
import { SLASH_COMMAND_HINT } from "../../../slashCommands.js"
import { CHALK, COLORS } from "../theme.js"
import { formatCell } from "../utils/format.js"
import { stripAnsiCodes } from "../utils/ansi.js"

type RenderNodesContext = {
  claudeChrome: boolean
  keymap: string
  contentWidth: number
  hints: string[]
  completionHint?: string | null
  shortcutsOpen: boolean
  ctrlCPrimedAt: number | null
  escPrimedAt: number | null
  pendingResponse: boolean
  toolWindow: { items: ReadonlyArray<any>; truncated: boolean; hiddenCount: number }
  renderToolEntry: (entry: any) => React.ReactNode
  scrollbackMode: boolean
  liveSlots: any[]
  animationTick: number
  collapsibleEntries: any[]
  collapsibleMeta: Map<string, { index: number; total: number }>
  selectedCollapsibleEntryId: string | null
  compactMode: boolean
  conversationWindow: { items: ReadonlyArray<any>; truncated: boolean; hiddenCount: number }
  streamingConversationEntry: any | null
  renderConversationEntry: (entry: any, key?: string) => React.ReactNode
}

export const useReplViewRenderNodes = (context: RenderNodesContext) => {
  const {
    claudeChrome,
    keymap,
    contentWidth,
    hints,
    completionHint,
    shortcutsOpen,
    ctrlCPrimedAt,
    escPrimedAt,
    pendingResponse,
    toolWindow,
    renderToolEntry,
    scrollbackMode,
    liveSlots,
    animationTick,
    collapsibleEntries,
    collapsibleMeta,
    selectedCollapsibleEntryId,
    compactMode,
    conversationWindow,
    streamingConversationEntry,
    renderConversationEntry,
  } = context

  const toolNodes = useMemo(() => {
    if (toolWindow.items.length === 0) return []
    const nodes: React.ReactNode[] = []
    if (!scrollbackMode && toolWindow.truncated) {
      nodes.push(
        <Text key="tool-truncated" color="dim">
          … {toolWindow.hiddenCount} earlier tool event{toolWindow.hiddenCount === 1 ? "" : "s"} hidden …
        </Text>,
      )
    }
    for (const entry of toolWindow.items) {
      nodes.push(renderToolEntry(entry))
    }
    return nodes
  }, [renderToolEntry, scrollbackMode, toolWindow])

  const liveSlotNodes = useMemo(
    () =>
      liveSlots.map((slot, idx) => (
        <LiveSlot key={`slot-${slot.id}`} slot={slot} index={idx} tick={animationTick} />
      )),
    [animationTick, liveSlots],
  )

  const renderPermissionNoteLine = useCallback((value: string, cursorIndex: number) => {
    if (!value) {
      return `${CHALK.inverse(" ")}${CHALK.dim(" Tell me what to do differently…")}`
    }
    const safeCursor = Math.max(0, Math.min(cursorIndex, value.length))
    const before = value.slice(0, safeCursor)
    const currentChar = value[safeCursor] ?? " "
    const after = value.slice(safeCursor + 1)
    return `${before}${CHALK.inverse(currentChar === "" ? " " : currentChar)}${after}`
  }, [])

  const metaNodes = useMemo(() => {
    if (claudeChrome) return []
    const codexPreface = keymap === "codex" ? "Try edit <file> to..." : null
    const hintParts = [
      "! for bash",
      "/ for commands",
      "@ for files",
      "Tab to complete",
      "Esc interrupt",
      "Esc Esc clear input",
      "Ctrl+L clear screen",
      "Ctrl+K model",
      `Ctrl+O ${keymap === "claude" ? "transcript" : "detailed"}`,
      "Ctrl+B tasks",
      "/usage",
    ]
    if (keymap === "claude") {
      hintParts.push("Ctrl+T todos")
    } else {
      hintParts.push("Ctrl+T transcript")
    }
    hintParts.push("Ctrl+G skills")
    const nodes: Array<JSX.Element> = []
    if (codexPreface) {
      nodes.push(
        <Text key="meta-preface" color="dim">
          {codexPreface}
        </Text>,
      )
    }
    nodes.push(
      <Text key="meta-slash" color="dim">
        Slash commands: {SLASH_COMMAND_HINT}
      </Text>,
    )
    nodes.push(
      <Text key="meta-hints" color="dim">
        {hintParts.join(" • ")}
      </Text>,
    )
    return nodes
  }, [claudeChrome, keymap])

  const shortcutLines = useMemo(() => {
    if (claudeChrome) {
      const rows: Array<[string, string, string?]> = [
        ["! for bash mode", "double tap esc to clear input", "ctrl + _ to undo"],
        ["/ for commands", "shift + tab to auto-accept edits", "ctrl + z to suspend"],
        ["@ for file paths", "ctrl + o for transcript", "ctrl + v to paste images"],
        ["& for background", "ctrl + t to show todos", "alt + p to switch model"],
        ["", "shift + ⏎ for newline", "ctrl + s to stash prompt"],
        ["", "ctrl + g for skills", "ctrl + r for rewind"],
      ]
      const colWidth = Math.max(22, Math.floor((contentWidth - 4) / 3))
      return rows.map(([a, b, c]) => {
        const left = formatCell(a, colWidth, "left")
        const mid = formatCell(b, colWidth, "left")
        const right = formatCell(c ?? "", colWidth, "left")
        return `${left}  ${mid}  ${right}`.trimEnd()
      })
    }
    const rows: Array<[string, string]> = [
      ["Ctrl+C ×2", "Exit the REPL"],
      ["Ctrl+D", "Exit immediately"],
      ["Ctrl+Z", "Suspend (empty input)"],
      ["Ctrl+L", "Clear screen (keep transcript)"],
      ["Ctrl+A", "Start of line"],
      ["Ctrl+E", "End of line"],
      ["Home/End", "Start/end of line"],
      ["Ctrl+U", "Delete to start"],
      ["Ctrl+S", "Stash input to history"],
      ["Esc", "Stop streaming"],
      ["Esc Esc", "Clear input"],
      ["Shift+Enter", "Insert newline"],
      ["Alt+Enter", "Insert newline"],
      ["Ctrl+O", keymap === "claude" ? "Transcript viewer" : "Toggle detailed transcript"],
      ["Ctrl+P", "Command palette"],
      ["Ctrl+K", "Model picker"],
      ["Alt+P", "Model picker"],
      ["Ctrl+G", "Skills picker"],
      ["Ctrl+B", "Background tasks"],
      ["Ctrl+R", "Rewind (checkpoints)"],
      ["/usage", "Usage summary"],
      ["Tab", "Complete @ or / list"],
      ["/", "Slash commands"],
      ["@", "File picker"],
      ["n/p", "Transcript match nav"],
      ["s", "Save transcript (viewer)"],
      ["?", "Toggle shortcuts"],
    ]
    if (keymap === "claude") {
      rows.push(["Ctrl+T", "Todos panel"])
    } else {
      rows.push(["Ctrl+T", "Transcript viewer"])
    }
    const pad = 14
    return rows.map(([key, desc]) => `${CHALK.cyan(key.padEnd(pad))} ${desc}`)
  }, [claudeChrome, contentWidth, keymap])

  const shortcutLines = useMemo(() => {
    if (claudeChrome) {
      const rows: Array<[string, string, string?]> = [
        ["! for bash mode", "double tap esc to clear input", "ctrl + _ to undo"],
        ["/ for commands", "shift + tab to auto-accept edits", "ctrl + z to suspend"],
        ["@ for file paths", "ctrl + o for transcript", "ctrl + v to paste images"],
        ["& for background", "ctrl + t to show todos", "alt + p to switch model"],
        ["", "shift + ⏎ for newline", "ctrl + s to stash prompt"],
        ["", "ctrl + g for skills", "ctrl + r for rewind"],
      ]
      const colWidth = Math.max(22, Math.floor((contentWidth - 4) / 3))
      return rows.map(([a, b, c]) => {
        const left = formatCell(a, colWidth, "left")
        const mid = formatCell(b, colWidth, "left")
        const right = formatCell(c ?? "", colWidth, "left")
        return `${left}  ${mid}  ${right}`.trimEnd()
      })
    }
    const rows: Array<[string, string]> = [
      ["Ctrl+C ×2", "Exit the REPL"],
      ["Ctrl+D", "Exit immediately"],
      ["Ctrl+Z", "Suspend (empty input)"],
      ["Ctrl+L", "Clear screen (keep transcript)"],
      ["Ctrl+A", "Start of line"],
      ["Ctrl+E", "End of line"],
      ["Home/End", "Start/end of line"],
      ["Ctrl+U", "Delete to start"],
      ["Ctrl+S", "Stash input to history"],
      ["Esc", "Stop streaming"],
      ["Esc Esc", "Clear input"],
      ["Shift+Enter", "Insert newline"],
      ["Alt+Enter", "Insert newline"],
      ["Ctrl+O", keymap === "claude" ? "Transcript viewer" : "Toggle detailed transcript"],
      ["Ctrl+P", "Command palette"],
      ["Ctrl+K", "Model picker"],
      ["Alt+P", "Model picker"],
      ["Ctrl+G", "Skills picker"],
      ["Ctrl+B", "Background tasks"],
      ["Ctrl+R", "Rewind (checkpoints)"],
      ["/usage", "Usage summary"],
      ["Tab", "Complete @ or / list"],
      ["/", "Slash commands"],
      ["@", "File picker"],
      ["n/p", "Transcript match nav"],
      ["s", "Save transcript (viewer)"],
      ["?", "Toggle shortcuts"],
    ]
    if (keymap === "claude") {
      rows.push(["Ctrl+T", "Todos panel"])
    } else {
      rows.push(["Ctrl+T", "Transcript viewer"])
    }
    const pad = 14
    return rows.map(([key, desc]) => `${CHALK.cyan(key.padEnd(pad))} ${desc}`)
  }, [claudeChrome, contentWidth, keymap])

  const hintNodes = useMemo(() => {
    const filtered = claudeChrome ? hints.filter((hint) => !hint.startsWith("Session ")) : hints
    if (claudeChrome) {
      if (shortcutsOpen) {
        return shortcutLines.map((line, index) => (
          <Text key={`hint-shortcut-${index}`} wrap="truncate-end">
            {line}
          </Text>
        ))
      }
      let statusText = completionHint ?? filtered.slice(-1)[0] ?? ""
      if (escPrimedAt && !pendingResponse) {
        statusText = "Press Esc again to clear input."
      } else if (ctrlCPrimedAt) {
        statusText = "Press Ctrl+C again to exit."
      }
      const leftText = "  ? for shortcuts"
      const leftLen = stripAnsiCodes(leftText).length
      const gutter = 2
      const rightMax = Math.max(0, contentWidth - leftLen - gutter)
      const trimmedStatus = statusText.length > 0 && rightMax > 0
        ? formatCell(statusText, rightMax, "right").trimStart()
        : ""
      const rightText = trimmedStatus.length > 0 ? formatCell(trimmedStatus, rightMax, "right").trimStart() : ""
      const line = rightText.length > 0
        ? `${leftText}${" ".repeat(gutter)}${rightText}`
        : leftText
      return [
        <Text key="hint-claude-footer" color={COLORS.textMuted}>
          {line}
        </Text>,
      ]
    }
    const latest = filtered.slice(-4)
    const nodes = latest.map((hint, index) => (
      <Text key={`hint-${index}`} color="yellow">
        {CHALK.yellow("•")} {hint}
      </Text>
    ))
    if (escPrimedAt && !pendingResponse) {
      nodes.push(
        <Text key="hint-esc-clear" color="yellow">
          {CHALK.yellow("•")} Press Esc again to clear input.
        </Text>,
      )
    }
    if (ctrlCPrimedAt) {
      nodes.push(
        <Text key="hint-exit" color="yellow">
          {CHALK.yellow("•")} Press Ctrl+C again to exit.
        </Text>,
      )
    }
    return nodes
  }, [
    claudeChrome,
    completionHint,
    contentWidth,
    ctrlCPrimedAt,
    escPrimedAt,
    hints,
    pendingResponse,
    shortcutLines,
    shortcutsOpen,
  ])

  const collapsedHintNode = useMemo(() => {
    if (collapsibleEntries.length === 0) return null
    const meta = selectedCollapsibleEntryId ? collapsibleMeta.get(selectedCollapsibleEntryId) : undefined
    const selectionText = meta ? `block ${meta.index + 1}/${meta.total}` : "no block targeted"
    return (
      <Text color="dim">
        Collapsed transcript controls: use "[" and "]" to cycle ({selectionText}), press e to expand/collapse.
      </Text>
    )
  }, [collapsibleEntries.length, collapsibleMeta, selectedCollapsibleEntryId])

  const virtualizationHintNode = useMemo(() => {
    if (scrollbackMode) return null
    if (!compactMode) return null
    const hidden = conversationWindow.hiddenCount
    const hiddenText = hidden > 0 ? ` (${hidden} hidden)` : ""
    return (
      <Text color="dim">
        Compact transcript mode active — showing last {conversationWindow.items.length} message{conversationWindow.items.length === 1 ? "" : "s"}
        {hiddenText}. Use /view scroll auto to expand.
      </Text>
    )
  }, [compactMode, conversationWindow.hiddenCount, conversationWindow.items.length, scrollbackMode])

  const transcriptNodes = useMemo(() => {
    if (conversationWindow.items.length === 0) {
      if (scrollbackMode) {
        return streamingConversationEntry
          ? [renderConversationEntry(streamingConversationEntry, "streaming-only")]
          : []
      }
      return [
        <Text key="transcript-empty" color="gray">
          {pendingResponse ? "Assistant is thinking…" : "No conversation yet. Type a prompt to get started."}
        </Text>,
      ]
    }
    const nodes: React.ReactNode[] = []
    if (!scrollbackMode && conversationWindow.truncated) {
      nodes.push(
        <Text key="transcript-truncated" color="dim">
          … {conversationWindow.hiddenCount} earlier {conversationWindow.hiddenCount === 1 ? "message" : "messages"} hidden …
        </Text>,
      )
    }
    for (const entry of conversationWindow.items) {
      nodes.push(renderConversationEntry(entry))
    }
    return nodes
  }, [conversationWindow, pendingResponse, renderConversationEntry, scrollbackMode, streamingConversationEntry])

  return {
    toolNodes,
    liveSlotNodes,
    renderPermissionNoteLine,
    metaNodes,
    shortcutLines,
    hintNodes,
    collapsedHintNode,
    virtualizationHintNode,
    transcriptNodes,
  }
}
