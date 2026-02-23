import React, { useCallback, useMemo } from "react"
import { Box, Text } from "ink"
import { LiveSlot } from "../../LiveSlot.js"
import { SLASH_COMMAND_HINT } from "../../../slashCommands.js"
import { CHALK, COLORS } from "../theme.js"
import { formatCell } from "../utils/format.js"
import { stripAnsiCodes } from "../utils/ansi.js"

type RenderNodesContext = {
  claudeChrome: boolean
  footerV2Enabled?: boolean
  screenReaderMode?: boolean
  screenReaderProfile?: "concise" | "balanced" | "verbose"
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
  subagentStrip: { headline: string; detail?: string; tone: "info" | "success" | "error" } | null
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
    footerV2Enabled = false,
    screenReaderMode = false,
    screenReaderProfile = "balanced",
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
    subagentStrip,
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

  const subagentStripNode = useMemo(() => {
    if (scrollbackMode || !subagentStrip) return null
    const toneColor =
      subagentStrip.tone === "error"
        ? COLORS.error
        : subagentStrip.tone === "success"
          ? COLORS.success
          : COLORS.info
    return (
      <Box flexDirection="column">
        <Text color={toneColor}>{subagentStrip.headline}</Text>
        {subagentStrip.detail ? <Text color={COLORS.textSoft}>{subagentStrip.detail}</Text> : null}
      </Box>
    )
  }, [scrollbackMode, subagentStrip])

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
    if (screenReaderMode) {
      if (screenReaderProfile === "concise") {
        return [
          <Text key="meta-a11y-concise" color="dim">
            Accessibility mode active. Enter submit, Esc interrupt.
          </Text>,
        ]
      }
      if (screenReaderProfile === "verbose") {
        return [
          <Text key="meta-a11y-verbose-1" color="dim">
            Accessibility mode active. Core shortcuts: Enter submit, Shift+Enter newline, Esc interrupt, Ctrl+C ×2 exit.
          </Text>,
          <Text key="meta-a11y-verbose-2" color="dim">
            Extended shortcuts: Ctrl+O transcript, Ctrl+B tasks, Ctrl+T todos/transcript, Ctrl+K model, Ctrl+G skills.
          </Text>,
        ]
      }
      return [
        <Text key="meta-a11y" color="dim">
          Accessibility mode active. Core shortcuts: Enter submit, Shift+Enter newline, Esc interrupt, Ctrl+C ×2 exit.
        </Text>,
      ]
    }
    if (claudeChrome) return []
    if (footerV2Enabled) return []
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
      "Ctrl+B tasks/focus",
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
  }, [claudeChrome, footerV2Enabled, keymap, screenReaderMode, screenReaderProfile])

  const shortcutLines = useMemo(() => {
    if (screenReaderMode) {
      if (screenReaderProfile === "concise") {
        return [
          "Enter submit  ·  Shift+Enter newline  ·  Esc interrupt",
          "Ctrl+O transcript  ·  Ctrl+T todos/transcript",
        ]
      }
      if (screenReaderProfile === "verbose") {
        return [
          "Enter submit  ·  Shift+Enter newline  ·  Esc interrupt",
          "Ctrl+O transcript  ·  Ctrl+B tasks  ·  Ctrl+T todos/transcript",
          "Ctrl+K model  ·  Ctrl+G skills  ·  Ctrl+C ×2 exit",
          "/ for commands  ·  @ for files  ·  Tab complete",
          "s save transcript  ·  n/p match nav  ·  ? shortcuts",
        ]
      }
      return [
        "Enter submit  ·  Shift+Enter newline  ·  Esc interrupt",
        "Ctrl+O transcript  ·  Ctrl+B tasks  ·  Ctrl+T todos/transcript",
        "Ctrl+K model  ·  Ctrl+G skills  ·  Ctrl+C ×2 exit",
      ]
    }
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
      ["Ctrl+B", "Background tasks (F focus, [/] cycle lane)"],
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
  }, [claudeChrome, contentWidth, keymap, screenReaderMode, screenReaderProfile])

  const hintNodes = useMemo(() => {
    if (screenReaderMode) {
      const filtered = hints.filter((hint) => !hint.startsWith("Session "))
      const keepCount = screenReaderProfile === "concise" ? 1 : screenReaderProfile === "verbose" ? 4 : 2
      const latest = filtered.slice(-keepCount)
      const lines = latest.length > 0 ? latest : [pendingResponse ? "Assistant is thinking…" : "Ready."]
      return lines.map((line, index) => (
        <Text key={`hint-a11y-${index}`} color={COLORS.footerHint}>
          {line}
        </Text>
      ))
    }
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
        <Text key="hint-claude-status" color={COLORS.footerHint}>
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
    screenReaderMode,
    screenReaderProfile,
    statusLineAlign,
    statusLinePosition,
  ])

  const shortcutHintNodes = useMemo(() => {
    if (!claudeChrome) return []
    const statusText = completionHint ?? hints.filter((hint) => !hint.startsWith("Session ")).slice(-1)[0] ?? ""
    const belowStatusLine =
      statusLinePosition === "below_input" && statusText
        ? [
            <Text key="hint-claude-status-below" color={COLORS.footerHint}>
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
    if (statusLinePosition === "below_input" && statusText) {
      const left = "  ? for shortcuts"
      const width = Math.max(1, contentWidth)
      let combined = `${left} ${statusText}`
      if (left.length + statusText.length < width) {
        combined = `${left}${" ".repeat(Math.max(1, width - left.length - statusText.length))}${statusText}`
      } else if (combined.length > width) {
        combined = combined.slice(0, width)
      }
      return [
        <Text key="hint-claude-shortcuts-status" color={COLORS.footerHint}>
          {combined}
        </Text>,
      ]
    }
    return [
      ...belowStatusLine,
      <Text key="hint-claude-shortcuts" color={COLORS.footerHint}>
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
        <Text key="transcript-empty-primary" color={COLORS.textSoft}>
          {pendingResponse ? "Assistant is thinking…" : "No conversation yet."}
        </Text>,
        <Text key="transcript-empty-secondary" color={COLORS.textMuted}>
          {pendingResponse
            ? "Press Esc to interrupt the current response."
            : "Try / commands, @ files, or ask for a brief repo summary."}
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
    subagentStripNode,
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
