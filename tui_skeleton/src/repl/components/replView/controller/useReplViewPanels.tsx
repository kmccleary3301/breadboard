import { useCallback, useEffect, useMemo } from "react"
import { buildSuggestions, SLASH_COMMANDS } from "../../../slashCommands.js"
import { computeModelColumns, CONTEXT_COLUMN_WIDTH, PRICE_COLUMN_WIDTH } from "../../../modelMenu/layout.js"
import { useSpinner } from "../../../hooks/useSpinner.js"
import { useAnimationClock } from "../../../hooks/useAnimationClock.js"
import { computeDiffPreview } from "../../../transcriptUtils.js"
import { buildTranscript } from "../../../transcriptBuilder.js"
import type { TranscriptItem } from "../../../transcriptModel.js"
import { renderCodeLines } from "../renderers/markdown/streamMdxAdapter.js"
import { stripAnsiCodes } from "../utils/ansi.js"
import { formatCell } from "../utils/format.js"
import { buildCTreeTreeRows } from "../../../ctrees/treeView.js"
import { scoreFuzzyMatch } from "../../../fileRanking.js"
import { splitUnifiedDiff } from "../utils/diff.js"
import { useComposerController } from "../features/composer/useComposerController.js"
import { computeInputLocked, computeOverlayActive } from "../keybindings/overlayState.js"
import { DELTA_GLYPH, GLYPHS, uiText } from "../theme.js"

type PanelsContext = Record<string, any>

export const useReplViewPanels = (context: PanelsContext) => {
  const {
    inspectRawOpen,
    inspectMenu,
    setInspectRawScroll,
    inspectRawViewportRows,
    columnWidth,
    rowCount,
    modelSearch,
    formatProviderCell,
    formatContextCell,
    formatPriceCell,
    buildModelRowText,
    pendingResponse,
    liveSlots,
    modelMenu,
    skillsMenu,
    paletteState,
    confirmState,
    shortcutsOpen,
    usageOpen,
    permissionRequest,
    rewindMenu,
    todosOpen,
    tasksOpen,
    ctreeOpen,
    transcriptViewerOpen,
    claudeChrome,
    setShortcutsOpen,
    inputLocked: inputLockedOverride,
    conversation,
    toolEvents,
    rawEvents,
    viewPrefs,
    verboseOutput,
    keymap,
    transcriptSearchQuery,
    transcriptSearchIndex,
    transcriptViewerScroll,
    transcriptViewerFollowTail,
    setTranscriptToolIndex,
    setTranscriptSearchIndex,
    setTranscriptSearchQuery,
    setTranscriptViewerFollowTail,
    setTranscriptViewerScroll,
    permissionFileIndex,
    setPermissionScroll,
    rewindIndex,
    todos,
    tasks,
    workGraph,
    subagentTaskboardEnabled,
    taskIndex,
    taskFocusLaneId,
    taskFocusViewOpen,
    taskSearchQuery,
    taskStatusFilter,
    setTaskNotice,
    setTaskTailLines,
    setTaskTailPath,
    ctreeTree,
    ctreeCollapsedNodes,
    ctreeIndex,
    ctreeIncludePreviews,
    onReadFile,
  } = context

  const inspectRawLines = useMemo(() => {
    if (!inspectRawOpen || inspectMenu.status !== "ready") return []
    try {
      const payload = {
        session: inspectMenu.session,
        skills: inspectMenu.skills,
        ctree: inspectMenu.ctree ?? null,
      }
      return JSON.stringify(payload, null, 2).split("\n")
    } catch {
      return ["<unable to serialize inspector payload>"]
    }
  }, [inspectMenu, inspectRawOpen])
  const inspectRawMaxScroll = useMemo(
    () => Math.max(0, inspectRawLines.length - inspectRawViewportRows),
    [inspectRawLines.length, inspectRawViewportRows],
  )
  useEffect(() => {
    setInspectRawScroll((prev: number) => Math.max(0, Math.min(prev, inspectRawMaxScroll)))
  }, [inspectRawMaxScroll, setInspectRawScroll])

  const PANEL_WIDTH = useMemo(() => Math.min(96, Math.max(60, Math.floor(columnWidth * 0.8))), [columnWidth])
  const modelColumnLayout = useMemo(() => computeModelColumns(columnWidth), [columnWidth])
  const modelMenuCompact = useMemo(() => rowCount <= 20 || columnWidth <= 70, [columnWidth, rowCount])
  const modelMenuHeaderText = useMemo(() => {
    const headerCells = [formatCell(uiText("Provider · Model"), modelColumnLayout.providerWidth)]
    if (modelColumnLayout.showContext) headerCells.push(formatCell("Context", CONTEXT_COLUMN_WIDTH, "right"))
    if (modelColumnLayout.showPriceIn) headerCells.push(formatCell("$/M In", PRICE_COLUMN_WIDTH, "right"))
    if (modelColumnLayout.showPriceOut) headerCells.push(formatCell("$/M Out", PRICE_COLUMN_WIDTH, "right"))
    return buildModelRowText(headerCells)
  }, [buildModelRowText, modelColumnLayout])

  const formatModelRowText = useCallback(
    (item: any) => {
      const cells = [formatProviderCell(item, modelColumnLayout.providerWidth, modelSearch)]
      if (modelColumnLayout.showContext) cells.push(formatContextCell(item.contextTokens ?? null, CONTEXT_COLUMN_WIDTH))
      if (modelColumnLayout.showPriceIn) cells.push(formatPriceCell(item.priceInPerM ?? null, PRICE_COLUMN_WIDTH))
      if (modelColumnLayout.showPriceOut) cells.push(formatPriceCell(item.priceOutPerM ?? null, PRICE_COLUMN_WIDTH))
      return buildModelRowText(cells)
    },
    [buildModelRowText, formatContextCell, formatPriceCell, formatProviderCell, modelColumnLayout, modelSearch],
  )

  const MAX_SUGGESTIONS = SLASH_COMMANDS.length
  const CONTENT_PADDING = 6
  const MAX_VISIBLE_MODELS = 8
  const MODEL_VISIBLE_ROWS = useMemo(() => {
    const base = Math.floor(rowCount * 0.45)
    const capped = Math.max(6, Math.min(14, base))
    return Math.max(4, Math.min(capped, rowCount - 10))
  }, [rowCount])
  const SKILLS_VISIBLE_ROWS = useMemo(() => {
    const base = Math.floor(rowCount * 0.5)
    const capped = Math.max(8, Math.min(18, base))
    return Math.max(6, Math.min(capped, rowCount - 8))
  }, [rowCount])

  const animationActive = pendingResponse || liveSlots.length > 0
  const animationTick = useAnimationClock(animationActive, 120)
  const spinner = useSpinner(pendingResponse, animationActive ? animationTick : undefined)

  const overlayFlags = useMemo(
    () => ({
      modelMenuOpen: modelMenu.status !== "hidden",
      skillsMenuOpen: skillsMenu.status !== "hidden",
      inspectMenuOpen: inspectMenu.status !== "hidden",
      paletteOpen: paletteState.status === "open",
      confirmOpen: confirmState.status === "prompt",
      shortcutsOpen,
      usageOpen,
      permissionOpen: Boolean(permissionRequest),
      rewindOpen: rewindMenu.status !== "hidden",
      todosOpen,
      tasksOpen,
      ctreeOpen,
      transcriptViewerOpen,
      claudeChrome,
    }),
    [
      claudeChrome,
      confirmState.status,
      ctreeOpen,
      inspectMenu.status,
      modelMenu.status,
      paletteState.status,
      permissionRequest,
      rewindMenu.status,
      shortcutsOpen,
      skillsMenu.status,
      tasksOpen,
      todosOpen,
      transcriptViewerOpen,
      usageOpen,
    ],
  )
  const inputLocked = inputLockedOverride ?? computeInputLocked(overlayFlags)
  const overlayActive = computeOverlayActive(overlayFlags)

  const {
    input,
    cursor,
    suggestIndex,
    setSuggestIndex,
    suppressSuggestions,
    setSuppressSuggestions,
    attachments,
    handleAttachment,
    removeLastAttachment,
    clearAttachments,
    inputTextVersion,
    inputValueRef,
    handleLineEdit,
    handleLineEditGuarded,
    pushHistoryEntry,
    recallHistory,
    moveCursorVertical,
  } = useComposerController({
    inputLocked,
    shortcutsOpen,
    setShortcutsOpen,
  })

  const mergedSuppress = suppressSuggestions
  const suggestions = useMemo(
    () => (mergedSuppress ? [] : buildSuggestions(input, MAX_SUGGESTIONS)),
    [input, mergedSuppress],
  )
  const activeSlashQuery = useMemo(() => {
    if (!input.startsWith("/")) return ""
    const [lookup] = input.slice(1).split(/\\s+/)
    return lookup ?? ""
  }, [input])
  const maxVisibleSuggestions = useMemo(() => {
    if (claudeChrome) {
      return Math.max(12, Math.min(20, Math.floor(rowCount * 0.6)))
    }
    return Math.max(8, Math.min(14, Math.floor(rowCount * 0.4)))
  }, [claudeChrome, rowCount])
  const inputMaxVisibleLines = useMemo(
    () => Math.max(3, Math.min(8, Math.floor(rowCount * 0.25))),
    [rowCount],
  )
  const wrapSuggestionText = useCallback((text: string, width: number, maxLines = Infinity) => {
    if (width <= 0) return [text]
    const words = text.trim().split(/\\s+/).filter(Boolean)
    if (words.length === 0) return [""]
    const lines: string[] = []
    let current = ""
    for (const word of words) {
      if (word.length > width) {
        if (current) {
          lines.push(current)
          current = ""
        }
        for (let i = 0; i < word.length; i += width) {
          lines.push(word.slice(i, i + width))
        }
        continue
      }
      if (!current) {
        current = word
        continue
      }
      if (current.length + 1 + word.length <= width) {
        current = `${current} ${word}`
      } else {
        lines.push(current)
        current = word
      }
    }
    if (current) lines.push(current)
    if (lines.length > maxLines) {
      const trimmed = lines.slice(0, Math.max(1, maxLines))
      const lastIndex = trimmed.length - 1
      let last = trimmed[lastIndex] ?? ""
      const ellipsis = GLYPHS.ellipsis
      if (width <= 1) {
        last = ellipsis
      } else if (last.length + ellipsis.length > width) {
        last = last.slice(0, Math.max(0, width - 1))
      }
      trimmed[lastIndex] = `${last}${ellipsis}`
      return trimmed
    }
    return lines
  }, [])
  const suggestionPrefix = useMemo(() => (claudeChrome ? "  " : ""), [claudeChrome])
  const suggestionCommandWidth = useMemo(() => {
    if (!claudeChrome) return null
    const maxLabel = suggestions.reduce((max, row) => {
      const label = uiText(`${row.command}${row.usage ? ` ${row.usage}` : ""}`)
      return Math.max(max, stripAnsiCodes(label).length)
    }, 0)
    const padded = maxLabel > 0 ? maxLabel + 2 : 18
    const maxAllowed = Math.max(18, Math.min(32, Math.floor((columnWidth - 6) * 0.45)))
    return Math.min(Math.max(16, padded), maxAllowed)
  }, [claudeChrome, columnWidth, suggestions])
  const suggestionLayout = useMemo(() => {
    const maxWidth = claudeChrome ? columnWidth - suggestionPrefix.length - 2 : Math.min(columnWidth - 4, 72)
    const totalWidth = Math.max(24, maxWidth)
    const baseCommandWidth = Math.max(14, Math.min(28, Math.floor(totalWidth * 0.35)))
    const commandWidth = claudeChrome && suggestionCommandWidth ? suggestionCommandWidth : baseCommandWidth
    const summaryWidth = Math.max(8, totalWidth - commandWidth - 2)
    return { totalWidth, commandWidth, summaryWidth }
  }, [claudeChrome, columnWidth, suggestionCommandWidth, suggestionPrefix.length])
  const buildSuggestionLines = useCallback(
    (row: any, multiline: boolean): Array<{ label: string; summary: string }> => {
      const label = `${row.command}${row.usage ? ` ${row.usage}` : ""}`
      const summaryText = row.shortcut ? uiText(`${row.summary} · ${row.shortcut}`) : row.summary
      if (!multiline) {
        return [{ label, summary: summaryText }]
      }
      const summaryMaxLines = 2
      const summaryLines = wrapSuggestionText(summaryText, suggestionLayout.summaryWidth, summaryMaxLines)
      if (summaryLines.length === 0) summaryLines.push("")
      return summaryLines.map((line, index) => ({
        label: index === 0 ? label : "",
        summary: line,
      }))
    },
    [suggestionLayout.summaryWidth, wrapSuggestionText],
  )
  const suggestionWindow = useMemo(() => {
    if (suggestions.length === 0) {
      return { items: [] as any[], hiddenAbove: 0, hiddenBelow: 0, start: 0, lineCount: 0 }
    }
    const maxRows = Math.max(1, Math.min(maxVisibleSuggestions, suggestions.length))
    if (suggestions.length <= maxRows) {
      const lineCount = suggestions.reduce((sum, row) => sum + buildSuggestionLines(row, claudeChrome).length, 0)
      return { items: suggestions, hiddenAbove: 0, hiddenBelow: 0, start: 0, lineCount }
    }
    const half = Math.floor(maxRows / 2)
    const start = Math.min(Math.max(0, suggestIndex - half), Math.max(0, suggestions.length - maxRows))
    const items = suggestions.slice(start, start + maxRows)
    const lineCount = items.reduce((sum, row) => sum + buildSuggestionLines(row, claudeChrome).length, 0)
    return {
      items,
      hiddenAbove: start,
      hiddenBelow: Math.max(0, suggestions.length - (start + items.length)),
      start,
      lineCount,
    }
  }, [buildSuggestionLines, claudeChrome, maxVisibleSuggestions, suggestIndex, suggestions])
  const paletteItems = useMemo(() => {
    if (paletteState.status === "hidden") return []
    const query = paletteState.query.trim().toLowerCase()
    if (!query) return SLASH_COMMANDS
    return SLASH_COMMANDS.filter((command) => command.name.toLowerCase().includes(query))
  }, [paletteState])

  const transcriptViewerLines = useMemo(() => {
    const lines: string[] = []
    const normalizeNewlines = (text: string) => text.replace(/\\r\\n?/g, "\\n")
    const transcript = buildTranscript(
      { conversation, toolEvents, rawEvents },
      { includeRawEvents: viewPrefs.rawStream === true, pendingToolsInTail: false },
    )
    const items: TranscriptItem[] = transcriptViewerOpen
      ? [...transcript.committed, ...transcript.tail]
      : transcript.committed

    const renderLines = (prefix: string, text: string) => {
      const entryLines = normalizeNewlines(text).split("\\n")
      entryLines.forEach((line, idx) => {
        lines.push(`${idx === 0 ? prefix : "  "}${line}`)
      })
    }

    for (const item of items) {
      if (item.kind === "message") {
        const glyph = item.speaker === "user" ? "❯" : item.speaker === "assistant" ? GLYPHS.assistantDot : GLYPHS.systemDot
        renderLines(`${glyph} `, item.text)
      } else {
        renderLines(`${GLYPHS.bullet} `, item.text)
      }
      lines.push("")
    }
    while (lines.length > 0 && lines[lines.length - 1] === "") {
      lines.pop()
    }
    return lines
  }, [conversation, rawEvents, toolEvents, transcriptViewerOpen, viewPrefs.rawStream])
  const transcriptToolLines = useMemo(() => {
    const indices: number[] = []
    for (let line = 0; line < transcriptViewerLines.length; line += 1) {
      const value = transcriptViewerLines[line] ?? ""
      if (value.startsWith(`${GLYPHS.bullet} `)) {
        indices.push(line)
      }
    }
    return indices
  }, [transcriptViewerLines])
  useEffect(() => {
    if (!transcriptViewerOpen) return
    setTranscriptToolIndex(0)
  }, [setTranscriptToolIndex, transcriptToolLines.length, transcriptViewerOpen])

  const transcriptViewerBodyRows = useMemo(
    () => Math.max(1, rowCount - (verboseOutput ? 5 : 4)),
    [rowCount, verboseOutput],
  )
  const transcriptViewerMaxScroll = useMemo(
    () => Math.max(0, transcriptViewerLines.length - transcriptViewerBodyRows),
    [transcriptViewerBodyRows, transcriptViewerLines.length],
  )
  const transcriptViewerEffectiveScroll = useMemo(() => {
    if (transcriptViewerFollowTail) return transcriptViewerMaxScroll
    return Math.max(0, Math.min(transcriptViewerScroll, transcriptViewerMaxScroll))
  }, [transcriptViewerFollowTail, transcriptViewerMaxScroll, transcriptViewerScroll])

  const transcriptSearchMatches = useMemo(() => {
    const query = transcriptSearchQuery.trim()
    if (!query) return []
    const matches: Array<{ line: number; start: number; end: number }> = []
    for (let line = 0; line < transcriptViewerLines.length; line += 1) {
      const candidate = stripAnsiCodes(transcriptViewerLines[line] ?? "")
      const score = scoreFuzzyMatch(candidate, query)
      if (score == null) continue
      matches.push({ line, start: 0, end: candidate.length })
    }
    return matches
  }, [transcriptSearchQuery, transcriptViewerLines])

  const transcriptSearchSafeIndex = useMemo(() => {
    if (transcriptSearchMatches.length === 0) return 0
    return Math.max(0, Math.min(transcriptSearchIndex, transcriptSearchMatches.length - 1))
  }, [transcriptSearchIndex, transcriptSearchMatches.length])

  const transcriptSearchActiveLine = useMemo(() => {
    if (transcriptSearchMatches.length === 0) return null
    return transcriptSearchMatches[transcriptSearchSafeIndex]?.line ?? null
  }, [transcriptSearchMatches, transcriptSearchSafeIndex])

  const transcriptSearchLineMatches = useMemo(() => {
    if (transcriptSearchMatches.length === 0) return []
    const set = new Set<number>()
    for (const match of transcriptSearchMatches) {
      set.add(match.line)
    }
    return Array.from(set)
  }, [transcriptSearchMatches])

  const jumpTranscriptToLine = useCallback(
    (line: number) => {
      const target = Math.max(0, Math.min(transcriptViewerLines.length - 1, line))
      const desired = Math.max(0, target - Math.floor(transcriptViewerBodyRows / 2))
      const clamped = Math.max(0, Math.min(transcriptViewerMaxScroll, desired))
      setTranscriptViewerFollowTail(false)
      setTranscriptViewerScroll(clamped)
    },
    [setTranscriptViewerFollowTail, setTranscriptViewerScroll, transcriptViewerBodyRows, transcriptViewerLines.length, transcriptViewerMaxScroll],
  )

  useEffect(() => {
    if (!transcriptViewerOpen) return
    if (!context.transcriptSearchOpen) return
    if (transcriptSearchMatches.length === 0) return
    setTranscriptSearchIndex(0)
    const line = transcriptSearchMatches[0]?.line
    if (typeof line === "number") {
      jumpTranscriptToLine(line)
    }
  }, [context.transcriptSearchOpen, jumpTranscriptToLine, transcriptSearchMatches, transcriptViewerOpen, context.transcriptSearchQuery])

  const permissionDiffSections = useMemo(() => {
    const diff = permissionRequest?.diffText
    if (!diff) return []
    return splitUnifiedDiff(diff)
  }, [permissionRequest?.diffText])

  const permissionDiffPreview = useMemo(() => {
    const diff = permissionRequest?.diffText
    if (!diff) return null
    const lines = diff.replace(/\\r\\n?/g, "\\n").split("\\n")
    return computeDiffPreview(lines)
  }, [permissionRequest?.diffText])

  const permissionSelectedFileIndex = useMemo(() => {
    if (permissionDiffSections.length === 0) return 0
    return Math.max(0, Math.min(permissionFileIndex, permissionDiffSections.length - 1))
  }, [permissionDiffSections.length, permissionFileIndex])

  const permissionSelectedSection = useMemo(() => {
    if (permissionDiffSections.length === 0) return null
    return permissionDiffSections[permissionSelectedFileIndex] ?? null
  }, [permissionDiffSections, permissionSelectedFileIndex])

  const permissionDiffLines = useMemo(() => {
    if (!permissionSelectedSection) return []
    return renderCodeLines(permissionSelectedSection.lines.join("\\n"), "diff")
  }, [permissionSelectedSection])

  const permissionViewportRows = useMemo(() => Math.max(8, Math.min(20, Math.floor(rowCount * 0.45))), [rowCount])

  const rewindCheckpoints = useMemo(() => {
    if (rewindMenu.status === "hidden") return []
    return rewindMenu.checkpoints
  }, [rewindMenu])

  const rewindSelectedIndex = useMemo(() => {
    if (rewindCheckpoints.length === 0) return 0
    return Math.max(0, Math.min(rewindIndex, rewindCheckpoints.length - 1))
  }, [rewindCheckpoints.length, rewindIndex])

  const rewindVisibleLimit = useMemo(() => Math.max(6, Math.min(10, Math.floor(rowCount * 0.28))), [rowCount])
  const rewindOffset = useMemo(() => {
    if (rewindCheckpoints.length <= rewindVisibleLimit) return 0
    const half = Math.floor(rewindVisibleLimit / 2)
    const target = Math.max(0, rewindSelectedIndex - half)
    return Math.min(target, Math.max(0, rewindCheckpoints.length - rewindVisibleLimit))
  }, [rewindCheckpoints.length, rewindSelectedIndex, rewindVisibleLimit])

  const rewindVisible = useMemo(
    () => rewindCheckpoints.slice(rewindOffset, rewindOffset + rewindVisibleLimit),
    [rewindCheckpoints, rewindOffset, rewindVisibleLimit],
  )
  const todoGroups = useMemo(() => {
    const groups: Record<string, any[]> = {
      in_progress: [],
      todo: [],
      blocked: [],
      done: [],
      canceled: [],
    }
    for (const item of todos) {
      const status = String(item.status || "todo").toLowerCase()
      if (status in groups) {
        groups[status].push(item)
      } else {
        groups.todo.push(item)
      }
    }
    return groups
  }, [todos])
  const todoRows = useMemo(() => {
    const rows: Array<{ kind: "header" | "item"; label: string; status?: string }> = []
    const pushGroup = (label: string, items: any[], status: string) => {
      if (items.length === 0) return
      rows.push({ kind: "header", label, status })
      for (const item of items) {
        rows.push({ kind: "item", label: item.title, status: item.status })
      }
    }
    pushGroup("In Progress", todoGroups.in_progress, "in_progress")
    pushGroup("Todo", todoGroups.todo, "todo")
    pushGroup("Blocked", todoGroups.blocked, "blocked")
    pushGroup("Done", todoGroups.done, "done")
    pushGroup("Canceled", todoGroups.canceled, "canceled")
    return rows
  }, [todoGroups])
  const todoViewportRows = useMemo(() => Math.max(8, Math.min(18, Math.floor(rowCount * 0.45))), [rowCount])
  const todoMaxScroll = Math.max(0, todoRows.length - todoViewportRows)

  const sortedTasks = useMemo(() => {
    const legacySorted = [...tasks].sort((a: any, b: any) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0))
    if (!subagentTaskboardEnabled) return legacySorted
    const byId = new Map<string, any>()
    for (const task of legacySorted) {
      if (task?.id) byId.set(task.id, task)
    }
    const merged: any[] = []
    const seen = new Set<string>()
    const itemOrder = Array.isArray(workGraph?.itemOrder) ? workGraph.itemOrder : []
    for (const workId of itemOrder) {
      const item = workGraph?.itemsById?.[workId]
      if (!item) continue
      const base = byId.get(workId) ?? {}
      seen.add(workId)
      merged.push({
        ...base,
        id: workId,
        description: base.description ?? item.title ?? null,
        subagentType: base.subagentType ?? item.laneLabel ?? null,
        status: base.status ?? item.status ?? null,
        outputExcerpt: base.outputExcerpt ?? item.lastSafeExcerpt ?? null,
        createdAt: base.createdAt ?? item.createdAt ?? item.updatedAt ?? 0,
        artifactPath: base.artifactPath ?? item.artifactPaths?.[0] ?? null,
        updatedAt: Math.max(base.updatedAt ?? 0, item.updatedAt ?? 0),
        counters: item.counters ?? null,
        steps: item.steps ?? [],
        mode: item.mode ?? null,
        laneId: item.laneId ?? null,
      })
    }
    for (const task of legacySorted) {
      if (!task?.id || seen.has(task.id)) continue
      merged.push(task)
    }
    return merged.sort((a: any, b: any) => (b.updatedAt ?? 0) - (a.updatedAt ?? 0))
  }, [subagentTaskboardEnabled, tasks, workGraph])
  const filteredTasks = useMemo(() => {
    const query = taskSearchQuery.trim().toLowerCase()
    const statusFilter = taskStatusFilter
    return sortedTasks.filter((task: any) => {
      if (statusFilter !== "all") {
        const normalized = (task.status ?? "").toLowerCase()
        if (!normalized || !normalized.includes(statusFilter)) {
          return false
        }
      }
      if (!query) return true
      const haystack = [
        task.id,
        task.description ?? "",
        task.subagentType ?? "",
        task.status ?? "",
        task.kind ?? "",
        task.sessionId ?? "",
      ]
        .join(" ")
        .toLowerCase()
      return haystack.includes(query)
    })
  }, [sortedTasks, taskSearchQuery, taskStatusFilter])
  const taskRows = useMemo(() => {
    return filteredTasks.map((task: any) => {
      const status = (task.status ?? "").toLowerCase()
      const labelParts = []
      const description = task.description || task.kind || "task"
      labelParts.push(description)
      if (task.subagentType) {
        labelParts.push(`(${task.subagentType})`)
      }
      const counters = task.counters
      const progressLabel =
        counters && Number.isFinite(counters.total) && counters.total > 0
          ? `${counters.completed ?? 0}/${counters.total}`
          : null
      return {
        id: task.id,
        status,
        label: labelParts.join(" "),
        progressLabel,
        task,
      }
    })
  }, [filteredTasks])
  const taskLaneOrder = useMemo(() => {
    const explicitOrder = Array.isArray(workGraph?.laneOrder) ? workGraph.laneOrder.filter((id: unknown) => typeof id === "string") : []
    if (explicitOrder.length > 0) return explicitOrder
    const fallback = new Set<string>()
    for (const row of taskRows) {
      const laneId = row.task?.laneId
      if (typeof laneId === "string" && laneId.length > 0) fallback.add(laneId)
    }
    return Array.from(fallback)
  }, [taskRows, workGraph?.laneOrder])
  const taskRowsForDisplay = useMemo(() => {
    if (!taskFocusViewOpen || !taskFocusLaneId) return taskRows
    return taskRows.filter((row: any) => row.task?.laneId === taskFocusLaneId)
  }, [taskFocusLaneId, taskFocusViewOpen, taskRows])
  const taskFocusLaneLabel = useMemo(() => {
    if (!taskFocusLaneId) return null
    const lane = workGraph?.lanesById?.[taskFocusLaneId]
    if (lane?.label) return lane.label
    return taskFocusLaneId
  }, [taskFocusLaneId, workGraph?.lanesById])
  const taskViewportRows = useMemo(() => Math.max(8, Math.min(16, Math.floor(rowCount * 0.45))), [rowCount])
  const taskMaxScroll = Math.max(0, taskRowsForDisplay.length - taskViewportRows)
  const selectedTaskIndex = useMemo(() => {
    if (taskRowsForDisplay.length === 0) return 0
    return Math.max(0, Math.min(taskIndex, taskRowsForDisplay.length - 1))
  }, [taskIndex, taskRowsForDisplay.length])
  const selectedTask = useMemo(() => taskRowsForDisplay[selectedTaskIndex]?.task ?? null, [selectedTaskIndex, taskRowsForDisplay])

  const { rows: ctreeRows, childrenByParent: ctreeChildrenByParent } = useMemo(
    () => buildCTreeTreeRows(ctreeTree ?? null, ctreeCollapsedNodes),
    [ctreeTree, ctreeCollapsedNodes],
  )
  const ctreeCollapsibleIds = useMemo(() => {
    const ids = new Set<string>()
    for (const [parentId, children] of ctreeChildrenByParent.entries()) {
      if (parentId && children.length > 0) ids.add(parentId)
    }
    return ids
  }, [ctreeChildrenByParent])

  const ctreeViewportRows = useMemo(() => Math.max(10, Math.min(18, Math.floor(rowCount * 0.55))), [rowCount])
  const ctreeMaxScroll = Math.max(0, ctreeRows.length - ctreeViewportRows)
  const selectedCTreeIndex = useMemo(() => {
    if (ctreeRows.length === 0) return 0
    return Math.max(0, Math.min(ctreeIndex, ctreeRows.length - 1))
  }, [ctreeIndex, ctreeRows.length])
  const selectedCTreeRow = useMemo(() => ctreeRows[selectedCTreeIndex] ?? null, [ctreeRows, selectedCTreeIndex])

  const formatCTreeNodeLabel = useCallback((node: any): string => {
    if (typeof node.label === "string" && node.label.trim()) return node.label.trim()
    if (node.kind === "root") return "C-Tree"
    if (node.kind === "turn") return typeof node.turn === "number" ? `Turn ${node.turn}` : "Turn ?"
    if (node.kind === "task_root") return "Tasks"
    if (node.kind === "task") return "Task"
    if (node.kind === "collapsed") return "Collapsed"
    return node.kind || "node"
  }, [])

  const formatCTreeNodePreview = useCallback(
    (node: any): string | null => {
      const meta = (node.meta ?? {}) as Record<string, unknown>
      if (ctreeIncludePreviews && typeof meta.content_preview === "string") {
        const trimmed = meta.content_preview.replace(/\\s+/g, " ").trim()
        return trimmed.length > 70 ? `${trimmed.slice(0, 67)}${GLYPHS.ellipsis}` : trimmed
      }
      const collapsedIds = Array.isArray(meta.collapsed_ids) ? meta.collapsed_ids.length : null
      if (typeof collapsedIds === "number") return `${collapsedIds} collapsed node${collapsedIds === 1 ? "" : "s"}`
      if (typeof meta.role === "string") return `role ${meta.role}`
      if (typeof meta.digest === "string") return `digest ${meta.digest.slice(0, 10)}`
      if (typeof meta.payload_sha1 === "string") return `payload ${meta.payload_sha1.slice(0, 10)}`
      if (typeof meta.content_hash === "string") return `content ${meta.content_hash.slice(0, 10)}`
      if (typeof meta.tool_call_count === "number") return `tool calls ${meta.tool_call_count}`
      return null
    },
    [ctreeIncludePreviews],
  )

  const formatCTreeNodeFlags = useCallback((node: any): string[] => {
    const meta = (node.meta ?? {}) as Record<string, unknown>
    const flags: string[] = []
    if (meta.selected === true) flags.push("selected")
    if (meta.kept === true) flags.push("kept")
    if (meta.dropped === true) flags.push("dropped")
    if (meta.collapsed === true) flags.push("collapsed")
    return flags
  }, [])

  const requestTaskTail = useCallback(async (options?: { raw?: boolean; tailLines?: number; maxBytes?: number }) => {
    if (!selectedTask) {
      setTaskNotice("No task selected.")
      return
    }
    const candidates: string[] = []
    if (selectedTask.artifactPath) candidates.push(selectedTask.artifactPath)
    const taskId = selectedTask.id
    candidates.push(`.breadboard/subagents/agent-${taskId}.jsonl`)
    candidates.push(`.breadboard/subagents/${taskId}.jsonl`)
    candidates.push(`.breadboard/subagents/${taskId}.json`)
    let lastError: string | null = null
    for (const pathCandidate of candidates) {
      try {
        const rawMode = options?.raw === true
        const tailLines = Math.max(8, Math.min(400, Math.floor(options?.tailLines ?? 24)))
        const readMode: "cat" | "snippet" = rawMode ? "cat" : "snippet"
        const content = await onReadFile(pathCandidate, {
          mode: readMode,
          headLines: rawMode ? undefined : 4,
          tailLines: rawMode ? undefined : tailLines,
          maxBytes: options?.maxBytes ?? (rawMode ? 120_000 : 40_000),
        })
        const lines = content.content.replace(/\\r\\n?/g, "\\n").split("\\n")
        setTaskTailLines(lines)
        setTaskTailPath(content.path)
        const truncated = content.truncated ? " (truncated)" : ""
        const modeLabel = rawMode ? "raw" : `tail ${tailLines}`
        setTaskNotice(`Loaded ${content.path} (${modeLabel})${truncated}`)
        return
      } catch (error) {
        lastError = (error as Error).message
      }
    }
    setTaskNotice(lastError ?? "Unable to load task output.")
  }, [onReadFile, selectedTask, setTaskNotice, setTaskTailLines, setTaskTailPath])

  return {
    inspectRawLines,
    inspectRawMaxScroll,
    PANEL_WIDTH,
    modelColumnLayout,
    modelMenuCompact,
    modelMenuHeaderText,
    formatModelRowText,
    CONTENT_PADDING,
    MAX_VISIBLE_MODELS,
    MODEL_VISIBLE_ROWS,
    SKILLS_VISIBLE_ROWS,
    animationTick,
    spinner,
    overlayFlags,
    inputLocked,
    overlayActive,
    input,
    cursor,
    suggestIndex,
    setSuggestIndex,
    suppressSuggestions: mergedSuppress,
    setSuppressSuggestions,
    attachments,
    handleAttachment,
    removeLastAttachment,
    clearAttachments,
    inputTextVersion,
    inputValueRef,
    handleLineEdit,
    handleLineEditGuarded,
    pushHistoryEntry,
    recallHistory,
    moveCursorVertical,
    suggestions,
    activeSlashQuery,
    maxVisibleSuggestions,
    inputMaxVisibleLines,
    wrapSuggestionText,
    suggestionPrefix,
    suggestionCommandWidth,
    suggestionLayout,
    buildSuggestionLines,
    suggestionWindow,
    paletteItems,
    transcriptViewerLines,
    transcriptToolLines,
    transcriptViewerBodyRows,
    transcriptViewerMaxScroll,
    transcriptViewerEffectiveScroll,
    transcriptSearchMatches,
    transcriptSearchSafeIndex,
    transcriptSearchActiveLine,
    transcriptSearchLineMatches,
    jumpTranscriptToLine,
    permissionDiffSections,
    permissionDiffPreview,
    permissionSelectedFileIndex,
    permissionSelectedSection,
    permissionDiffLines,
    permissionViewportRows,
    rewindCheckpoints,
    rewindSelectedIndex,
    rewindVisibleLimit,
    rewindOffset,
    rewindVisible,
    todoRows,
    todoViewportRows,
    todoMaxScroll,
    taskRows: taskRowsForDisplay,
    taskLaneOrder,
    taskFocusLaneId,
    taskFocusLaneLabel,
    taskViewportRows,
    taskMaxScroll,
    selectedTaskIndex,
    selectedTask,
    ctreeRows,
    ctreeCollapsibleIds,
    ctreeViewportRows,
    ctreeMaxScroll,
    selectedCTreeIndex,
    selectedCTreeRow,
    formatCTreeNodeLabel,
    formatCTreeNodePreview,
    formatCTreeNodeFlags,
    requestTaskTail,
  }
}
