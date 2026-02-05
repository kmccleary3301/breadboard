import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { useStdout } from "ink"
import type { Block, InlineNode } from "@stream-mdx/core/types"
import type {
  ConversationEntry,
  LiveSlotEntry,
  StreamStats,
  ModelMenuState,
  SkillsMenuState,
  InspectMenuState,
  GuardrailNotice,
  QueuedAttachment,
  TranscriptPreferences,
  ToolLogEntry,
  TodoItem,
  TaskEntry,
  SkillEntry,
  PermissionRequest,
  PermissionDecision,
  PermissionRuleScope,
  RewindMenuState,
  CheckpointSummary,
  CTreeSnapshot,
} from "../../../types.js"
import type { ReplViewProps } from "../replViewTypes.js"
import type { SlashCommandInfo, SlashSuggestion } from "../../../slashCommands.js"
import { ASCII_HEADER } from "../../../viewUtils.js"
import { STATUS_VERBS } from "../../../designSystem.js"
import { type KeyHandler } from "../../../hooks/useKeyRouter.js"
import type { SessionFileInfo, SessionFileContent, CTreeTreeNode, CTreeTreeResponse } from "../../../../api/types.js"
import { loadFileMentionConfig, type FileMentionMode } from "../../../fileMentions.js"
import { loadFilePickerConfig, loadFilePickerResources, type FilePickerMode, type FilePickerResource } from "../../../filePicker.js"
import { loadKeymapConfig } from "../../../keymap.js"
import { loadProfileConfig } from "../../../profile.js"
import { loadChromeMode } from "../../../chrome.js"
import { ensureShikiLoaded, maybeHighlightCode, subscribeShiki } from "../../../shikiHighlighter.js"
import { getSessionDraft, updateSessionDraft } from "../../../../cache/sessionCache.js"
import { buildConversationWindow, MAX_TRANSCRIPT_ENTRIES, MIN_TRANSCRIPT_ROWS } from "../../../transcriptUtils.js"
import { CLI_VERSION, COLOR_MODE, DELTA_GLYPH, DOT_SEPARATOR, uiText } from "../theme.js"
import {
  formatBytes,
  formatDuration,
  formatTokenCount,
  truncateLine,
} from "../utils/format.js"
import {
  clearToEnd,
  hashString,
  makeSnippet,
  measureBytes,
  normalizeNewlines,
  normalizeSessionPath,
  stripCommandQuotes,
} from "../utils/text.js"
import { clampCommandLines, formatListCommandLines } from "../features/filePicker/atCommands.js"
import { formatSizeDetail } from "../utils/files.js"
import type { TranscriptMatch } from "../types.js"
import { useReplLayout } from "../layout/useReplLayout.js"
import { formatIsoTimestamp } from "../utils/diff.js"
import { stripAnsiCodes } from "../utils/ansi.js"
import { computeInputLocked, computeOverlayActive } from "../keybindings/overlayState.js"
import { useReplKeyRouter } from "../keybindings/useReplKeyRouter.js"
import { matchCtrl, runKeymap } from "../keybindings/keymaps.js"
import { useModalController } from "../features/modals/useModalController.js"
import { buildModalStack } from "../overlays/buildModalStack.js"
import { useGlobalKeys } from "./keyHandlers/useGlobalKeys.js"
import { useReplViewPanels } from "./useReplViewPanels.js"
import { useReplViewMenus } from "./useReplViewMenus.js"
import { useReplViewInputHandlers } from "./useReplViewInputHandlers.js"
import { useReplViewScrollback } from "./useReplViewScrollback.js"
import {
  ALWAYS_ALLOW_SCOPE,
  BOX_CHARS,
  DOUBLE_CTRL_C_WINDOW_MS,
  MODEL_PROVIDER_ORDER,
  SKILL_GROUP_ORDER,
  buildModelRowText,
  buildSkillKey,
  centerPlain,
  colorCentered,
  colorLine,
  formatContextCell,
  formatCtreeSummary,
  formatPriceCell,
  formatProfileLabel,
  formatProviderCell,
  formatProviderLabel,
  isSkillSelected,
  normalizeModeLabel,
  normalizePermissionLabel,
  normalizeProviderKey,
  resolveGreetingName,
} from "./replViewControllerUtils.js"
import { useReplViewModalStack } from "./useReplViewModalStack.js"
import { ReplViewBaseContent } from "./ReplViewBaseContent.js"

const META_LINE_COUNT = 2
const COMPOSER_MIN_ROWS = 6
const TOOL_COLLAPSE_THRESHOLD = 24
const TOOL_COLLAPSE_HEAD = 6
const TOOL_COLLAPSE_TAIL = 6
const TOOL_LABEL_WIDTH = 12
const LABEL_WIDTH = 9
const parseBoolEnv = (value: string | undefined, fallback: boolean): boolean => {
  if (value == null) return fallback
  const normalized = value.trim().toLowerCase()
  if (!normalized) return fallback
  if (["1", "true", "yes", "on"].includes(normalized)) return true
  if (["0", "false", "no", "off"].includes(normalized)) return false
  return fallback
}

const resolveScrollbackMode = (): boolean => {
  const explicitMode = (process.env.BREADBOARD_TUI_MODE ?? "").trim().toLowerCase()
  if (explicitMode === "window") return false
  if (explicitMode === "scrollback") return true
  return parseBoolEnv(process.env.BREADBOARD_TUI_SCROLLBACK, true)
}

export const useReplViewController = ({
  configPath,
  sessionId,
  conversation: conversationProp,
  toolEvents: toolEventsProp,
  rawEvents,
  liveSlots,
  status,
  pendingResponse,
  disconnected,
  mode,
  permissionMode,
  hints,
  stats,
  modelMenu,
  skillsMenu,
  inspectMenu,
  guardrailNotice,
  viewClearAt,
  viewPrefs,
  todos,
  tasks,
  ctreeSnapshot,
  ctreeTree,
  ctreeTreeStatus,
  ctreeTreeError,
  ctreeStage,
  ctreeIncludePreviews,
  ctreeSource,
  ctreeUpdatedAt,
  permissionRequest,
  permissionError,
  permissionQueueDepth,
  rewindMenu,
  onSubmit,
  onModelMenuOpen,
  onModelSelect,
  onModelMenuCancel,
  onSkillsMenuOpen,
  onSkillsMenuCancel,
  onSkillsApply,
  onGuardrailToggle,
  onGuardrailDismiss,
  onPermissionDecision,
  onRewindClose,
  onRewindRestore,
  onListFiles,
  onReadFile,
  onCtreeRequest,
  onCtreeRefresh,
}: ReplViewProps) => {
  const SCROLLBACK_MODE = useMemo(() => resolveScrollbackMode(), [])
  const collapsedEntriesRef = useRef(new Map<string, boolean>())
  const [collapsedVersion, setCollapsedVersion] = useState(0)
  const [selectedCollapsibleEntryId, setSelectedCollapsibleEntryId] = useState<string | null>(null)
  const [pendingStartedAt, setPendingStartedAt] = useState<number | null>(null)
  const [lastDurationMs, setLastDurationMs] = useState<number | null>(null)
  const pendingStartedAtRef = useRef<number | null>(null)
  const {
    paletteState,
    setPaletteState,
    confirmState,
    openPalette,
    closePalette,
    openConfirm,
    closeConfirm,
    runConfirmAction,
    shortcutsOpen,
    setShortcutsOpen,
    shortcutsOpenedAtRef,
    usageOpen,
    setUsageOpen,
  } = useModalController()
  const [inspectRawOpen, setInspectRawOpen] = useState(false)
  const [inspectRawScroll, setInspectRawScroll] = useState(0)
  const [modelSearch, setModelSearch] = useState("")
  const [modelIndex, setModelIndex] = useState(0)
  const [modelOffset, setModelOffset] = useState(0)
  const [modelProviderFilter, setModelProviderFilter] = useState<string | null>(null)
  const [skillsSearch, setSkillsSearch] = useState("")
  const [skillsIndex, setSkillsIndex] = useState(0)
  const [skillsOffset, setSkillsOffset] = useState(0)
  const [skillsMode, setSkillsMode] = useState<"allowlist" | "blocklist">("blocklist")
  const [skillsSelected, setSkillsSelected] = useState<Set<string>>(new Set())
  const [skillsDirty, setSkillsDirty] = useState(false)
  const [permissionTab, setPermissionTab] = useState<"summary" | "diff" | "rules" | "note">("summary")
  const [permissionScope, setPermissionScope] = useState<PermissionRuleScope>("project")
  const [permissionFileIndex, setPermissionFileIndex] = useState(0)
  const [permissionScroll, setPermissionScroll] = useState(0)
  const [permissionNote, setPermissionNote] = useState("")
  const [permissionNoteCursor, setPermissionNoteCursor] = useState(0)
  const permissionTabRef = useRef(permissionTab)
  const permissionInputSnapshotRef = useRef<{ value: string; cursor: number } | null>(null)
  const permissionActiveRef = useRef(false)
  const permissionNoteRef = useRef(permissionNote)
  const permissionDecisionTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const [todosOpen, setTodosOpen] = useState(false)
  const [todoScroll, setTodoScroll] = useState(0)
  const [tasksOpen, setTasksOpen] = useState(false)
  const [taskScroll, setTaskScroll] = useState(0)
  const [taskIndex, setTaskIndex] = useState(0)
  const [taskNotice, setTaskNotice] = useState<string | null>(null)
  const [taskSearchQuery, setTaskSearchQuery] = useState("")
  const [taskStatusFilter, setTaskStatusFilter] = useState<"all" | "running" | "completed" | "failed">("all")
  const [taskTailLines, setTaskTailLines] = useState<string[]>([])
  const [taskTailPath, setTaskTailPath] = useState<string | null>(null)
  const [ctreeOpen, setCtreeOpen] = useState(false)
  const [ctreeScroll, setCtreeScroll] = useState(0)
  const [ctreeIndex, setCtreeIndex] = useState(0)
  const [ctreeCollapsedNodes, setCtreeCollapsedNodes] = useState<Set<string>>(new Set())
  const [ctreeShowDetails, setCtreeShowDetails] = useState(false)
  const [rewindIndex, setRewindIndex] = useState(0)
  const [escPrimedAt, setEscPrimedAt] = useState<number | null>(null)
  const [ctrlCPrimedAt, setCtrlCPrimedAt] = useState<number | null>(null)
  const [verboseOutput, setVerboseOutput] = useState(false)
  const [transcriptViewerOpen, setTranscriptViewerOpen] = useState(false)
  const [transcriptViewerScroll, setTranscriptViewerScroll] = useState(0)
  const [transcriptViewerFollowTail, setTranscriptViewerFollowTail] = useState(true)
  const [transcriptSearchQuery, setTranscriptSearchQuery] = useState("")
  const [transcriptSearchOpen, setTranscriptSearchOpen] = useState(false)
  const [transcriptSearchIndex, setTranscriptSearchIndex] = useState(0)
  const [transcriptExportNotice, setTranscriptExportNotice] = useState<string | null>(null)
  const [transcriptToolIndex, setTranscriptToolIndex] = useState(0)
  const [transcriptNudge, setTranscriptNudge] = useState(0)
  const draftAppliedRef = useRef(false)
  const draftLoadSeq = useRef(0)
  const draftSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastSavedDraftRef = useRef<{ text: string; cursor: number } | null>(null)
  const { stdout } = useStdout()
  const [, forceShikiRefresh] = useState(0)
  const pushCommandResultRef = useRef<(title: string, lines: string[]) => void>(() => {})
  const pushCommandResult = useCallback((title: string, lines: string[]) => {
    pushCommandResultRef.current(title, lines)
  }, [])
  const renderConversationEntryRef = useRef<(entry: ConversationEntry, key?: string) => React.ReactNode>(() => null)
  const renderToolEntryRef = useRef<(entry: ToolLogEntry, key?: string) => React.ReactNode>(() => null)
  const renderConversationEntryForFeed = useCallback(
    (entry: ConversationEntry, key?: string) => renderConversationEntryRef.current(entry, key),
    [],
  )
  const renderToolEntryForFeed = useCallback(
    (entry: ToolLogEntry, key?: string) => renderToolEntryRef.current(entry, key),
    [],
  )
  const conversation = useMemo(() => {
    const filtered = conversationProp.filter((entry) => {
      if (!entry || typeof entry.text !== "string") return true
      const trimmed = entry.text.trim().toLowerCase()
      if (entry.speaker === "assistant" && trimmed === "none") return false
      return true
    })
    if (!viewClearAt) return filtered
    return filtered.filter((entry) => entry.createdAt >= viewClearAt)
  }, [conversationProp, viewClearAt])
  const toolEvents = useMemo(() => {
    const base = viewPrefs.rawStream ? [...toolEventsProp, ...rawEvents] : toolEventsProp
    const filteredBase = base.filter((entry) => {
      const text = entry.text?.trim()
      if (!text) return false
      const lowered = text.toLowerCase()
      if (lowered === "tool" || lowered === "toolz" || lowered === "none") return false
      return true
    })
    const deduped: typeof filteredBase = []
    for (const entry of filteredBase) {
      const lines = entry.text.split(/\r?\n/)
      const header = (lines[0] ?? "").trim()
      const last = deduped[deduped.length - 1]
      if (last) {
        const lastHeader = (last.text.split(/\r?\n/)[0] ?? "").trim()
        const sameTool =
          last.id === entry.id || (last.callId && entry.callId && last.callId === entry.callId)
        const exactDuplicate =
          last.kind === entry.kind && last.status === entry.status && last.text === entry.text
        if (exactDuplicate) {
          continue
        }
        if (sameTool) {
          const lastPending = last.kind === "call" || last.status === "pending"
          const entryPending = entry.kind === "call" || entry.status === "pending"
          if ((!entryPending && lastPending) || entry.text.length >= last.text.length) {
            deduped[deduped.length - 1] = entry
            continue
          }
        } else if (lastHeader && lastHeader === header) {
          const lastPending = last.kind === "call" || last.status === "pending"
          const timeDelta = Math.abs(entry.createdAt - last.createdAt)
          if (lastPending && timeDelta < 1500 && entry.text.length >= last.text.length) {
            deduped[deduped.length - 1] = entry
            continue
          }
        }
      }
      deduped.push(entry)
    }
    if (!viewClearAt) return deduped
    return deduped.filter((entry) => entry.createdAt >= viewClearAt)
  }, [rawEvents, toolEventsProp, viewClearAt, viewPrefs.rawStream])
  const fileMentionConfig = useMemo(() => loadFileMentionConfig(), [])
  const filePickerConfig = useMemo(() => loadFilePickerConfig(), [])
  const keymap = useMemo(() => loadKeymapConfig(), [])
  const chromeMode = useMemo(() => loadChromeMode(keymap), [keymap])
  const profile = useMemo(() => loadProfileConfig(), [])
  const isBreadboardProfile = profile.name === "breadboard_v1"
  const claudeChrome = chromeMode === "claude"
  const filePickerResources = useMemo(() => loadFilePickerResources(), [])
  const [, forceRedraw] = useState(0)
  useEffect(() => {
    const unsubscribe = subscribeShiki(() => {
      forceShikiRefresh((value) => value + 1)
    })
    ensureShikiLoaded()
    return unsubscribe
  }, [forceShikiRefresh])
  const fixedFrameWidthRaw = (process.env.BREADBOARD_TUI_FRAME_WIDTH ?? "").toString().trim()
  const fixedFrameWidth = fixedFrameWidthRaw ? Number(fixedFrameWidthRaw) : NaN
  const resolvedColumns =
    stdout?.columns && Number.isFinite(stdout.columns) && stdout.columns > 0 ? stdout.columns : null
  const columnWidth =
    Number.isFinite(fixedFrameWidth) && fixedFrameWidth > 0
      ? resolvedColumns
        ? Math.min(fixedFrameWidth, resolvedColumns)
        : fixedFrameWidth
      : resolvedColumns ?? 80
  const contentWidth = useMemo(
    () => Math.max(10, columnWidth - (claudeChrome ? 0 : 2)),
    [claudeChrome, columnWidth],
  )
  const rowCount = stdout?.rows && Number.isFinite(stdout.rows) ? stdout.rows : 40
  useEffect(() => {
    if (inspectMenu.status === "hidden") {
      setInspectRawOpen(false)
      setInspectRawScroll(0)
    }
  }, [inspectMenu.status])
  useEffect(() => {
    if (pendingResponse) {
      if (pendingStartedAtRef.current == null) {
        pendingStartedAtRef.current = Date.now()
      }
      return
    }
    if (pendingStartedAtRef.current != null) {
      const duration = Date.now() - pendingStartedAtRef.current
      pendingStartedAtRef.current = null
      setLastDurationMs(duration)
    }
  }, [pendingResponse])
  const inspectRawViewportRows = useMemo(() => Math.max(10, Math.min(24, Math.floor(rowCount * 0.6))), [rowCount])
  const panels = useReplViewPanels({
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
    taskIndex,
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
  })
  const {
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
    taskRows,
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
  } = panels
  const skillsSelection = skillsMenu.status === "ready" ? skillsMenu.selection : null
  const menus = useReplViewMenus({
    skillsMenu,
    skillsSearch,
    skillsIndex,
    skillsOffset,
    setSkillsIndex,
    setSkillsOffset,
    setSkillsSearch,
    skillsMode,
    setSkillsMode,
    skillsSelected,
    setSkillsSelected,
    skillsDirty,
    setSkillsDirty,
    skillsSelection,
    onSkillsApply,
    onSkillsMenuCancel,
    SKILL_GROUP_ORDER,
    buildSkillKey,
    skillsVisibleRows: SKILLS_VISIBLE_ROWS,
    modelMenu,
    modelSearch,
    modelProviderFilter,
    setModelSearch,
    setModelIndex,
    setModelOffset,
    setModelProviderFilter,
    normalizeProviderKey,
    formatProviderLabel,
    MODEL_VISIBLE_ROWS,
    permissionRequest,
    setPermissionScope,
    ALWAYS_ALLOW_SCOPE,
    setPermissionScroll,
    setPermissionFileIndex,
    setPermissionNote,
    setPermissionNoteCursor,
    permissionTabRef,
    setPermissionTab,
    permissionTab,
    permissionNoteRef,
    permissionDecisionTimerRef,
    tasksOpen,
    setTaskIndex,
    setTaskScroll,
    setTaskNotice,
    setTaskSearchQuery,
    setTaskStatusFilter,
    setTaskTailLines,
    setTaskTailPath,
    taskRows,
    taskMaxScroll,
    ctreeOpen,
    setCtreeIndex,
    setCtreeScroll,
    setCtreeShowDetails,
    setCtreeCollapsedNodes,
    ctreeRows,
    ctreeMaxScroll,
    onCtreeRequest,
    sessionId,
    todosOpen,
    setTodoScroll,
    todos,
    rewindMenu,
    setRewindIndex,
    input,
    suggestions,
    setSuggestIndex,
    escPrimedAt,
    setEscPrimedAt,
    ctrlCPrimedAt,
    setCtrlCPrimedAt,
    DOUBLE_CTRL_C_WINDOW_MS,
    stdout,
    transcriptViewerOpen,
    setTranscriptViewerOpen,
    setTranscriptViewerFollowTail,
    setTranscriptViewerScroll,
    setTranscriptSearchOpen,
    setTranscriptSearchQuery,
    setTranscriptSearchIndex,
    setTranscriptExportNotice,
    transcriptViewerLines,
    pushCommandResult,
    verboseOutput,
    permissionNote,
    MODEL_PROVIDER_ORDER,
  })
  const {
    skillsCatalog,
    skillsSources,
    skillsData,
    skillsSelectedEntry,
    skillsDisplayRows,
    skillsDisplayIndex,
    resetSkillsSelection,
    toggleSkillsMode,
    toggleSkillSelection,
    applySkillsSelection,
    filteredModels,
    modelProviderOrder,
    modelProviderLabel,
    modelProviderCounts,
    enterTranscriptViewer,
    exitTranscriptViewer,
    saveTranscriptExport,
  } = menus
  const {
    filePickerController,
    applySuggestion,
    applyPaletteItem,
    handleAtCommand,
    handleLineSubmit,
    clearScreen,
    handleOverlayKeys,
    handleEditorKeys,
    handlePaletteKeys,
  } = useReplViewInputHandlers({
    configPath,
    input,
    cursor,
    inputLocked,
    inputTextVersion,
    claudeChrome,
    rowCount,
    filePickerConfig,
    filePickerResources,
    fileMentionConfig,
    onListFiles,
    handleLineEdit,
    onReadFile,
    pushCommandResult,
    setSuggestIndex,
    setSuppressSuggestions,
    attachments,
    onSubmit,
    setTodosOpen,
    setUsageOpen,
    setTasksOpen,
    enterTranscriptViewer,
    closePalette,
    closeConfirm,
    confirmState,
    ctrlCPrimedAt,
    ctreeCollapsibleIds,
    ctreeIncludePreviews,
    ctreeMaxScroll,
    ctreeOpen,
    ctreeRows,
    ctreeScroll,
    ctreeSource,
    ctreeStage,
    ctreeViewportRows,
    doubleCtrlWindowMs: DOUBLE_CTRL_C_WINDOW_MS,
    escPrimedAt,
    exitTranscriptViewer,
    filteredModels,
    inputValueRef,
    inspectMenu,
    inspectRawMaxScroll,
    inspectRawOpen,
    inspectRawViewportRows,
    jumpTranscriptToLine,
    keymap,
    modelIndex,
    modelMenu,
    modelProviderFilter,
    modelProviderOrder,
    modelSearch,
    modelVisibleRows: MODEL_VISIBLE_ROWS,
    normalizeProviderKey,
    onCtreeRefresh,
    onModelMenuCancel,
    onModelSelect,
    onPermissionDecision,
    onRewindClose,
    onRewindRestore,
    onSkillsMenuCancel,
    onSkillsMenuOpen,
    pendingResponse,
    permissionDecisionTimerRef,
    permissionDiffSections,
    permissionInputSnapshotRef,
    permissionNote,
    permissionNoteCursor,
    permissionNoteRef,
    permissionRequest,
    permissionScope,
    permissionTab,
    permissionTabRef,
    permissionViewportRows,
    resetSkillsSelection,
    rewindIndex,
    rewindMenu,
    rewindVisibleLimit,
    runConfirmAction,
    saveTranscriptExport,
    setCtreeCollapsedNodes,
    setCtreeIndex,
    setCtreeOpen,
    setCtreeScroll,
    setCtreeShowDetails,
    setCtrlCPrimedAt,
    setEscPrimedAt,
    setInspectRawOpen,
    setInspectRawScroll,
    setModelIndex,
    setModelOffset,
    setModelProviderFilter,
    setModelSearch,
    setPermissionFileIndex,
    setPermissionNote,
    setPermissionNoteCursor,
    setPermissionScroll,
    setPermissionTab,
    setRewindIndex,
    setShortcutsOpen,
    setSkillsIndex,
    setSkillsOffset,
    setSkillsSearch,
    setTaskIndex,
    setTaskScroll,
    setTaskSearchQuery,
    setTaskStatusFilter,
    setTodoScroll,
    setTranscriptSearchIndex,
    setTranscriptSearchOpen,
    setTranscriptSearchQuery,
    setTranscriptToolIndex,
    setTranscriptViewerFollowTail,
    setTranscriptViewerScroll,
    shortcutsOpen,
    shortcutsOpenedAtRef,
    skillsData,
    skillsMenu,
    skillsSearch,
    skillsSelectedEntry,
    skillsVisibleRows: SKILLS_VISIBLE_ROWS,
    stdout,
    taskMaxScroll,
    taskRows,
    taskScroll,
    taskSearchQuery,
    taskStatusFilter,
    taskViewportRows,
    tasksOpen,
    todoMaxScroll,
    todoViewportRows,
    todosOpen,
    toggleSkillsMode,
    toggleSkillSelection,
    transcriptSearchMatches,
    transcriptSearchOpen,
    transcriptToolLines,
    transcriptViewerBodyRows,
    transcriptViewerEffectiveScroll,
    transcriptViewerFollowTail,
    transcriptViewerMaxScroll,
    transcriptViewerOpen,
    selectedCTreeIndex,
    selectedCTreeRow,
    selectedTaskIndex,
    requestTaskTail,
    usageOpen,
    suggestIndex,
    suggestions,
    removeLastAttachment,
    pushHistoryEntry,
    recallHistory,
    moveCursorVertical,
    applySkillsSelection,
    forceRedraw,
    sessionId,
    draftLoadSeq,
    draftAppliedRef,
    draftSaveTimerRef,
    lastSavedDraftRef,
    permissionActiveRef,
    paletteItems,
    paletteState,
    setPaletteState,
  })

  const {
    fileMentions,
    setFileMentions,
    filePicker,
    setFilePicker,
    filePickerIndexRef,
    fileMenuRowsRef,
    filePickerDismissed,
    setFilePickerDismissed,
    fileIndexMeta,
    fileMenuMaxRows,
    activeAtMention,
    filePickerQueryParts,
    rawFilePickerNeedle,
    filePickerActive,
    filePickerResourcesVisible,
    filePickerFilteredItems,
    filePickerIndex,
    fileIndexItems,
    fileMenuMode,
    fileMenuNeedle,
    fileMenuNeedlePending,
    fileMenuRows,
    fileMenuHasLarge,
    fileMenuIndex,
    selectedFileRow,
    selectedFileIsLarge,
    fileMenuWindow,
    closeFilePicker,
    ensureFileIndexScan,
    loadFilePickerDirectory,
    insertFileMention,
    insertDirectoryMention,
    insertResourceMention,
    queueFileMention,
  } = filePickerController
  const {
    visibleModelRows,
    headerLines,
    landingNode,
    usageSummary,
    modeBadge,
    permissionBadge,
    headerSubtitleLines,
    promptRule,
    pendingClaudeStatus,
    networkBanner,
    compactMode,
    statusGlyph,
    modelGlyph,
    remoteGlyph,
    toolsGlyph,
    eventsGlyph,
    turnGlyph,
    staticFeed,
    conversationWindow,
    collapsibleEntries,
    collapsibleMeta,
    toggleCollapsedEntry,
    cycleCollapsibleSelection,
    toggleSelectedCollapsibleEntry,
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
  } = useReplViewScrollback({
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
    attachments,
    fileMentions,
    transcriptViewerOpen,
    transcriptNudge,
    completionHint:
      claudeChrome && lastDurationMs != null ? `✻ Cooked for ${formatDuration(lastDurationMs)}` : null,
    renderConversationEntryForFeed,
    renderToolEntryForFeed,
    renderConversationEntryRef,
    renderToolEntryRef,
    pushCommandResultRef,
    conversation,
    toolEvents,
    SCROLLBACK_MODE,
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
    sessionId,
    viewClearAt,
    verboseOutput,
    TOOL_COLLAPSE_THRESHOLD,
    TOOL_COLLAPSE_HEAD,
    TOOL_COLLAPSE_TAIL,
    TOOL_LABEL_WIDTH,
    LABEL_WIDTH,
    normalizeModeLabel,
    normalizePermissionLabel,
    CLI_VERSION,
    shortcutsOpen,
    ctrlCPrimedAt,
    escPrimedAt,
  })

  const handleGlobalKeys = useGlobalKeys({
    clearScreen,
    closePalette,
    confirmState,
    ctrlCPrimedAt,
    ctreeOpen,
    cycleCollapsibleSelection,
    doubleCtrlWindowMs: DOUBLE_CTRL_C_WINDOW_MS,
    enterTranscriptViewer,
    escPrimedAt,
    exitTranscriptViewer,
    guardrailNotice,
    handleLineEdit,
    keymap,
    modelMenu,
    onGuardrailDismiss,
    onGuardrailToggle,
    onModelMenuCancel,
    onModelMenuOpen,
    onRewindClose,
    onSkillsMenuCancel,
    onSkillsMenuOpen,
    onSubmit,
    openConfirm,
    openPalette,
    overlayActive,
    paletteState,
    pendingResponse,
    permissionRequest,
    pushCommandResult,
    rewindMenu,
    scrollbackMode: SCROLLBACK_MODE,
    setCtrlCPrimedAt,
    setCtreeOpen,
    setEscPrimedAt,
    setTasksOpen,
    setTodosOpen,
    setTranscriptNudge,
    setVerboseOutput,
    skillsMenu,
    toggleSelectedCollapsibleEntry,
    transcriptViewerBodyRows,
    transcriptViewerOpen,
  })

  useReplKeyRouter({
    overlayFlags,
    modal: handleOverlayKeys,
    palette: handlePaletteKeys,
    editor: handleEditorKeys,
    global: handleGlobalKeys,
  })

  const landingAlways = useMemo(() => {
    const raw = (process.env.BREADBOARD_TUI_LANDING_ALWAYS ?? "").trim().toLowerCase()
    return ["1", "true", "yes", "on"].includes(raw)
  }, [])

  const showLandingInline =
    SCROLLBACK_MODE &&
    !landingAlways &&
    staticFeed.length === 0 &&
    conversation.length === 0 &&
    toolEvents.length === 0

  const composerPanelContext = {
    claudeChrome, modelMenu, pendingClaudeStatus, promptRule, input, cursor, inputLocked,
    attachments, fileMentions, inputMaxVisibleLines, handleLineEditGuarded, handleLineSubmit, handleAttachment,
    overlayActive, filePickerActive, fileIndexMeta, fileMenuMode, filePicker, fileMenuRows, fileMenuHasLarge,
    fileMenuWindow, fileMenuIndex, fileMenuNeedlePending, filePickerQueryParts, filePickerConfig, fileMentionConfig,
    selectedFileIsLarge, columnWidth, suggestions, suggestionWindow, suggestionPrefix, suggestionLayout,
    buildSuggestionLines, suggestIndex, activeSlashQuery, hintNodes, shortcutHintNodes,
  }
  const baseContent = (
    <ReplViewBaseContent
      claudeChrome={claudeChrome}
      scrollbackMode={SCROLLBACK_MODE}
      sessionId={sessionId}
      statusGlyph={statusGlyph}
      status={status}
      modelGlyph={modelGlyph}
      stats={stats}
      remoteGlyph={remoteGlyph}
      eventsGlyph={eventsGlyph}
      toolsGlyph={toolsGlyph}
      modeBadge={modeBadge}
      permissionBadge={permissionBadge}
      turnGlyph={turnGlyph}
      usageSummary={usageSummary}
      metaNodes={metaNodes}
      guardrailNotice={guardrailNotice ?? null}
      networkBanner={networkBanner}
      showLandingInline={showLandingInline}
      landingNode={landingNode}
      transcriptNodes={transcriptNodes}
      toolNodes={toolNodes}
      overlayActive={overlayActive}
      liveSlotNodes={liveSlotNodes}
      collapsedHintNode={collapsedHintNode}
      virtualizationHintNode={virtualizationHintNode}
      composerPanelContext={composerPanelContext}
    />
  )

  const transcriptDetailLabel = useMemo(() => {
    const parts: string[] = []
    if (verboseOutput) {
      parts.push(uiText("Showing detailed transcript · Ctrl+O to toggle"))
    }
    if (transcriptExportNotice) {
      parts.push(transcriptExportNotice)
    }
    return parts.join(DOT_SEPARATOR)
  }, [transcriptExportNotice, verboseOutput])

  const modalStack = useReplViewModalStack({
    confirmState,
    shortcutsOpen,
    claudeChrome,
    isBreadboardProfile,
    columnWidth,
    contentWidth,
    sessionId,
    status: status ?? "",
    mode: mode ?? null,
    pendingResponse,
    stats,
    paletteState,
    paletteItems,
    shortcutLines,
    clearToEnd,
    modelMenu,
    modelMenuCompact,
    modelSearch,
    modelProviderLabel: modelProviderLabel ?? "",
    modelProviderFilter: modelProviderFilter ?? "",
    modelMenuHeaderText,
    filteredModels,
    visibleModelRows,
    modelIndex,
    modelOffset,
    formatModelRowText,
    skillsMenu,
    skillsSelected,
    skillsMode,
    skillsSearch,
    skillsIndex,
    skillsOffset,
    skillsSources,
    skillsDirty,
    rewindMenu,
    todosOpen,
    todoScroll,
    todos,
    usageOpen,
    inspectMenu,
    inspectRawOpen,
    inspectRawScroll,
    tasks,
    tasksOpen,
    taskScroll,
    taskSearchQuery,
    taskStatusFilter,
    permissionRequest,
    permissionQueueDepth: permissionQueueDepth ?? 0,
    permissionTab,
    permissionScope,
    permissionScroll,
    permissionError: permissionError ?? null,
    permissionNote: permissionNote ?? "",
    permissionNoteCursor: permissionNoteCursor ?? 0,
    renderPermissionNoteLine,
    ctreeOpen,
    ctreeScroll,
    ctreeStage,
    ctreeIncludePreviews,
    ctreeSource,
    ctreeTree,
    ctreeTreeStatus,
    ctreeTreeError: ctreeTreeError ?? null,
    ctreeUpdatedAt: ctreeUpdatedAt ?? null,
    ctreeCollapsedNodes,
    ctreeShowDetails,
    formatCtreeSummary: (value?: unknown) => formatCtreeSummary(value as CTreeSnapshot | null | undefined),
    ctreeSnapshot,
    PANEL_WIDTH,
    MAX_VISIBLE_MODELS,
    MODEL_VISIBLE_ROWS,
    SKILLS_VISIBLE_ROWS,
    isSkillSelected: (selected: Set<string>, skill: unknown) => isSkillSelected(selected, skill as SkillEntry),
    panels,
    menus,
  })
  return {
    scrollbackMode: SCROLLBACK_MODE,
    staticFeed,
    transcriptViewerOpen,
    transcriptViewerLines,
    columnWidth,
    rowCount,
    transcriptViewerEffectiveScroll,
    transcriptSearchOpen,
    transcriptSearchQuery,
    transcriptSearchLineMatches,
    transcriptSearchMatches,
    transcriptSearchSafeIndex,
    transcriptSearchActiveLine,
    transcriptDetailLabel,
    keymap,
    modalStack,
    baseContent,
  }
}

export type ReplViewController = ReturnType<typeof useReplViewController>
