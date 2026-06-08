import React, { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { Box, Text } from "ink"
import { HEADER_COLOR } from "../../../viewUtils.js"
import { useScrollbackFeed } from "../scrollback/useScrollbackFeed.js"
import { buildTranscript } from "../../../transcriptBuilder.js"
import type { TranscriptItem } from "../../../transcriptModel.js"
import type { ToolLogEntry, ToolLogKind } from "../../../types.js"
import { useToolRenderer } from "../renderers/toolRenderer.js"
import { resolveDiffRenderStyle } from "../renderers/diffStyles.js"
import { useConversationMeasure, useConversationRenderer } from "../renderers/conversationRenderer.js"
import { blocksToLinesWithRawFallback, renderMarkdownFallbackLines } from "../renderers/markdown/streamMdxAdapter.js"
import { buildInitialFragmentState, applyFrozenChunkToFragmentState, findActiveFrozenOverlap, hotTailBlockIds } from "../renderers/markdown/assistantMessageFragmentModel.js"
import { expandMarkdownBlocksForPromotion } from "../renderers/markdown/markdownPromotionUnits.js"
import { planStaticPromotions } from "../renderers/markdown/staticPromotionPlanner.js"
import { StreamMdxBlockStore } from "../renderers/markdown/streamMdxBlockStore.js"
import { TerminalLineChunkCache } from "../renderers/markdown/terminalLineChunkCache.js"
import { sliceTailByLineBudget, trimTailByLineCount } from "../layout/windowing.js"
import { isInlineThinkingBlockText, stripInlineThinkingMarker } from "../../../transcriptUtils.js"
import { CHALK, COLORS, DOT_SEPARATOR } from "../theme.js"
import { filterReadableHints, isLifecycleNoiseHint } from "../../../../commands/repl/hintPolicy.js"
import { formatCostUsd, formatLatency } from "../utils/format.js"
import { stringWidth, stripAnsiCodes } from "../utils/ansi.js"
import { getTodoPreviewRowCount, type TodoPreviewModel } from "../composer/todoPreview.js"
import { getThinkingPreviewRowCount, type ThinkingPreviewModel } from "../composer/thinkingPreview.js"
import { useReplViewRenderNodes } from "./useReplViewRenderNodes.js"
import { applyTranscriptMemoryBounds } from "./transcriptMemoryBounds.js"
import {
  buildLandingContext,
  buildScrollbackLanding,
  buildScrollbackSessionHeaderLines,
  buildScrollbackSessionHeader,
  getScrollbackLandingRowCount,
  resolveLandingVariantForViewport,
} from "../landing/scrollbackLanding.js"
import {
  buildSubagentStripSummary,
  createSubagentStripLifecycleState,
  reduceSubagentStripLifecycle,
  type SubagentStripSummary,
} from "./subagentStrip.js"
import { buildCommittedGroups, computeScrollbackSurfaceModel } from "./scrollbackSurfaceModel.js"
import { applyMainBufferFollowSnapshot, captureMainBufferFollowSnapshot, type MainBufferFollowSnapshot } from "./mainBufferFollow.js"
import { buildManagedViewportResetKey, computeManagedBodyRows, shouldAppendHistoryLanding, shouldShowInlineSessionHeader } from "./scrollbackViewportPolicy.js"
import { resolveLandingLifecycle } from "./landingLifecycle.js"
import { writeAppStartAnchorDebugRecord, writeManagedRegionBoundsDebugRecord, writeMarkdownMetricsDebugRecord, writeRenderTimelineDebugRecord, writeSurfaceModelDebugRecord, writeViewportResetDebugRecord } from "./qcDebugLog.js"
import type { StaticFeedItem } from "../types.js"

type ScrollbackContext = Record<string, any>

const padVisibleEnd = (value: string, width: number): string => {
  const safeWidth = Math.max(1, Math.floor(width))
  const visibleLength = stringWidth(stripAnsiCodes(value))
  return visibleLength >= safeWidth ? value : `${value}${" ".repeat(safeWidth - visibleLength)}`
}

const parseBoundedIntEnv = (value: string | undefined, fallback: number, min: number, max: number): number => {
  if (!value?.trim()) return fallback
  const parsed = Number.parseInt(value.trim(), 10)
  if (!Number.isFinite(parsed)) return fallback
  if (parsed < min) return min
  if (parsed > max) return max
  return parsed
}

const parseBooleanEnv = (value: string | undefined, fallback: boolean): boolean => {
  if (!value?.trim()) return fallback
  const normalized = value.trim().toLowerCase()
  if (["1", "true", "yes", "on"].includes(normalized)) return true
  if (["0", "false", "no", "off"].includes(normalized)) return false
  return fallback
}

const SUBAGENT_STRIP_IDLE_COOLDOWN_MS = parseBoundedIntEnv(
  process.env.BREADBOARD_SUBAGENTS_STRIP_IDLE_COOLDOWN_MS,
  1500,
  0,
  60_000,
)
const SUBAGENT_STRIP_MIN_UPDATE_MS = parseBoundedIntEnv(
  process.env.BREADBOARD_SUBAGENTS_STRIP_UPDATE_MS,
  120,
  0,
  5_000,
)
const TRANSCRIPT_MEMORY_BOUNDS_ENABLED = parseBooleanEnv(process.env.BREADBOARD_TRANSCRIPT_MEMORY_BOUNDS_V1, false)
const TRANSCRIPT_MEMORY_BOUNDS_MAX_ITEMS = parseBoundedIntEnv(
  process.env.BREADBOARD_TRANSCRIPT_MEMORY_BOUNDS_MAX_ITEMS,
  1_200,
  50,
  100_000,
)
const TRANSCRIPT_MEMORY_BOUNDS_MAX_BYTES = parseBoundedIntEnv(
  process.env.BREADBOARD_TRANSCRIPT_MEMORY_BOUNDS_MAX_BYTES,
  2_000_000,
  1_024,
  50_000_000,
)
const TRANSCRIPT_MEMORY_BOUNDS_MARKER = parseBooleanEnv(
  process.env.BREADBOARD_TRANSCRIPT_MEMORY_BOUNDS_MARKER,
  true,
)
const MARKDOWN_STATIC_PROMOTION_ENABLED = parseBooleanEnv(process.env.BREADBOARD_MARKDOWN_STATIC_PROMOTION, false)
const MARKDOWN_STATIC_PROMOTION_HOLDBACK_ROWS = parseBoundedIntEnv(process.env.BREADBOARD_MARKDOWN_STATIC_PROMOTION_HOLDBACK_ROWS, 1, 1, 40)
const MARKDOWN_STATIC_PROMOTION_RESIZE_QUIET_MS = parseBoundedIntEnv(process.env.BREADBOARD_MARKDOWN_STATIC_PROMOTION_RESIZE_QUIET_MS, 250, 0, 10_000)

const NETWORK_BANNER_ROWS = 7
const COLLAPSED_HINT_ROWS = 2
const OWNED_SCENE_HOST_ROWS = 1

const countLiveSlotRows = (slots: ReadonlyArray<{ summary?: string | null }>): number => {
  let rows = 0
  for (const slot of slots) {
    rows += 1
    if (typeof slot.summary === "string" && slot.summary.trim().length > 0) rows += 1
  }
  return rows
}

const countVisibleTranscriptRows = (
  entries: ReadonlyArray<TranscriptItem>,
  measureTranscriptEntryLines: (entry: TranscriptItem) => number,
): number => {
  if (entries.length === 0) return 0
  const contentRows = entries.reduce((total, entry) => total + Math.max(1, measureTranscriptEntryLines(entry)), 0)
  // ReplViewRenderNodes inserts one blank row before the first transcript entry
  // and one separator row between each rendered entry.
  const gapRows = entries.length
  return contentRows + gapRows
}

const countScrollbackTranscriptReclaimRows = (
  entries: ReadonlyArray<TranscriptItem>,
  measureTranscriptEntryLines: (entry: TranscriptItem) => number,
): number => {
  const rows = countVisibleTranscriptRows(entries, measureTranscriptEntryLines)
  if (rows === 0) return 0
  const hasAssistantMessage = entries.some((entry) =>
    entry.kind === "message" &&
    entry.speaker === "assistant"
  )
  // Assistant markdown can render a leading structural blank before rich-block
  // finalization has settled. Reserve one reclaim row for every visible
  // assistant message so the composer never overwrites the final answer line.
  return rows + (hasAssistantMessage ? 1 : 0)
}

const isAssistantMarkdownReadyForStaticFeed = (entry: TranscriptItem): boolean => {
  if (entry.kind !== "message" || entry.speaker !== "assistant") return true
  if (entry.phase !== "final") return false
  if (entry.markdownStreaming) return false
  const hasRichBlocks = Array.isArray(entry.richBlocks) && entry.richBlocks.length > 0
  if (!hasRichBlocks) return true
  // stream-mdx finalization can settle after the engine has already marked the
  // turn complete. Once the assistant entry is final and non-streaming, the
  // feed renderer can safely use rich blocks plus raw-text fallback; waiting for
  // block finalization metadata leaves large answers trapped in the volatile
  // active band and can drop middle content from native scrollback.
  if (String(entry.text ?? "").trim().length > 0) return true
  return entry.markdownFinalized === true || Boolean(entry.markdownError)
}

const isScrollbackStaticFeedOwnedEntry = (entry: TranscriptItem): boolean => {
  if (entry.kind !== "message") return true
  if (entry.speaker === "user") return true
  return entry.speaker === "assistant" && isAssistantMarkdownReadyForStaticFeed(entry)
}

export const shouldRenderScrollbackActiveEntry = (
  entry: TranscriptItem,
  printedTranscriptIds: ReadonlySet<string>,
): boolean => {
  if (isScrollbackStaticFeedOwnedEntry(entry)) {
    // A committed row is only safe to remove from the active repaint band
    // after the static feed has recorded that it was printed. Otherwise fast
    // settle/resize sequences can create a terminal-visible handoff gap.
    return !printedTranscriptIds.has(entry.id)
  }
  return !printedTranscriptIds.has(entry.id)
}

const normalizeMarkdownPromotionLine = (line: string): string => {
  const trimmed = line.trim()
  if (!trimmed) return ""
  // stream-mdx heading blocks store the heading text without the markdown
  // marker, while final raw markdown still includes it. Treat those as the
  // same line so static-promotion fallback does not re-append headings.
  return trimmed.replace(/^#{1,6}\s+/, "")
}

export const useReplViewScrollback = (context: ScrollbackContext) => {
  // Scrollback ownership is split deliberately:
  // - warm landing can still be reprojected while it remains part of the
  //   managed visible region
  // - hot/live material is render-model driven and width-sensitive
  // - cold committed history is appended and then treated as durable transcript
  // This hook should prepare those surfaces without reintroducing a global
  // "rebuild the whole main buffer" policy on ordinary resize.
  const {
    filteredModels,
    modelMenu,
    modelOffset,
    MODEL_VISIBLE_ROWS,
    modelProviderCounts,
    normalizeProviderKey,
    formatProviderLabel,
    stats,
    mode,
    permissionMode,
    claudeChrome,
    pendingResponse,
    mainFollowTail,
    disconnected,
    status,
    contentWidth,
    rowCount,
    guardrailNotice,
    viewPrefs,
    overlayActive,
    filePickerActive,
    fileMenuMode,
    filePicker,
    fileIndexMeta,
    fileMenuRows,
    fileMenuWindow,
    fileMenuNeedlePending,
    selectedFileIsLarge,
    suggestions,
    suggestionWindow,
    hints,
    todoPreviewModel,
    thinkingPreviewModel,
    runtimeStatusChips,
    attachments,
    fileMentions,
    workGraph,
    transcriptViewerOpen,
    transcriptNudge,
    completionHint,
    statusLinePosition,
    statusLineAlign,
    renderToolEntryForFeed,
    renderConversationEntryRef,
    renderToolEntryRef,
    pushCommandResultRef,
    conversation,
    toolEvents,
    SCROLLBACK_MODE,
    screenReaderMode,
    screenReaderProfile,
    ASCII_HEADER,
    setCollapsedVersion,
    collapsedEntriesRef,
    collapsedVersion,
    selectedCollapsibleEntryId,
    setSelectedCollapsibleEntryId,
    animationTick,
    spinner,
    liveSlots,
    keymap,
    footerV2Enabled,
    liveShellOwnershipMode,
    tuiConfig,
  } = context
  const subagentStripLifecycleRef = useRef(createSubagentStripLifecycleState())
  const subagentStripSummaryRef = useRef<SubagentStripSummary | null>(null)
  const subagentStripTimerRef = useRef<NodeJS.Timeout | null>(null)
  const markdownBlockStoreRef = useRef(new StreamMdxBlockStore())
  const markdownChunkCacheRef = useRef(new TerminalLineChunkCache())
  const promotedMarkdownChunkIdsRef = useRef(new Set<string>())
  const frozenMarkdownBlockIdsRef = useRef(new Map<string, Set<string>>())
  const [subagentStrip, setSubagentStrip] = useState<SubagentStripSummary | null>(null)

  const visibleModels = useMemo(() => {
    if (modelMenu.status !== "ready") return []
    return filteredModels.slice(modelOffset, modelOffset + MODEL_VISIBLE_ROWS)
  }, [filteredModels, modelMenu.status, modelOffset, MODEL_VISIBLE_ROWS])

  const visibleModelRows = useMemo(() => {
    if (modelMenu.status !== "ready") return []
    const rows: Array<{ kind: "header"; label: string; count: number | null } | { kind: "item"; item: any; index: number }> = []
    for (let idx = 0; idx < visibleModels.length; idx += 1) {
      const item = visibleModels[idx]
      const globalIndex = modelOffset + idx
      const prev = filteredModels[globalIndex - 1]
      const providerKey = normalizeProviderKey(item.provider)
      const prevKey = normalizeProviderKey(prev?.provider)
      if (idx === 0 || !prev || prevKey !== providerKey) {
        const label = formatProviderLabel(item.provider)
        const count = modelProviderCounts.get(providerKey) ?? null
        rows.push({ kind: "header", label, count })
      }
      rows.push({ kind: "item", item, index: globalIndex })
    }
    return rows
  }, [filteredModels, modelMenu.status, modelOffset, modelProviderCounts, normalizeProviderKey, visibleModels, formatProviderLabel])

  const headerLines = useMemo(() => {
    if (screenReaderMode) return []
    return ["", ...ASCII_HEADER.map((line: string) => CHALK.hex(HEADER_COLOR)(line))]
  }, [ASCII_HEADER, screenReaderMode])

  const usageSummary = useMemo(() => {
    const usage = stats.usage
    if (!usage) return null
    const parts: string[] = []
    const hasTokenInputs = usage.totalTokens != null || usage.promptTokens != null || usage.completionTokens != null
    if (hasTokenInputs) {
      const total =
        usage.totalTokens ??
        (usage.promptTokens != null || usage.completionTokens != null
          ? (usage.promptTokens ?? 0) + (usage.completionTokens ?? 0)
          : undefined)
      if (total != null && Number.isFinite(total)) {
        parts.push(`tok ${Math.round(total)}`)
      }
    }
    if (usage.costUsd != null && Number.isFinite(usage.costUsd)) {
      parts.push(`cost ${formatCostUsd(usage.costUsd)}`)
    }
    if (usage.latencyMs != null && Number.isFinite(usage.latencyMs)) {
      parts.push(`lat ${formatLatency(usage.latencyMs)}`)
    }
    return parts.length > 0 ? parts.join(" · ") : null
  }, [stats.usage])

  const modeBadge = useMemo(() => {
    const { label, color } = context.normalizeModeLabel(mode)
    return CHALK.hex(color)(`[${label}]`)
  }, [mode, context])
  const permissionBadge = useMemo(() => {
    const { label, color } = context.normalizePermissionLabel(permissionMode)
    return CHALK.hex(color)(`[${label}]`)
  }, [permissionMode, context])
  const headerSubtitleLines = useMemo(() => {
    if (screenReaderMode) {
      const modeLabel = mode ? `mode ${String(mode).trim()}` : "mode unknown"
      const permsLabel = permissionMode ? `perms ${String(permissionMode).trim()}` : "perms default"
      if (screenReaderProfile === "concise") {
        return [CHALK.cyan("breadboard accessibility mode")]
      }
      if (screenReaderProfile === "verbose") {
        const modelLabel = stats.model ? `model ${stats.model}` : "model unknown"
        return [CHALK.cyan("breadboard accessibility mode"), CHALK.dim(`${modeLabel} · ${permsLabel}`), CHALK.dim(modelLabel)]
      }
      return [CHALK.cyan("breadboard accessibility mode"), CHALK.dim(`${modeLabel} · ${permsLabel}`)]
    }
    if (!claudeChrome) {
      return [CHALK.cyan("breadboard — interactive session")]
    }
    const modelLine = stats.model ? `${stats.model} · API Usage Billing` : "Model unknown · API Usage Billing"
    const cwdLine = process.cwd()
    return [
      CHALK.cyan(`breadboard v${context.CLI_VERSION}`),
      CHALK.dim(modelLine),
      CHALK.dim(`mode ${modeBadge}  perms ${permissionBadge}`),
      CHALK.dim(cwdLine),
    ]
  }, [
    claudeChrome,
    mode,
    modeBadge,
    permissionMode,
    permissionBadge,
    screenReaderMode,
    screenReaderProfile,
    stats.model,
    context,
  ])
  const promptRule = useMemo(() => {
    const glyph = tuiConfig.composer.ruleCharacter || "─"
    return glyph.repeat(contentWidth)
  }, [contentWidth, tuiConfig.composer.ruleCharacter])
  const pendingClaudeStatus = useMemo(
    () =>
      !footerV2Enabled && claudeChrome && pendingResponse && tuiConfig.statusLine.showWhenPending
        ? tuiConfig.statusLine.activeText
        : null,
    [claudeChrome, footerV2Enabled, pendingResponse, tuiConfig.statusLine.activeText, tuiConfig.statusLine.showWhenPending],
  )
  const networkBanner = useMemo(() => {
    if (disconnected) {
      if (/^Recovery (needed|stopped)/i.test(status)) return null
      return {
        tone: "error" as const,
        label: "Disconnected",
        message: "Lost connection to the engine. Check network or restart the session.",
      }
    }
    if (status.toLowerCase().startsWith("reconnecting")) {
      return {
        tone: "warning" as const,
        label: "Reconnecting",
        message: status,
      }
    }
    return null
  }, [disconnected, status])
  const bodyNetworkBanner = networkBanner
  const bodyGuardrailNotice = SCROLLBACK_MODE ? null : guardrailNotice

  const compactMode =
    viewPrefs.virtualization === "compact" || (viewPrefs.virtualization === "auto" && rowCount <= 32)

  const transcript = useMemo(() => {
    const includeTools = viewPrefs.toolRail !== false || viewPrefs.rawStream === true
    return buildTranscript(
      {
        conversation,
        toolEvents: includeTools ? toolEvents : [],
        rawEvents: viewPrefs.rawStream ? context.rawEvents ?? [] : [],
      },
      { includeRawEvents: viewPrefs.rawStream === true, pendingToolsInTail: true },
    )
  }, [conversation, toolEvents, viewPrefs.rawStream, viewPrefs.toolRail, context.rawEvents])

  const headerReserveRows = useMemo(() => {
    if (SCROLLBACK_MODE) return 0
    if (screenReaderMode) return 0
    if (footerV2Enabled) return 0
    return claudeChrome ? 0 : 3
  }, [SCROLLBACK_MODE, claudeChrome, footerV2Enabled, screenReaderMode])
  const guardrailReserveRows = useMemo(() => {
    if (!guardrailNotice) return 0
    const expanded = Boolean(guardrailNotice.detail && guardrailNotice.expanded)
    return expanded ? 7 : 6
  }, [guardrailNotice])
  const composerReserveRows = useMemo(() => {
    const outerMargin = 1
    const promptLineRows =
      SCROLLBACK_MODE &&
      !overlayActive &&
      !filePickerActive &&
      suggestions.length === 0 &&
      String(context.input ?? "").length > 0
        ? Math.max(3, Math.min(4, Math.floor(Number(context.inputMaxVisibleLines) || 3)))
        : 1
    const promptRuleRows = claudeChrome ? 2 : 0
    const pendingStatusRows = !footerV2Enabled && claudeChrome && pendingClaudeStatus ? 1 : 0
    const statusChipRows =
      !footerV2Enabled && claudeChrome && Array.isArray(runtimeStatusChips) && runtimeStatusChips.length > 0 ? 1 : 0
    const suggestionRows = (() => {
      if (overlayActive) return 1
      if (filePickerActive) {
        const chromeRows = claudeChrome ? 0 : 2
        const listMargin = claudeChrome ? 0 : 1
        const base = chromeRows + listMargin
        if (fileMenuMode === "tree") {
          if (filePicker.status === "loading" || filePicker.status === "hidden") return base + 1
          if (filePicker.status === "error") return base + 2
        } else {
          if (fileIndexMeta.status === "idle" || fileIndexMeta.status === "scanning") {
            if (fileMenuRows.length === 0) return base + 1
          }
          if (fileIndexMeta.status === "error" && fileMenuRows.length === 0) return base + 2
        }
        if (fileMenuRows.length === 0) return base + 1
        const hiddenRows =
          (fileMenuWindow.hiddenAbove > 0 ? 1 : 0) + (fileMenuWindow.hiddenBelow > 0 ? 1 : 0)
        const fuzzyStatusRows =
          fileMenuMode === "fuzzy"
            ? (fileIndexMeta.status === "idle" || fileIndexMeta.status === "scanning" ? 1 : 0) +
              (fileIndexMeta.truncated ? 1 : 0) +
              (fileMenuNeedlePending ? 1 : 0)
            : 0
        const largeHintRows = selectedFileIsLarge ? 1 : 0
        return base + fileMenuWindow.lineCount + hiddenRows + fuzzyStatusRows + largeHintRows
      }
      if (suggestions.length === 0) return 1
      const hiddenRows =
        (suggestionWindow.hiddenAbove > 0 ? 1 : 0) + (suggestionWindow.hiddenBelow > 0 ? 1 : 0)
      return 1 + suggestionWindow.lineCount + hiddenRows
    })()
    const hintCount = overlayActive ? 0 : Math.min(4, hints.length)
    const claudeStatusRows = claudeChrome
      ? statusLinePosition === "below_input"
        ? context.shortcutsOpen
          ? 1
          : 0
        : 1
      : 0
    const claudeShortcutRows = claudeChrome ? (context.shortcutsOpen ? 6 : 1) : 0
    const hintRows = footerV2Enabled
      ? 0
      : overlayActive
        ? 0
        : claudeChrome
          ? claudeStatusRows + claudeShortcutRows
          : hintCount > 0
            ? 1 + hintCount
            : 0
    const footerRows = footerV2Enabled ? 3 : 0
    const todoPreviewRows =
      overlayActive ? 0 : getTodoPreviewRowCount(todoPreviewModel as TodoPreviewModel | null)
    const thinkingPreviewRows =
      overlayActive ? 0 : getThinkingPreviewRowCount(thinkingPreviewModel as ThinkingPreviewModel | null)
    const attachmentRows = overlayActive ? 0 : attachments.length > 0 ? attachments.length + 3 : 0
    const fileMentionRows = overlayActive ? 0 : fileMentions.length > 0 ? fileMentions.length + 3 : 0
    return (
      outerMargin +
      statusChipRows +
      pendingStatusRows +
      thinkingPreviewRows +
      todoPreviewRows +
      promptRuleRows +
      promptLineRows +
      suggestionRows +
      hintRows +
      attachmentRows +
      fileMentionRows +
      footerRows
    )
  }, [
    attachments.length,
    claudeChrome,
    footerV2Enabled,
    fileMentions.length,
    fileIndexMeta.status,
    fileIndexMeta.truncated,
    fileMenuRows.length,
    fileMenuWindow.hiddenAbove,
    fileMenuWindow.hiddenBelow,
    fileMenuWindow.lineCount,
    fileMenuMode,
    fileMenuNeedlePending,
    filePicker.status,
    filePickerActive,
    hints.length,
    runtimeStatusChips,
    thinkingPreviewModel,
    todoPreviewModel,
    overlayActive,
    pendingClaudeStatus,
    statusLinePosition,
    context.input,
    context.inputMaxVisibleLines,
    suggestions.length,
    suggestionWindow.hiddenAbove,
    suggestionWindow.hiddenBelow,
    suggestionWindow.lineCount,
    selectedFileIsLarge,
  ])

  const overlayReserveRows = useMemo(() => {
    if (!overlayActive) return 0
    // Overlays are rendered inline at composer level; do not reserve extra terminal space.
    return 0
  }, [overlayActive])

  const bodyTopMarginRows = 1
  const bodyBudgetRows = useMemo(() => {
    const available = rowCount - headerReserveRows - guardrailReserveRows - composerReserveRows - bodyTopMarginRows - overlayReserveRows
    return Math.max(0, available)
  }, [composerReserveRows, guardrailReserveRows, headerReserveRows, overlayReserveRows, rowCount])

  const statusGlyph = pendingResponse ? spinner : CHALK.hex("#7CF2FF")("●")
  const modelGlyph = CHALK.hex("#B36BFF")("●")
  const remoteGlyph = stats.remote ? CHALK.hex("#7CF2FF")("●") : CHALK.hex("#475569")("○")
  const toolsGlyph = stats.toolCount > 0 ? CHALK.hex("#FBBF24")("●") : CHALK.hex("#475569")("○")
  const eventsGlyph = stats.eventCount > 0 ? CHALK.hex("#A855F7")("●") : CHALK.hex("#475569")("○")
  const turnGlyph = stats.lastTurn != null ? CHALK.hex("#34D399")("●") : CHALK.hex("#475569")("○")

  const transcriptCommitted = transcript.committed
  const transcriptTail = transcript.tail

  const chromeLabel = claudeChrome ? "Claude Code" : keymap === "codex" ? "Codex" : "Breadboard"
  const configLabel = chromeLabel
  const modelLabel = useMemo(() => {
    const raw = stats.model ?? ""
    if (!raw) return "model"
    const parts = raw.split("/")
    return parts[parts.length - 1] || raw
  }, [stats.model])
  const compactSessionLabel = useMemo(() => {
    const raw = String(context.sessionId ?? "").trim()
    if (!raw) return null
    const tail = raw.length > 8 ? raw.slice(-8) : raw
    return `s ${tail}`
  }, [context.sessionId])
  const compactStatusLabel = useMemo(() => {
    if (stats.lastTurn != null && stats.lastTurn > 1) return `resumed ${DOT_SEPARATOR} turn ${stats.lastTurn}`
    if (stats.lastTurn != null) return `turn ${stats.lastTurn}`
    if (transcriptCommitted.length > 0) return "history"
    return null
  }, [stats.lastTurn, transcriptCommitted.length])
  const landingWidth = useMemo(
    () => Math.max(10, contentWidth - (claudeChrome ? 1 : 0)),
    [claudeChrome, contentWidth],
  )
  const resolvedLandingVariant = useMemo(
    () =>
      resolveLandingVariantForViewport({
        contentWidth: landingWidth,
        maxRows: bodyBudgetRows,
        preferredVariant: tuiConfig.landing.variant,
        modelLabel,
        chromeLabel,
        configLabel,
        cwd: process.cwd(),
        borderStyle: tuiConfig.landing.borderStyle,
        showAsciiArt: tuiConfig.landing.showAsciiArt,
      }),
    [
      bodyBudgetRows,
      chromeLabel,
      configLabel,
      landingWidth,
      modelLabel,
      tuiConfig.landing.borderStyle,
      tuiConfig.landing.showAsciiArt,
      tuiConfig.landing.variant,
    ],
  )
  const landingNode = useMemo(
    () =>
      buildScrollbackLanding(
        buildLandingContext({
          contentWidth: landingWidth,
          modelLabel,
          chromeLabel,
          configLabel,
          cwd: process.cwd(),
          sessionLabel: compactSessionLabel,
          statusLabel: compactStatusLabel,
          variant: resolvedLandingVariant,
          borderStyle: tuiConfig.landing.borderStyle,
          showAsciiArt: tuiConfig.landing.showAsciiArt,
        }),
      ),
    [
      chromeLabel,
      compactSessionLabel,
      compactStatusLabel,
      configLabel,
      landingWidth,
      modelLabel,
      resolvedLandingVariant,
      tuiConfig.landing.borderStyle,
      tuiConfig.landing.showAsciiArt,
    ],
  )
  const landingRowCount = useMemo(
    () =>
      getScrollbackLandingRowCount(
        buildLandingContext({
          contentWidth: landingWidth,
          modelLabel,
          chromeLabel,
          configLabel,
          cwd: process.cwd(),
          sessionLabel: compactSessionLabel,
          statusLabel: compactStatusLabel,
          variant: resolvedLandingVariant,
          borderStyle: tuiConfig.landing.borderStyle,
          showAsciiArt: tuiConfig.landing.showAsciiArt,
        }),
      ),
    [
      chromeLabel,
      compactSessionLabel,
      compactStatusLabel,
      configLabel,
      landingWidth,
      modelLabel,
      resolvedLandingVariant,
      tuiConfig.landing.borderStyle,
      tuiConfig.landing.showAsciiArt,
    ],
  )
  const historyLandingNode = useMemo(
    () =>
      buildScrollbackSessionHeader(
        buildLandingContext({
          contentWidth: landingWidth,
          modelLabel,
          chromeLabel,
          configLabel,
          cwd: process.cwd(),
          sessionLabel: compactSessionLabel,
          statusLabel: compactStatusLabel,
          variant: "compact",
          borderStyle: tuiConfig.landing.borderStyle,
          showAsciiArt: false,
        }),
      ),
    [chromeLabel, compactSessionLabel, compactStatusLabel, configLabel, landingWidth, modelLabel, tuiConfig.landing.borderStyle],
  )
  const historyLandingRowCount = useMemo(
    () =>
      buildScrollbackSessionHeaderLines(
        buildLandingContext({
          contentWidth: landingWidth,
          modelLabel,
          chromeLabel,
          configLabel,
          cwd: process.cwd(),
          sessionLabel: compactSessionLabel,
          statusLabel: compactStatusLabel,
          variant: "compact",
          borderStyle: tuiConfig.landing.borderStyle,
          showAsciiArt: false,
        }),
      ).length,
    [chromeLabel, compactSessionLabel, compactStatusLabel, configLabel, landingWidth, modelLabel, tuiConfig.landing.borderStyle],
  )
  const frozenStartupLandingWidth = useMemo(
    () =>
      // This landing is committed to Ink Static almost immediately in
      // preserved-scrollback mode. Static feed rows cannot be reflow-corrected
      // on later terminal shrink, so wide startup rows must be emitted at a
      // width that remains safe in the compact support lane while preserving
      // the split identity on wide starts.
      Math.max(10, Math.min(landingWidth, 76)),
    [landingWidth],
  )
  const frozenStartupLandingVariant = useMemo(
    () => (frozenStartupLandingWidth >= 76 ? "split" : "compact"),
    [frozenStartupLandingWidth],
  )
  const frozenStartupLandingShowAsciiArt = tuiConfig.landing.showAsciiArt && frozenStartupLandingWidth >= 76
  const frozenStartupLandingNode = useMemo(
    () =>
      buildScrollbackLanding(
        buildLandingContext({
          contentWidth: frozenStartupLandingWidth,
          modelLabel,
          chromeLabel,
          configLabel,
          cwd: process.cwd(),
          sessionLabel: compactSessionLabel,
          statusLabel: compactStatusLabel,
          variant: frozenStartupLandingVariant,
          borderStyle: tuiConfig.landing.borderStyle,
          showAsciiArt: frozenStartupLandingShowAsciiArt,
        }),
      ),
    [
      chromeLabel,
      compactSessionLabel,
      compactStatusLabel,
      configLabel,
      frozenStartupLandingWidth,
      frozenStartupLandingVariant,
      frozenStartupLandingShowAsciiArt,
      modelLabel,
      tuiConfig.landing.borderStyle,
    ],
  )
  const frozenStartupLandingRowCount = useMemo(
    () =>
      getScrollbackLandingRowCount(
        buildLandingContext({
          contentWidth: frozenStartupLandingWidth,
          modelLabel,
          chromeLabel,
          configLabel,
          cwd: process.cwd(),
          sessionLabel: compactSessionLabel,
          statusLabel: compactStatusLabel,
          variant: frozenStartupLandingVariant,
          borderStyle: tuiConfig.landing.borderStyle,
          showAsciiArt: frozenStartupLandingShowAsciiArt,
        }),
      ),
    [
      chromeLabel,
      compactSessionLabel,
      compactStatusLabel,
      configLabel,
      frozenStartupLandingWidth,
      frozenStartupLandingVariant,
      frozenStartupLandingShowAsciiArt,
      modelLabel,
      tuiConfig.landing.borderStyle,
    ],
  )
  const [landingRetired, setLandingRetired] = useState<boolean>(context.landingAlways)
  const [landingEverVisibleInline, setLandingEverVisibleInline] = useState(false)
  const [pausedFollowSnapshot, setPausedFollowSnapshot] = useState<MainBufferFollowSnapshot | null>(null)

  useEffect(() => {
    setLandingRetired(context.landingAlways)
    setLandingEverVisibleInline(false)
    setPausedFollowSnapshot(null)
  }, [context.landingAlways, context.sessionId, context.viewClearAt])

  const ownedSceneMode = liveShellOwnershipMode === "owned-live"

  const preserveLatestSettledTurn =
    (ownedSceneMode || SCROLLBACK_MODE) &&
    mainFollowTail &&
    !pendingResponse &&
    transcriptTail.length === 0 &&
    transcriptCommitted.length > 0

  const frozenLandingNode = frozenStartupLandingNode
  const frozenLandingLineCount = frozenStartupLandingRowCount
  const emittedLandingVariant = SCROLLBACK_MODE ? frozenStartupLandingVariant : resolvedLandingVariant
  const emittedLandingRowCount = SCROLLBACK_MODE ? frozenStartupLandingRowCount : landingRowCount
  const sessionHeaderNode = SCROLLBACK_MODE ? null : historyLandingNode

  const toToolLogEntry = useCallback((entry: TranscriptItem): ToolLogEntry | null => {
    if (entry.kind === "tool") {
      return {
        id: entry.id,
        kind: entry.toolKind,
        text: entry.text,
        status: entry.status,
        callId: entry.callId ?? null,
        display: entry.display ?? null,
        createdAt: entry.createdAt,
      }
    }
    if (entry.kind === "system") {
      const mapKind = (kind: TranscriptItem["kind"] | string): ToolLogKind => {
        if (kind === "error") return "error"
        if (kind === "reward") return "reward"
        if (kind === "completion") return "completion"
        return "status"
      }
      return {
        id: entry.id,
        kind: mapKind(entry.systemKind),
        text: entry.text,
        status: entry.status,
        createdAt: entry.createdAt,
      }
    }
    return null
  }, [])

  const diffRenderStyle = useMemo(() => resolveDiffRenderStyle(tuiConfig), [tuiConfig])
  const markdownRenderOptions = useMemo(
    () => ({ diffStyle: diffRenderStyle }),
    [diffRenderStyle],
  )
  const staticFeedMarkdownWidth = useMemo(() => Math.max(1, Math.floor(contentWidth - 2)), [contentWidth])
  const renderStaticConversationEntryForFeed = useCallback(
    (entry: any, key?: string) => {
      if (entry.speaker === "system") return null
      const normalizedText = stripInlineThinkingMarker(String(entry.text ?? "")).trimEnd()
      const rawText = normalizedText.trim()
      const hasRichBlocks = Array.isArray(entry.richBlocks) && entry.richBlocks.length > 0
      if (!hasRichBlocks && (!rawText || rawText.toLowerCase() === "none")) return null
      const lines =
        entry.speaker === "assistant" && hasRichBlocks
          ? blocksToLinesWithRawFallback(entry.richBlocks, normalizedText, { ...markdownRenderOptions, width: staticFeedMarkdownWidth })
          : entry.speaker === "assistant"
            ? renderMarkdownFallbackLines(normalizedText, { ...markdownRenderOptions, width: staticFeedMarkdownWidth })
            : normalizedText.split(/\r?\n/)
      const label = entry.speaker === "user" ? "❯ " : ""
      const padLabel = entry.speaker === "user" ? "  " : ""
      const renderedLines = lines.map((line: string, index: number) =>
        padVisibleEnd(`${index === 0 ? label : padLabel}${line}`, contentWidth),
      )
      if (entry.speaker === "user") renderedLines.push("")
      return (
        <Text key={key ?? entry.id}>{renderedLines.join("\n")}</Text>
      )
    },
    [contentWidth, markdownRenderOptions, staticFeedMarkdownWidth],
  )

  const renderTranscriptEntryForFeed = useCallback(
    (entry: TranscriptItem, key?: string) => {
      if (entry.kind === "message") {
        return renderStaticConversationEntryForFeed(entry as any, key)
      }
      const toolEntry = toToolLogEntry(entry)
      if (!toolEntry) return null
      return renderToolEntryForFeed(toolEntry, key)
    },
    [renderStaticConversationEntryForFeed, renderToolEntryForFeed, toToolLogEntry],
  )

  const markdownStaticPromotion = useMemo(() => {
    if (!SCROLLBACK_MODE || !MARKDOWN_STATIC_PROMOTION_ENABLED || transcriptTail.length === 0) {
      return { chunks: [] as StaticFeedItem[], frozenByMessage: new Map<string, Set<string>>() }
    }
    const staticChunks: StaticFeedItem[] = []
    const frozenByMessage = new Map<string, Set<string>>()
    for (const entry of transcriptTail as any[]) {
      if (entry.kind !== "message" || entry.speaker !== "assistant" || !Array.isArray(entry.richBlocks) || entry.richBlocks.length === 0) continue
      const messageId = String(entry.id)
      const promotionBlocks = expandMarkdownBlocksForPromotion(entry.richBlocks)
      const update = markdownBlockStoreRef.current.update(messageId, promotionBlocks)
      for (const blockId of update.mutationAfterFrozenIds) {
        writeMarkdownMetricsDebugRecord({
          event: "markdown_frozen_mutation_attempt",
          messageId,
          blockId,
        })
      }
      const alreadyFrozen = frozenMarkdownBlockIdsRef.current.get(messageId) ?? new Set<string>()
      const plan = planStaticPromotions({
        messageId,
        snapshots: markdownBlockStoreRef.current.get(messageId),
        cache: markdownChunkCacheRef.current,
        renderOptions: { ...markdownRenderOptions, width: staticFeedMarkdownWidth },
        stabilityPolicy: {
          holdbackRows: MARKDOWN_STATIC_PROMOTION_HOLDBACK_ROWS,
          resizeQuietMs: MARKDOWN_STATIC_PROMOTION_RESIZE_QUIET_MS,
          now: Date.now(),
          lastResizeAt: null,
        },
        alreadyPromotedChunkIds: promotedMarkdownChunkIdsRef.current,
        alreadyFrozenBlockIds: alreadyFrozen,
      })
      for (const chunkId of plan.duplicateChunkIds) {
        writeMarkdownMetricsDebugRecord({
          event: "markdown_static_chunk_duplicate_attempt",
          messageId,
          chunkId,
        })
      }
      let fragmentState = buildInitialFragmentState(messageId, promotionBlocks.map((block: any) => String(block.id)))
      for (const chunk of plan.chunks) {
        const staticFeedItemId = `md-static-${chunk.chunkId}`
        const renderedChunkLines = chunk.lines.map((line) => padVisibleEnd(line, contentWidth))
        promotedMarkdownChunkIdsRef.current.add(chunk.chunkId)
        const nextFrozen = frozenMarkdownBlockIdsRef.current.get(messageId) ?? new Set<string>()
        for (const blockId of chunk.blockIds) nextFrozen.add(blockId)
        frozenMarkdownBlockIdsRef.current.set(messageId, nextFrozen)
        markdownBlockStoreRef.current.markFrozen(messageId, chunk.blockIds)
        fragmentState = applyFrozenChunkToFragmentState(fragmentState, chunk, staticFeedItemId)
        staticChunks.push({
          id: staticFeedItemId,
          lineCount: Math.max(1, chunk.rowCount * 2),
          node: (
            <Box flexDirection="column">
              {renderedChunkLines.flatMap((line, index) => [
                <Text key={`${staticFeedItemId}-ln-${index}`}>{line}</Text>,
                <Text key={`${staticFeedItemId}-sentinel-${index}`}> </Text>,
              ])}
            </Box>
          ),
        })
        writeMarkdownMetricsDebugRecord({
          event: "markdown_chunk_promote",
          messageId,
          chunkId: chunk.chunkId,
          staticFeedItemId,
          blockIds: chunk.blockIds,
          rowCount: chunk.rowCount,
          width: chunk.renderOptionsKey.width,
        })
      }
      const overlap = findActiveFrozenOverlap(fragmentState)
      for (const blockId of overlap) {
        writeMarkdownMetricsDebugRecord({
          event: "markdown_active_frozen_overlap",
          messageId,
          blockId,
        })
      }
      const tailIds = plan.hotTailBlockIds
      writeMarkdownMetricsDebugRecord({
        event: "markdown_hot_tail",
        messageId,
        blockIds: tailIds,
        rowCount: tailIds.length,
      })
      frozenByMessage.set(messageId, new Set(frozenMarkdownBlockIdsRef.current.get(messageId) ?? []))
    }
    return { chunks: staticChunks, frozenByMessage }
  }, [SCROLLBACK_MODE, contentWidth, markdownRenderOptions, staticFeedMarkdownWidth, transcriptTail])

  const { measureToolEntryLines, renderToolEntry } = useToolRenderer({
    claudeChrome,
    verboseOutput: context.verboseOutput,
    collapseThreshold: context.TOOL_COLLAPSE_THRESHOLD,
    collapseHead: context.TOOL_COLLAPSE_HEAD,
    collapseTail: context.TOOL_COLLAPSE_TAIL,
    labelWidth: context.TOOL_LABEL_WIDTH,
    contentWidth,
    diffLineNumbers: viewPrefs?.diffLineNumbers,
    diffStyle: diffRenderStyle,
  })

  const { isEntryCollapsible, measureConversationEntryLines } = useConversationMeasure({
    viewPrefs,
    verboseOutput: context.verboseOutput,
    collapsedEntriesRef,
    collapsedVersion,
    contentWidth,
    markdownRenderOptions,
    streamingAssistantPreviewLines: SCROLLBACK_MODE ? 1 : undefined,
  })

  const measureTranscriptEntryLines = useCallback(
    (entry: TranscriptItem) => {
      if (entry.kind === "message") {
        return measureConversationEntryLines(entry as any)
      }
      const toolEntry = toToolLogEntry(entry)
      if (!toolEntry) return 0
      return measureToolEntryLines(toolEntry)
    },
    [measureConversationEntryLines, measureToolEntryLines, toToolLogEntry],
  )

  const scrollbackLandingRowsForActiveSurface = SCROLLBACK_MODE ? 0 : landingRowCount
  const baseScrollbackSurfaceModel = useMemo(
    () =>
      computeScrollbackSurfaceModel({
        committed: transcriptCommitted,
        tail: transcriptTail,
        bodyBudgetRows,
        landingRows: scrollbackLandingRowsForActiveSurface,
        landingAlways: context.landingAlways,
        landingRetired,
        pendingResponse,
        preserveLatestSettledTurn,
        measureTranscriptEntryLines,
      }),
    [
      bodyBudgetRows,
      context.landingAlways,
      landingRetired,
      scrollbackLandingRowsForActiveSurface,
      measureTranscriptEntryLines,
      pendingResponse,
      preserveLatestSettledTurn,
      transcriptCommitted,
      transcriptTail,
    ],
  )

  const pausedFollowSnapshotCandidate =
    SCROLLBACK_MODE && pendingResponse && !mainFollowTail
      ? pausedFollowSnapshot ?? captureMainBufferFollowSnapshot(baseScrollbackSurfaceModel)
      : null

  useEffect(() => {
    if (!SCROLLBACK_MODE || !pendingResponse || mainFollowTail) {
      if (pausedFollowSnapshot) setPausedFollowSnapshot(null)
      return
    }
    if (!pausedFollowSnapshot) {
      setPausedFollowSnapshot(captureMainBufferFollowSnapshot(baseScrollbackSurfaceModel))
    }
  }, [SCROLLBACK_MODE, baseScrollbackSurfaceModel, mainFollowTail, pausedFollowSnapshot, pendingResponse])

  const scrollbackSurfaceModel = useMemo(
    () =>
      pausedFollowSnapshotCandidate
        ? applyMainBufferFollowSnapshot(
            baseScrollbackSurfaceModel,
            pausedFollowSnapshotCandidate,
            transcriptCommitted,
            transcriptTail,
            measureTranscriptEntryLines,
          )
        : baseScrollbackSurfaceModel,
    [
      baseScrollbackSurfaceModel,
      measureTranscriptEntryLines,
      pausedFollowSnapshotCandidate,
      transcriptCommitted,
      transcriptTail,
    ],
  )

  const warmLandingVisible = !landingRetired && scrollbackSurfaceModel.warmLandingVisible
  const activeWindowRenderedRowsEstimate = countVisibleTranscriptRows(
    scrollbackSurfaceModel.activeWindow.items,
    measureTranscriptEntryLines,
  )
  const sessionHeaderFitsActiveRegion =
    activeWindowRenderedRowsEstimate > 0 &&
    historyLandingRowCount + activeWindowRenderedRowsEstimate <= bodyBudgetRows
  const showSessionHeaderInline = shouldShowInlineSessionHeader({
    scrollbackMode: SCROLLBACK_MODE,
    hasSessionHeaderNode: Boolean(sessionHeaderNode),
    landingRetired,
    warmLandingVisible,
    fitsActiveRegion: sessionHeaderFitsActiveRegion,
  })

  const landingShouldRetireForSettledHistory =
    SCROLLBACK_MODE &&
    Boolean(landingNode) &&
    !context.landingAlways &&
    !landingRetired &&
    !pendingResponse &&
    transcriptTail.length === 0 &&
    transcriptCommitted.length > 0

  const landingShouldRetireNow =
    SCROLLBACK_MODE &&
    Boolean(landingNode) &&
    !context.landingAlways &&
    !landingRetired &&
    (!scrollbackSurfaceModel.warmLandingVisible || landingShouldRetireForSettledHistory)

  const landingShouldFreezeToHistory = false

  useEffect(() => {
    if (!landingShouldRetireNow && !landingShouldFreezeToHistory) return
    setLandingRetired(true)
  }, [landingShouldFreezeToHistory, landingShouldRetireNow])

  useEffect(() => {
    if (landingRetired) return
    if (!scrollbackSurfaceModel.warmLandingVisible) return
    setLandingEverVisibleInline(true)
  }, [landingRetired, scrollbackSurfaceModel.warmLandingVisible])

  const appendHeaderToFeed = SCROLLBACK_MODE && !landingNode
  const appendLandingToFeed =
    SCROLLBACK_MODE &&
    Boolean(frozenLandingNode) &&
    !warmLandingVisible &&
    (landingRetired || landingShouldRetireNow)
  const transcriptEntriesForFeed = useMemo(() => {
    if (!SCROLLBACK_MODE) return transcriptCommitted
    // In preserved scrollback mode, user requests are append-only transcript
    // anchors, completed tool rows are durable transcript facts, and finalized
    // Markdown-ready assistant messages move into the append-only feed as soon
    // as they settle. Keeping large final answers in the live band until they
    // become cold can make terminal history retain only repeated streaming tails.
    return transcriptCommitted.filter((entry) => {
      if (entry.kind === "message" && entry.speaker === "user") return true
      if (entry.kind === "tool") return true
      if (entry.kind === "message" && entry.speaker === "assistant") {
        return isAssistantMarkdownReadyForStaticFeed(entry)
      }
      return false
    })
  }, [SCROLLBACK_MODE, pendingResponse, scrollbackSurfaceModel.coldCommitted, transcriptCommitted, transcriptTail.length, viewPrefs.collapseMode])
  const promotionFilteredTranscriptEntriesForFeed = useMemo(() => {
    if (!MARKDOWN_STATIC_PROMOTION_ENABLED) return transcriptEntriesForFeed
    const filtered: TranscriptItem[] = []
    let changed = false
    for (const entry of transcriptEntriesForFeed as any[]) {
      if (entry.kind !== "message" || entry.speaker !== "assistant") {
        filtered.push(entry)
        continue
      }
      const frozen =
        markdownStaticPromotion.frozenByMessage.get(String(entry.id)) ??
        frozenMarkdownBlockIdsRef.current.get(String(entry.id))
      if (!frozen || frozen.size === 0) {
        filtered.push(entry)
        continue
      }
      changed = true
      const sourceBlocks = Array.isArray(entry.richBlocks) && entry.richBlocks.length > 0
        ? expandMarkdownBlocksForPromotion(entry.richBlocks)
        : markdownBlockStoreRef.current.get(String(entry.id)).map((snapshot) => snapshot.block)
      const residualBlocks = sourceBlocks.filter((block: any) => !frozen.has(String(block.id)))
      const representedRawLines = new Set<string>()
      for (const block of sourceBlocks as any[]) {
        const raw = String(block?.payload?.raw ?? "")
        for (const line of raw.split(/\r?\n/)) {
          const normalized = normalizeMarkdownPromotionLine(line)
          if (normalized) representedRawLines.add(normalized)
        }
      }
      const rawTailFallbackBlocks: any[] = []
      let rawTailFallbackIndex = 0
      for (const line of String(entry.text ?? "").split(/\r?\n/)) {
        const normalized = normalizeMarkdownPromotionLine(line)
        if (!normalized || representedRawLines.has(normalized)) continue
        rawTailFallbackBlocks.push({
          id: `${entry.id}:raw-tail-fallback:${rawTailFallbackIndex++}`,
          type: /^(\s*)([*+-]|\d+\.)\s+/.test(line) ? "list-item" : "paragraph",
          isFinalized: true,
          payload: { raw: line },
        })
        representedRawLines.add(normalized)
      }
      const safeResidualBlocks = rawTailFallbackBlocks.length > 0
        ? [...residualBlocks, ...rawTailFallbackBlocks]
        : residualBlocks
      if (safeResidualBlocks.length === 0) continue
      filtered.push({
        ...entry,
        // The feed renderer must not fall back to the original full markdown
        // text after promotion; residual rich blocks are the only safe append.
        text: "",
        richBlocks: safeResidualBlocks,
      })
    }
    return changed ? filtered : transcriptEntriesForFeed
  }, [markdownStaticPromotion.frozenByMessage, transcriptEntriesForFeed])
  const landingLifecycle = useMemo(
    () =>
      resolveLandingLifecycle({
        scrollbackMode: SCROLLBACK_MODE,
        hasLandingNode: Boolean(landingNode),
        landingVariant: emittedLandingVariant,
        landingRows: emittedLandingRowCount,
        bodyBudgetRows,
        warmLandingVisible,
        landingRetired,
        landingShouldRetireNow,
        appendLandingToFeed,
        landingAlways: context.landingAlways,
        transcriptCommittedCount: transcriptCommitted.length,
        transcriptTailCount: transcriptTail.length,
        pendingResponse,
      }),
    [
      SCROLLBACK_MODE,
      appendLandingToFeed,
      bodyBudgetRows,
      context.landingAlways,
      emittedLandingRowCount,
      emittedLandingVariant,
      landingNode,
      landingRetired,
      landingShouldRetireNow,
      pendingResponse,
      transcriptCommitted.length,
      transcriptTail.length,
      warmLandingVisible,
    ],
  )

  const { staticFeed, staticFeedLineCount, pushCommandResult: pushCommandResultFromFeed, printedTranscriptIdsRef } =
    useScrollbackFeed({
      enabled: SCROLLBACK_MODE,
      sessionId: context.sessionId,
      viewClearAt: context.viewClearAt,
      headerLines,
      headerSubtitleLines,
      historyLandingNode: frozenLandingNode,
      historyLandingLineCount: frozenLandingLineCount,
      appendHeaderToFeed,
      appendLandingToFeed,
      transcriptEntries: promotionFilteredTranscriptEntriesForFeed,
      streamingEntries: transcriptTail,
      staticMarkdownChunks: markdownStaticPromotion.chunks,
      allowTranscriptAppend: true,
      renderTranscriptEntry: renderTranscriptEntryForFeed,
      measureTranscriptEntryLines,
      transcriptViewerOpen,
    })

  useEffect(() => {
    pushCommandResultRef.current = pushCommandResultFromFeed
  }, [pushCommandResultFromFeed, pushCommandResultRef])

  const ownedSceneSettledEntries = useMemo(() => {
    if (!ownedSceneMode || pendingResponse || transcriptTail.length > 0) return null
    const groups = buildCommittedGroups(transcriptCommitted, measureTranscriptEntryLines)
    const latestGroup = groups[groups.length - 1]
    return latestGroup ? [...latestGroup.items] : []
  }, [measureTranscriptEntryLines, ownedSceneMode, pendingResponse, transcriptCommitted, transcriptTail.length])

  const transcriptEntriesForWindow = useMemo(() => {
    if (ownedSceneMode) {
      return ownedSceneSettledEntries ?? scrollbackSurfaceModel.activeWindow.items
    }
    if (!SCROLLBACK_MODE) {
      const base = transcriptTail.length > 0 ? [...transcriptCommitted, ...transcriptTail] : transcriptCommitted
      if (transcriptNudge > 0) {
        return trimTailByLineCount(base, transcriptNudge, measureTranscriptEntryLines)
      }
      return base
    }
    if (SCROLLBACK_MODE) {
      return scrollbackSurfaceModel.activeWindow.items.filter((entry) => {
        if (entry.kind === "message" && entry.speaker === "user") {
          // User requests are appended once through the static feed. Never
          // repaint them through the active band, where resize/final renders
          // would create duplicate prompt cards in native scrollback.
          return false
        }
        if (entry.kind === "tool" && entry.status !== "pending") {
          // Completed tool rows are append-only transcript facts. Repainting
          // them inside the active region during long assistant streams can
          // push duplicate tool blocks into native terminal scrollback.
          return false
        }
        // In preserved scrollback mode, durable committed entries eventually
        // belong to the static feed. Keep them in the live band until the feed
        // has actually printed that id so fast settle/resize transitions cannot
        // hide the latest prompt or assistant response.
        return shouldRenderScrollbackActiveEntry(entry, printedTranscriptIdsRef.current)
      })
    }
    return scrollbackSurfaceModel.activeWindow.items
  }, [measureTranscriptEntryLines, ownedSceneMode, ownedSceneSettledEntries, pendingResponse, printedTranscriptIdsRef, SCROLLBACK_MODE, scrollbackSurfaceModel.activeWindow.items, staticFeedLineCount, transcriptCommitted, transcriptNudge, transcriptTail])

  const promotedFilteredTranscriptEntriesForWindow = useMemo(() => {
    if (!MARKDOWN_STATIC_PROMOTION_ENABLED || markdownStaticPromotion.frozenByMessage.size === 0) return transcriptEntriesForWindow
    return transcriptEntriesForWindow.map((entry: any) => {
      if (entry.kind !== "message" || entry.speaker !== "assistant" || !Array.isArray(entry.richBlocks)) return entry
      const frozen = markdownStaticPromotion.frozenByMessage.get(String(entry.id))
      if (!frozen || frozen.size === 0) return entry
      const promotionBlocks = expandMarkdownBlocksForPromotion(entry.richBlocks)
      const richBlocks = promotionBlocks.filter((block: any) => !frozen.has(String(block.id)))
      return { ...entry, richBlocks }
    })
  }, [markdownStaticPromotion.frozenByMessage, transcriptEntriesForWindow])

  const boundedTranscript = useMemo(
    () =>
      applyTranscriptMemoryBounds(promotedFilteredTranscriptEntriesForWindow, {
        enabled: TRANSCRIPT_MEMORY_BOUNDS_ENABLED,
        maxItems: TRANSCRIPT_MEMORY_BOUNDS_MAX_ITEMS,
        maxBytes: TRANSCRIPT_MEMORY_BOUNDS_MAX_BYTES,
        includeCompactionMarker: TRANSCRIPT_MEMORY_BOUNDS_MARKER,
      }),
    [promotedFilteredTranscriptEntriesForWindow],
  )

  const transcriptLineBudget = overlayActive ? Math.min(10, bodyBudgetRows) : bodyBudgetRows
  const conversationWindow = useMemo(
    () =>
      SCROLLBACK_MODE || ownedSceneMode
        ? {
            ...scrollbackSurfaceModel.activeWindow,
            items: boundedTranscript.items,
            hiddenCount:
              ownedSceneMode && ownedSceneSettledEntries
                ? Math.max(0, transcriptCommitted.length - boundedTranscript.items.length)
                : scrollbackSurfaceModel.coldCommitted.length,
            truncated:
              ownedSceneMode && ownedSceneSettledEntries
                ? transcriptCommitted.length > boundedTranscript.items.length
                : scrollbackSurfaceModel.coldCommitted.length > 0,
          }
        : sliceTailByLineBudget(boundedTranscript.items, transcriptLineBudget, measureTranscriptEntryLines),
    [
      ownedSceneMode,
      ownedSceneSettledEntries,
      SCROLLBACK_MODE,
      boundedTranscript.items,
      measureTranscriptEntryLines,
      scrollbackSurfaceModel.activeWindow,
      scrollbackSurfaceModel.coldCommitted.length,
      transcriptCommitted.length,
      transcriptLineBudget,
    ],
  )

  const collapsibleEntries = useMemo(
    () =>
      conversationWindow.items.filter((entry: any) => entry.kind === "message" && isEntryCollapsible(entry as any)),
    [conversationWindow, isEntryCollapsible],
  )

  const collapsibleMeta = useMemo(() => {
    const map = new Map<string, { index: number; total: number }>()
    const total = collapsibleEntries.length
    collapsibleEntries.forEach((entry: any, index: number) => {
      map.set(entry.id, { index, total })
    })
    return map
  }, [collapsibleEntries])

  useEffect(() => {
    const map = collapsedEntriesRef.current
    let changed = false
    const activeIds = new Set<string>(collapsibleEntries.map((entry: any) => entry.id))
    for (const key of Array.from(map.keys()) as string[]) {
      if (!activeIds.has(key)) {
        map.delete(key)
        changed = true
      }
    }
    if (viewPrefs.collapseMode === "none") {
      for (const entry of collapsibleEntries as any[]) {
        const id = String(entry.id ?? "")
        if (!id) continue
        const forcedCollapsed = typeof entry.text === "string" && isInlineThinkingBlockText(entry.text)
        if (forcedCollapsed) {
          if (!map.has(id)) {
            map.set(id, true)
            changed = true
          }
          continue
        }
        if (map.get(id) !== false) {
          map.set(id, false)
          changed = true
        }
      }
    } else if (viewPrefs.collapseMode === "all") {
      for (const id of activeIds) {
        if (map.get(id) !== true) {
          map.set(id, true)
          changed = true
        }
      }
    } else {
      for (const id of activeIds) {
        if (!map.has(id)) {
          map.set(id, true)
          changed = true
        }
      }
    }
    if (changed) {
      setCollapsedVersion((value: number) => value + 1)
    }
  }, [collapsibleEntries, viewPrefs.collapseMode, setCollapsedVersion, collapsedEntriesRef])

  useEffect(() => {
    setSelectedCollapsibleEntryId((prev: string | null) => {
      if (collapsibleEntries.length === 0) {
        return prev === null ? prev : null
      }
      if (prev && collapsibleEntries.some((entry: any) => entry.id === prev)) {
        return prev
      }
      const latest = collapsibleEntries[collapsibleEntries.length - 1]
      return latest?.id ?? null
    })
  }, [collapsibleEntries, setSelectedCollapsibleEntryId])

  const toggleCollapsedEntry = useCallback((entryId: string) => {
    const map = collapsedEntriesRef.current
    const isExpanded = map.get(entryId) === false
    map.set(entryId, isExpanded ? true : false)
    setCollapsedVersion((value: number) => value + 1)
  }, [collapsedEntriesRef, setCollapsedVersion])

  const cycleCollapsibleSelection = useCallback(
    (direction: 1 | -1) => {
      if (collapsibleEntries.length === 0) return false
      if (!selectedCollapsibleEntryId) {
        setSelectedCollapsibleEntryId(collapsibleEntries[collapsibleEntries.length - 1]?.id ?? null)
        return true
      }
      const currentIndex = collapsibleEntries.findIndex((entry: any) => entry.id === selectedCollapsibleEntryId)
      const safeIndex = currentIndex === -1 ? collapsibleEntries.length - 1 : currentIndex
      const nextIndex = (safeIndex + direction + collapsibleEntries.length) % collapsibleEntries.length
      setSelectedCollapsibleEntryId(collapsibleEntries[nextIndex].id)
      return true
    },
    [collapsibleEntries, selectedCollapsibleEntryId, setSelectedCollapsibleEntryId],
  )

  const toggleSelectedCollapsibleEntry = useCallback(() => {
    if (!selectedCollapsibleEntryId) return false
    toggleCollapsedEntry(selectedCollapsibleEntryId)
    return true
  }, [selectedCollapsibleEntryId, toggleCollapsedEntry])

  const { renderConversationEntry } = useConversationRenderer({
    viewPrefs,
    collapsedEntriesRef,
    collapsedVersion,
    collapsibleMeta,
    selectedCollapsibleEntryId,
    labelWidth: context.LABEL_WIDTH,
    contentWidth,
    isEntryCollapsible,
    markdownRenderOptions,
    padLineEnds: !SCROLLBACK_MODE,
    streamingAssistantPreviewLines: SCROLLBACK_MODE ? 1 : undefined,
  })

  const renderTranscriptEntry = useCallback(
    (entry: TranscriptItem, key?: string) => {
      if (entry.kind === "message") {
        return renderConversationEntry(entry as any, key)
      }
      const toolEntry = toToolLogEntry(entry)
      if (!toolEntry) return null
      return renderToolEntry(toolEntry, key)
    },
    [renderConversationEntry, renderToolEntry, toToolLogEntry],
  )

  const subagentStripSummary = useMemo(() => {
    if (!tuiConfig.subagents.stripEnabled) return null
    return buildSubagentStripSummary(workGraph)
  }, [tuiConfig.subagents.stripEnabled, workGraph])

  useEffect(() => {
    subagentStripSummaryRef.current = subagentStripSummary
  }, [subagentStripSummary])

  useEffect(() => {
    if (subagentStripTimerRef.current) {
      clearTimeout(subagentStripTimerRef.current)
      subagentStripTimerRef.current = null
    }
    if (!tuiConfig.subagents.stripEnabled) {
      subagentStripLifecycleRef.current = createSubagentStripLifecycleState()
      setSubagentStrip(null)
      return
    }

    const nowMs = Date.now()
    const reduced = reduceSubagentStripLifecycle(subagentStripLifecycleRef.current, {
      summary: subagentStripSummary,
      nowMs,
      idleCooldownMs: SUBAGENT_STRIP_IDLE_COOLDOWN_MS,
      minUpdateMs: SUBAGENT_STRIP_MIN_UPDATE_MS,
    })
    subagentStripLifecycleRef.current = reduced
    setSubagentStrip(reduced.rendered)

    const hasRunning = Boolean(subagentStripSummary && subagentStripSummary.counts.running > 0)
    const waitForCooldownMs = hasRunning ? 0 : Math.max(0, reduced.visibleUntilMs - nowMs)
    const waitForCadenceMs = Math.max(0, reduced.lastUpdatedMs + SUBAGENT_STRIP_MIN_UPDATE_MS - nowMs)
    const wakeMs = Math.max(waitForCooldownMs, waitForCadenceMs)
    if (wakeMs > 0) {
      subagentStripTimerRef.current = setTimeout(() => {
        const next = reduceSubagentStripLifecycle(subagentStripLifecycleRef.current, {
          summary: subagentStripSummaryRef.current,
          nowMs: Date.now(),
          idleCooldownMs: SUBAGENT_STRIP_IDLE_COOLDOWN_MS,
          minUpdateMs: SUBAGENT_STRIP_MIN_UPDATE_MS,
        })
        subagentStripLifecycleRef.current = next
        setSubagentStrip(next.rendered)
      }, wakeMs + 5)
    }
    return () => {
      if (subagentStripTimerRef.current) {
        clearTimeout(subagentStripTimerRef.current)
        subagentStripTimerRef.current = null
      }
    }
  }, [subagentStripSummary, tuiConfig.subagents.stripEnabled])

  useLayoutEffect(() => {
    renderConversationEntryRef.current = renderConversationEntry
    renderToolEntryRef.current = renderToolEntry
  }, [renderConversationEntry, renderToolEntry, renderConversationEntryRef, renderToolEntryRef])

  const activeCompletionHint =
    completionHint && !isLifecycleNoiseHint(completionHint) && !(SCROLLBACK_MODE && !pendingResponse)
      ? completionHint
      : null
  const visibleHints = useMemo(
    () =>
      filterReadableHints(hints),
    [hints],
  )

  const renderNodes = useReplViewRenderNodes({
    liveShellOwnershipMode,
    claudeChrome,
    screenReaderMode,
    screenReaderProfile,
    footerV2Enabled,
    keymap,
    contentWidth,
    hints: visibleHints,
    completionHint: activeCompletionHint,
    statusLinePosition,
    statusLineAlign,
    shortcutsOpen: context.shortcutsOpen,
    ctrlCPrimedAt: context.ctrlCPrimedAt,
    escPrimedAt: context.escPrimedAt,
    pendingResponse,
    scrollbackMode: SCROLLBACK_MODE,
    subagentStrip,
    liveSlots,
    animationTick,
    collapsibleEntries,
    collapsibleMeta,
    selectedCollapsibleEntryId,
    compactMode,
    transcriptWindow: conversationWindow,
    renderTranscriptEntry,
  })

  const runtimePreviewRows = useMemo(() => {
    if (!claudeChrome || overlayActive) return 0
    let rows = 0
    if (pendingClaudeStatus) rows += 1
    if (!footerV2Enabled && Array.isArray(runtimeStatusChips) && runtimeStatusChips.length > 0) rows += 1
    rows += getThinkingPreviewRowCount(thinkingPreviewModel ?? null)
    rows += getTodoPreviewRowCount(todoPreviewModel ?? null)
    rows += visibleHints.length > 0 ? Math.min(4, visibleHints.length) : 0
    return rows
  }, [
    claudeChrome,
    footerV2Enabled,
    overlayActive,
    pendingClaudeStatus,
    runtimeStatusChips,
    thinkingPreviewModel,
    todoPreviewModel,
    visibleHints,
  ])

  const managedTranscriptRows = useMemo(
    () => {
      if (!SCROLLBACK_MODE) return countVisibleTranscriptRows(conversationWindow.items, measureTranscriptEntryLines)
      if (overlayActive && !pendingResponse) return 0
      const renderedRows = countScrollbackTranscriptReclaimRows(conversationWindow.items, measureTranscriptEntryLines)
      const activeReclaimRows = countScrollbackTranscriptReclaimRows(
        scrollbackSurfaceModel.activeWindow.items.filter(
          (entry) => !(entry.kind === "message" && entry.speaker === "user"),
        ),
        measureTranscriptEntryLines,
      )
      return Math.max(renderedRows, activeReclaimRows)
    },
    [SCROLLBACK_MODE, conversationWindow.items, measureTranscriptEntryLines, overlayActive, pendingResponse, scrollbackSurfaceModel.activeWindow.items],
  )

  const managedViewerRows = useMemo(() => {
    if (!ownedSceneMode || !transcriptViewerOpen) return 0
    return Math.max(1, rowCount)
  }, [ownedSceneMode, rowCount, transcriptViewerOpen])

  const managedLiveSlotRows = useMemo(() => {
    if (overlayActive || liveSlots.length === 0) return 0
    return 1 + countLiveSlotRows(liveSlots)
  }, [liveSlots, overlayActive])

  const managedBodyRows = useMemo(() =>
    computeManagedBodyRows({
      scrollbackMode: SCROLLBACK_MODE,
      ownedSceneMode,
      staticFeedLineCount,
      guardrailRows: bodyGuardrailNotice ? guardrailReserveRows : 0,
      networkBannerRows: bodyNetworkBanner ? NETWORK_BANNER_ROWS : 0,
      landingRows: !landingRetired && scrollbackSurfaceModel.warmLandingVisible ? landingRowCount : 0,
      sessionHeaderRows: showSessionHeaderInline ? historyLandingRowCount : 0,
      ownedSceneHostRows: OWNED_SCENE_HOST_ROWS,
      transcriptOrViewerRows: managedViewerRows > 0 ? managedViewerRows : managedTranscriptRows,
      liveSlotRows: managedLiveSlotRows,
      collapsedHintRows: !overlayActive && collapsibleEntries.length > 0 ? COLLAPSED_HINT_ROWS : 0,
      overlayRows: 0,
    }), [
    ownedSceneMode,
    SCROLLBACK_MODE,
    collapsibleEntries.length,
    guardrailNotice,
    guardrailReserveRows,
    landingRetired,
    landingRowCount,
    historyLandingRowCount,
    managedLiveSlotRows,
    managedTranscriptRows,
    managedViewerRows,
    bodyNetworkBanner,
    overlayActive,
    rowCount,
    staticFeedLineCount,
    scrollbackSurfaceModel.warmLandingVisible,
    showSessionHeaderInline,
  ])

  const composerRowsAboveCursor = useMemo(() => {
    if (SCROLLBACK_MODE && !overlayActive) {
      return composerReserveRows
    }
    if (!claudeChrome) return 0
    let rows = runtimePreviewRows
    if (tuiConfig.composer.showTopRule) rows += 1
    if (SCROLLBACK_MODE && !pendingResponse && conversation.length === 0) {
      rows = Math.max(rows, footerV2Enabled ? 3 : 1)
    }
    return rows
  }, [SCROLLBACK_MODE, claudeChrome, composerReserveRows, conversation.length, footerV2Enabled, overlayActive, pendingResponse, runtimePreviewRows, tuiConfig.composer.showTopRule])

  const managedViewportRowsAboveCursor = useMemo(() => {
    if (!SCROLLBACK_MODE && !ownedSceneMode) return 0
    const rows = managedBodyRows + composerRowsAboveCursor
    if (SCROLLBACK_MODE) return Math.min(Math.max(1, rowCount), rows)
    return rows
  }, [ownedSceneMode, SCROLLBACK_MODE, composerRowsAboveCursor, managedBodyRows, rowCount])

  const activeBodyMinRows = useMemo(() => {
    // Preserved scrollback must append naturally. A full-height minimum turns
    // the main buffer into a bottom-aligned viewport and creates the large
    // empty gulf seen in manual QC.
    if (!SCROLLBACK_MODE) return 0
    return 0
  }, [SCROLLBACK_MODE])

  const managedViewportRunStateEpoch = useMemo(() => {
    if (SCROLLBACK_MODE) {
      if (pendingResponse) return "active"
      return [
        "idle",
        transcriptTail.length > 0 ? "tail" : "settled",
        transcriptViewerOpen ? "viewer" : "scene",
        overlayActive ? "overlay" : "base",
        overlayActive ? `model:${modelMenu.status}` : "model:hidden",
        status,
        stats.lastTurn ?? 0,
        transcriptCommitted.length,
        disconnected ? "disconnected" : "connected",
      ].join("|")
    }
    if (!ownedSceneMode) return null
    return [
      pendingResponse ? "active" : "idle",
      transcriptTail.length > 0 ? "tail" : "settled",
      transcriptViewerOpen ? "viewer" : "scene",
      status,
      stats.lastTurn ?? 0,
      disconnected ? "disconnected" : "connected",
    ].join("|")
  }, [disconnected, modelMenu.status, ownedSceneMode, overlayActive, pendingResponse, SCROLLBACK_MODE, stats.lastTurn, status, transcriptCommitted.length, transcriptTail.length, transcriptViewerOpen])

  const composerResetToken = useMemo(() => {
    if (!SCROLLBACK_MODE || pendingResponse || overlayActive || filePickerActive) return "none"
    if (suggestions.length > 0) return "slash-active"
    const value = String(context.input ?? "")
    if (value.length === 0) return "empty"
    const lineCount = value.split("\n").length
    return lineCount > 1 ? `multi:${Math.min(4, lineCount)}` : "single"
  }, [SCROLLBACK_MODE, context.input, filePickerActive, overlayActive, pendingResponse, suggestions.length])

  const managedViewportResetKey = useMemo(
    () =>
      buildManagedViewportResetKey({
        sessionId: context.sessionId,
        viewClearAt: context.viewClearAt,
        networkBannerTone: SCROLLBACK_MODE ? null : networkBanner?.tone ?? null,
        hasGuardrailNotice: Boolean(bodyGuardrailNotice),
        runStateEpoch: managedViewportRunStateEpoch,
        composerResetToken,
      }),
    [SCROLLBACK_MODE, composerResetToken, context.sessionId, context.viewClearAt, guardrailNotice, managedViewportRunStateEpoch, networkBanner],
  )

  const lastViewportResetLogRef = useRef<string | null>(null)
  const lastSurfaceModelLogRef = useRef<string | null>(null)
  const lastRenderGeometryLogRef = useRef<string | null>(null)
  const lastRenderCommitLogRef = useRef<string | null>(null)
  const lastManagedRegionBoundsLogRef = useRef<string | null>(null)
  const lastAppStartAnchorSessionRef = useRef<string | null>(null)


  useEffect(() => {
    if (!SCROLLBACK_MODE) return
    if (lastAppStartAnchorSessionRef.current === context.sessionId) return
    lastAppStartAnchorSessionRef.current = context.sessionId
    writeAppStartAnchorDebugRecord({
      event: "app_start_anchor",
      sessionId: context.sessionId,
      mode: "preserved-scrollback",
      zoneModel: "three-zone",
      preAppHistoryPolicy: "untouched",
      activeManagedRegionPolicy: "line-bounded-local-repaint",
      coldCommittedHistoryPolicy: "durable-not-universally-relayouted",
      liveShellOwnershipMode,
      viewClearAt: context.viewClearAt ?? null,
      landingAlways: context.landingAlways,
      landingLifecycleState: landingLifecycle.state,
      landingLifecycleReason: landingLifecycle.reason,
    })
  }, [SCROLLBACK_MODE, context.landingAlways, context.sessionId, context.viewClearAt, landingLifecycle.reason, landingLifecycle.state, liveShellOwnershipMode])

  useEffect(() => {
    if (!SCROLLBACK_MODE) return
    if (lastViewportResetLogRef.current === managedViewportResetKey) return
    lastViewportResetLogRef.current = managedViewportResetKey
    writeViewportResetDebugRecord({
      event: "managed_viewport_reset_key",
      sessionId: context.sessionId,
      resetKey: managedViewportResetKey,
      viewClearAt: context.viewClearAt ?? null,
      networkBannerTone: SCROLLBACK_MODE ? null : networkBanner?.tone ?? null,
      hasGuardrailNotice: Boolean(bodyGuardrailNotice),
      pendingResponse,
      landingRetired,
      landingAlways: context.landingAlways,
      transcriptViewerOpen,
    })
  }, [
    SCROLLBACK_MODE,
    context.landingAlways,
    context.sessionId,
    context.viewClearAt,
    guardrailNotice,
    landingRetired,
    managedViewportResetKey,
    networkBanner,
    pendingResponse,
    mainFollowTail,
    transcriptViewerOpen,
  ])

  useEffect(() => {
    if (!SCROLLBACK_MODE) return
    const geometry = {
      event: "render_geometry",
      sessionId: context.sessionId,
      rowCount,
      contentWidth,
      bodyBudgetRows,
      managedBodyRows,
      managedViewportRowsAboveCursor,
      landingVariant: emittedLandingVariant,
      landingRowCount: emittedLandingRowCount,
    historyLandingRowCount,
    }
    const signature = JSON.stringify(geometry)
    if (lastRenderGeometryLogRef.current === signature) return
    lastRenderGeometryLogRef.current = signature
    writeRenderTimelineDebugRecord(geometry)
  }, [
    SCROLLBACK_MODE,
    bodyBudgetRows,
    contentWidth,
    context.sessionId,
    emittedLandingRowCount,
    emittedLandingVariant,
    historyLandingRowCount,
    managedBodyRows,
    managedViewportRowsAboveCursor,
    rowCount,
  ])


  useEffect(() => {
    if (!SCROLLBACK_MODE) return
    const bounds = {
      event: "managed_region_bounds",
      sessionId: context.sessionId,
      mode: "preserved-scrollback",
      rowCount,
      contentWidth,
      managedViewportRowsAboveCursor,
      managedBodyRows,
      composerRowsAboveCursor,
      activeManagedRegionTopOffset: -Math.max(0, managedViewportRowsAboveCursor - 1),
      activeManagedRegionBottomOffset: 0,
      landingVariant: emittedLandingVariant,
      landingRowCount: emittedLandingRowCount,
      historyLandingRowCount,
      landingRetired,
      landingLifecycleState: landingLifecycle.state,
      landingLifecycleReason: landingLifecycle.reason,
      landingLifecycleVisibleInline: landingLifecycle.visibleInline,
      landingLifecycleCommittedSnapshot: landingLifecycle.committedSnapshot,
      warmLandingVisible: scrollbackSurfaceModel.warmLandingVisible,
      transcriptBudgetRows: scrollbackSurfaceModel.transcriptBudgetRows,
      activeWindowUsedLines: scrollbackSurfaceModel.activeWindow.usedLines,
      activeWindowCount: scrollbackSurfaceModel.activeWindow.items.length,
      coldCommittedCount: scrollbackSurfaceModel.coldCommitted.length,
      warmCommittedCount: scrollbackSurfaceModel.warmCommitted.length,
      appendLandingToFeed,
      appendHeaderToFeed,
    }
    const signature = JSON.stringify(bounds)
    if (lastManagedRegionBoundsLogRef.current === signature) return
    lastManagedRegionBoundsLogRef.current = signature
    writeManagedRegionBoundsDebugRecord(bounds)
  }, [
    SCROLLBACK_MODE,
    appendHeaderToFeed,
    appendLandingToFeed,
    composerRowsAboveCursor,
    contentWidth,
    context.sessionId,
    emittedLandingRowCount,
    emittedLandingVariant,
    historyLandingRowCount,
    landingRetired,
    landingLifecycle,
    managedBodyRows,
    managedViewportRowsAboveCursor,
    rowCount,
    scrollbackSurfaceModel,
  ])

  useEffect(() => {
    if (!SCROLLBACK_MODE) return
    const activeWindowOvershoot = scrollbackSurfaceModel.activeWindow.usedLines > scrollbackSurfaceModel.transcriptBudgetRows
    const summary = {
      event: "surface_model",
      sessionId: context.sessionId,
      pendingResponse,
      mainFollowTail,
      landingAlways: context.landingAlways,
      landingVariant: emittedLandingVariant,
      landingRowCount: emittedLandingRowCount,
    historyLandingRowCount,
      landingRetired,
      landingLifecycleState: landingLifecycle.state,
      landingLifecycleReason: landingLifecycle.reason,
      landingLifecycleVisibleInline: landingLifecycle.visibleInline,
      landingLifecycleCommittedSnapshot: landingLifecycle.committedSnapshot,
      landingEverVisibleInline,
      landingShouldRetireNow,
      landingShouldRetireForSettledHistory,
      landingShouldFreezeToHistory,
      preserveLatestSettledTurn,
      warmLandingVisible: scrollbackSurfaceModel.warmLandingVisible,
      showSessionHeaderInline,
      sessionHeaderFitsActiveRegion,
      activeWindowRenderedRowsEstimate,
      transcriptBudgetRows: scrollbackSurfaceModel.transcriptBudgetRows,
      coldCommittedCount: scrollbackSurfaceModel.coldCommitted.length,
      warmCommittedCount: scrollbackSurfaceModel.warmCommitted.length,
      activeWindowCount: scrollbackSurfaceModel.activeWindow.items.length,
      activeWindowUsedLines: scrollbackSurfaceModel.activeWindow.usedLines,
      activeWindowHiddenCount: scrollbackSurfaceModel.activeWindow.hiddenCount,
      activeWindowTruncated: scrollbackSurfaceModel.activeWindow.truncated,
      activeWindowOvershoot,
      bodyBudgetRows,
      managedBodyRows,
      managedViewportRowsAboveCursor,
      appendLandingToFeed,
      appendHeaderToFeed,
      transcriptCommittedCount: transcriptCommitted.length,
      transcriptTailCount: transcriptTail.length,
    }
    const signature = JSON.stringify(summary)
    if (lastSurfaceModelLogRef.current === signature) return
    lastSurfaceModelLogRef.current = signature
    writeSurfaceModelDebugRecord(summary)
    const commitEvent = {
      event: "render_commit",
      sessionId: context.sessionId,
      pendingResponse,
      landingRetired,
      landingLifecycleState: landingLifecycle.state,
      landingLifecycleReason: landingLifecycle.reason,
      warmLandingVisible: scrollbackSurfaceModel.warmLandingVisible,
      showSessionHeaderInline,
      landingVariant: emittedLandingVariant,
      transcriptBudgetRows: scrollbackSurfaceModel.transcriptBudgetRows,
      activeWindowCount: scrollbackSurfaceModel.activeWindow.items.length,
      activeWindowUsedLines: scrollbackSurfaceModel.activeWindow.usedLines,
      activeWindowHiddenCount: scrollbackSurfaceModel.activeWindow.hiddenCount,
      activeWindowTruncated: scrollbackSurfaceModel.activeWindow.truncated,
      activeWindowOvershoot,
      appendLandingToFeed,
      appendHeaderToFeed,
      transcriptCommittedCount: transcriptCommitted.length,
      transcriptTailCount: transcriptTail.length,
    }
    const commitSignature = JSON.stringify(commitEvent)
    if (lastRenderCommitLogRef.current === commitSignature) return
    lastRenderCommitLogRef.current = commitSignature
    writeRenderTimelineDebugRecord(commitEvent)
  }, [
    SCROLLBACK_MODE,
    appendHeaderToFeed,
    appendLandingToFeed,
    bodyBudgetRows,
    context.landingAlways,
    context.sessionId,
    emittedLandingRowCount,
    emittedLandingVariant,
    landingEverVisibleInline,
    landingLifecycle,
    landingRetired,
    historyLandingRowCount,
    landingShouldFreezeToHistory,
    landingShouldRetireForSettledHistory,
    landingShouldRetireNow,
    managedBodyRows,
    preserveLatestSettledTurn,
    managedViewportRowsAboveCursor,
    pendingResponse,
    mainFollowTail,
    scrollbackSurfaceModel,
    transcriptCommitted.length,
    transcriptTail.length,
  ])

  return {
    visibleModels,
    visibleModelRows,
    headerLines,
    landingNode,
    sessionHeaderNode,
    showSessionHeaderInline,
    warmLandingVisible,
    usageSummary,
    modeBadge,
    permissionBadge,
    headerSubtitleLines,
    promptRule,
    pendingClaudeStatus,
    networkBanner: bodyNetworkBanner,
    compactMode,
    headerReserveRows,
    guardrailReserveRows,
    composerReserveRows,
    composerRowsAboveCursor,
    managedViewportRowsAboveCursor,
    managedViewportResetKey,
    overlayReserveRows,
    bodyBudgetRows,
    activeBodyMinRows,
    statusGlyph,
    modelGlyph,
    remoteGlyph,
    toolsGlyph,
    eventsGlyph,
    turnGlyph,
    transcriptMemoryBoundsSummary: boundedTranscript.summary,
    staticFeed,
    conversationWindow,
    collapsibleEntries,
    collapsibleMeta,
    toggleCollapsedEntry,
    cycleCollapsibleSelection,
    toggleSelectedCollapsibleEntry,
    renderConversationEntry,
    renderToolEntry,
    ...renderNodes,
  }
}
