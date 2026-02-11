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
  statusLinePosition: "above_input" | "below_input"
  statusLineAlign: "left" | "right"
  shortcutsOpen: boolean
  ctrlCPrimedAt: number | null
  escPrimedAt: number | null
  pendingResponse: boolean
  scrollbackMode: boolean
  liveSlots: any[]
  animationTick: number
  collapsibleEntries: any[]
  collapsibleMeta: Map<string, { index: number; total: number }>
  selectedCollapsibleEntryId: string | null
  compactMode: boolean
  transcriptWindow: { items: ReadonlyArray<any>; truncated: boolean; hiddenCount: number }
  renderTranscriptEntry: (entry: any, key?: string) => React.ReactNode
}

export const useReplViewRenderNodes = (context: RenderNodesContext) => {
  const {
    claudeChrome,
    keymap,
    contentWidth,
    hints,
    completionHint,
    statusLinePosition,
    statusLineAlign,
    shortcutsOpen,
    ctrlCPrimedAt,
    escPrimedAt,
    pendingResponse,
    scrollbackMode,
    liveSlots,
    animationTick,
    collapsibleEntries,
    collapsibleMeta,
    selectedCollapsibleEntryId,
    compactMode,
    transcriptWindow,
    renderTranscriptEntry,
  } = context

  const toolNodes = useMemo(() => [], [])

  const liveSlotNodes = useMemo(() => {
    if (scrollbackMode) return []
    return liveSlots.map((slot, idx) => <LiveSlot key={`slot-${slot.id}`} slot={slot} index={idx} tick={animationTick} />)
  }, [animationTick, liveSlots, scrollbackMode])

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

  const hintNodes = useMemo(() => {
    const filtered = claudeChrome ? hints.filter((hint) => !hint.startsWith("Session ")) : hints
    if (claudeChrome) {
      let statusText = completionHint ?? filtered.slice(-1)[0] ?? ""
      if (escPrimedAt && !pendingResponse) {
        statusText = "Press Esc again to clear input."
      } else if (ctrlCPrimedAt) {
        statusText = "Press Ctrl+C again to exit."
      }
      if (!statusText || statusLinePosition === "below_input") return []
      const line = formatCell(statusText, Math.max(1, contentWidth), statusLineAlign)
      return [
        <Text key="hint-claude-status" color={COLORS.textMuted}>
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
    statusLineAlign,
    statusLinePosition,
  ])

  const shortcutHintNodes = useMemo(() => {
    if (!claudeChrome) return []
    const statusText = completionHint ?? hints.filter((hint) => !hint.startsWith("Session ")).slice(-1)[0] ?? ""
    const belowStatusLine =
      statusLinePosition === "below_input" && statusText
        ? [
            <Text key="hint-claude-status-below" color={COLORS.textMuted}>
              {formatCell(statusText, Math.max(1, contentWidth), statusLineAlign)}
            </Text>,
          ]
        : []
    if (shortcutsOpen) {
      const lines = shortcutLines.map((line, index) => (
        <Text key={`hint-shortcut-${index}`} wrap="truncate-end">
          {line}
        </Text>
      ))
      return [...belowStatusLine, ...lines]
    }
    return [
      ...belowStatusLine,
      <Text key="hint-claude-shortcuts" color={COLORS.textMuted}>
        {"  ? for shortcuts"}
      </Text>,
    ]
  }, [claudeChrome, completionHint, contentWidth, hints, shortcutLines, shortcutsOpen, statusLineAlign, statusLinePosition])

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
    const hidden = transcriptWindow.hiddenCount
    const hiddenText = hidden > 0 ? ` (${hidden} hidden)` : ""
    return (
      <Text color="dim">
        Compact transcript mode active — showing last {transcriptWindow.items.length} entry{transcriptWindow.items.length === 1 ? "" : "s"}
        {hiddenText}. Use /view scroll auto to expand.
      </Text>
    )
  }, [compactMode, transcriptWindow.hiddenCount, transcriptWindow.items.length, scrollbackMode])

  const transcriptNodes = useMemo(() => {
    if (transcriptWindow.items.length === 0) {
      if (scrollbackMode) {
        return []
      }
      return [
        <Text key="transcript-empty" color="gray">
          {pendingResponse ? "Assistant is thinking…" : "No conversation yet. Type a prompt to get started."}
        </Text>,
      ]
    }
    const nodes: React.ReactNode[] = []
    if (!scrollbackMode && transcriptWindow.truncated) {
      nodes.push(
        <Text key="transcript-truncated" color="dim">
          … {transcriptWindow.hiddenCount} earlier {transcriptWindow.hiddenCount === 1 ? "entry" : "entries"} hidden …
        </Text>,
      )
    }
    for (const entry of transcriptWindow.items) {
      const rendered = renderTranscriptEntry(entry)
      if (rendered) nodes.push(rendered)
    }
    const spaced: React.ReactNode[] = []
    nodes.filter(Boolean).forEach((node, idx) => {
      if (idx === 0) spaced.push(<Text key="transcript-gap-start"> </Text>)
      spaced.push(node)
      if (idx < nodes.length - 1) spaced.push(<Text key={`transcript-gap-${idx}`}> </Text>)
    })
    return spaced
  }, [transcriptWindow, pendingResponse, renderTranscriptEntry, scrollbackMode])

  return {
    toolNodes,
    liveSlotNodes,
    renderPermissionNoteLine,
    metaNodes,
    shortcutLines,
    hintNodes,
    shortcutHintNodes,
    collapsedHintNode,
    virtualizationHintNode,
    transcriptNodes,
  }
}
