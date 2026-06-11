import React, { useCallback, useEffect, useMemo, useRef, useState } from "react"
import { appendFileSync } from "node:fs"
import { useInput, useStdout } from "ink"
import { resolveActualStdoutRows } from "../../../inkScrollbackStdout.js"
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
  ThinkingArtifact,
  ThinkingPreviewState,
  ActivitySnapshot,
  ToolDisplayPayload,
} from "../../../types.js"
import type { RecentSessionRow, ReplViewProps } from "../replViewTypes.js"
import {
  buildBottomAnchoredClearSequence,
  buildLineRangeAboveActiveBandClearSequence,
} from "../terminalSequences.js"
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
import { configureShikiTheme, ensureShikiLoaded, maybeHighlightCode, subscribeShiki } from "../../../shikiHighlighter.js"
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
import type { TranscriptMessageItem } from "../../../transcriptModel.js"
import type { TranscriptToolTarget } from "./transcriptViewerModel.js"
import { useReplLayout } from "../layout/useReplLayout.js"
import { useTerminalSize } from "../../../hooks/useTerminalSize.js"
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
import { useOverlayKeys } from "./keyHandlers/useOverlayKeys.js"
import { useReplViewScrollback } from "./useReplViewScrollback.js"
import { shouldSuppressThinkingPreview } from "./streamingSurfacePolicy.js"
import { resolveScrollbackEnabled } from "../../../../config/frontendMode.js"

const traceKeyDiagnostic = (event: Record<string, unknown>) => {
  const target = process.env.BREADBOARD_TUI_KEY_TRACE_FILE
  if (!target) return
  try {
    appendFileSync(target, `${JSON.stringify({ timestamp: Date.now(), ...event })}\n`, "utf8")
  } catch {
    // Diagnostic tracing must never affect TUI behavior.
  }
}
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
  buildTranscriptViewerDetailLabel,
} from "./replViewControllerUtils.js"
import { useReplViewModalStack } from "./useReplViewModalStack.js"
import { ReplViewBaseContent } from "./ReplViewBaseContent.js"
import { buildTodoPreviewModel } from "../composer/todoPreview.js"
import { buildThinkingPreviewModel } from "../composer/thinkingPreview.js"
import { useTuiConfig } from "../../../../tui_config/context.js"
import { formatConfiguredCompletionLine } from "../../../../tui_config/load.js"
import {
  buildTaskFocusCadenceRequest,
  deriveTaskFocusCadencePlan,
  type TaskFocusCadenceState,
} from "./taskFocusCadence.js"
import { shouldSuppressAssistantToolEcho } from "./displayProjectionPolicy.js"

const META_LINE_COUNT = 2
const COMPOSER_MIN_ROWS = 6
const TOOL_COLLAPSE_THRESHOLD = 24
const TOOL_COLLAPSE_HEAD = 6
const TOOL_COLLAPSE_TAIL = 6
const TOOL_LABEL_WIDTH = 12
const LABEL_WIDTH = 9
const DEFAULT_TASK_FOCUS_TAIL_LINES = 24
const DEFAULT_TASK_FOCUS_REFRESH_MS = 1500

const normalizeToolDisplayLines = (value: string | ReadonlyArray<string> | null | undefined): string[] => {
  if (Array.isArray(value)) {
    return value
      .map((line) => (typeof line === "string" ? line : ""))
      .filter((line) => line.length > 0)
  }
  if (typeof value !== "string") return []
  return normalizeNewlines(value)
    .split("\n")
    .map((line) => line.trimEnd())
    .filter((line) => line.length > 0)
}

type TaskboardSessionPrefs = {
  readonly statusFilter: "all" | "running" | "blocked" | "completed" | "failed" | "cancelled" | "pending"
  readonly groupMode: "status" | "lane"
  readonly laneFilter: string
  readonly collapsedGroups: ReadonlyArray<string>
}

const TASKBOARD_SESSION_PREFS = new Map<string, TaskboardSessionPrefs>()
const parseBoolEnv = (value: string | undefined, fallback: boolean): boolean => {
  if (value == null) return fallback
  const normalized = value.trim().toLowerCase()
  if (!normalized) return fallback
  if (["1", "true", "yes", "on"].includes(normalized)) return true
  if (["0", "false", "no", "off"].includes(normalized)) return false
  return fallback
}

const parseIntEnv = (value: string | undefined, fallback: number, minimum: number, maximum: number): number => {
  if (value == null) return fallback
  const parsed = Number(value.trim())
  if (!Number.isFinite(parsed)) return fallback
  return Math.max(minimum, Math.min(maximum, Math.floor(parsed)))
}

const resolveScreenReaderMode = (): boolean => parseBoolEnv(process.env.BREADBOARD_TUI_SCREEN_READER, false)

const resolveScreenReaderProfile = (): "concise" | "balanced" | "verbose" => {
  const raw = (process.env.BREADBOARD_TUI_SCREEN_READER_PROFILE ?? "").trim().toLowerCase()
  if (raw === "concise" || raw === "verbose") return raw
  return "balanced"
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
  mainFollowTail,
  disconnected,
  mode,
  permissionMode,
  hints,
  stats,
  modelMenu,
  skillsMenu,
  inspectMenu,
  guardrailNotice,
  activity,
  runtimeFlags,
  thinkingArtifact,
  thinkingPreview,
  viewClearAt,
  viewPrefs,
  todoScopeKey,
  todoScopeLabel,
  todoScopeStale,
  todoStore,
  todos,
  tasks,
  workGraph,
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
  onTaskAction,
  onRewindClose,
  onRewindRestore,
  onListFiles,
  onReadFile,
  onReadWorkingTreeDiff,
  onExportWorkingTreeDiffPatch,
  onCopyWorkingTreeDiffPatch,
  onListRecentSessions,
  onAttachSession,
  onCtreeRequest,
  onCtreeRefresh,
  liveShellOwnershipMode = "inline-scrollback",
  liveShellRendererHost = "ink-managed",
  liveShellSceneStrategy = "scene-owned-runtime",
}: ReplViewProps) => {
  // Controller ownership is semantic, not presentational:
  // - derive the canonical hot/warm/cold surface state
  // - route active-surface input ownership
  // - prepare render-model inputs for shell, bottom band, and viewer
  // It should not directly simulate fullscreen viewport repair in default
  // inline mode; shell and explicit viewer modes own that separately.
  const tuiConfig = useTuiConfig()
  const OWNED_LIVE_SHELL = liveShellOwnershipMode === "owned-live"
  const SCROLLBACK_MODE = useMemo(() => (OWNED_LIVE_SHELL ? false : resolveScrollbackEnabled()), [OWNED_LIVE_SHELL])
  const SCREEN_READER_MODE = useMemo(() => resolveScreenReaderMode(), [])
  const SCREEN_READER_PROFILE = useMemo(() => resolveScreenReaderProfile(), [])
  const FOOTER_V2_ENABLED = useMemo(() => parseBoolEnv(process.env.BREADBOARD_TUI_FOOTER_V2, true), [])
  const { stdout } = useStdout()
  const terminalSize = useTerminalSize(stdout)
  const reclaimOverlayRegion = useCallback((mode: "bounded" | "close" = "bounded") => {
    if (!SCROLLBACK_MODE) return
    if (!stdout?.isTTY) return
    try {
      const rows = Math.max(1, resolveActualStdoutRows(stdout?.rows))
      const clearRows = mode === "close"
        ? Math.max(1, Math.min(rows - 1, 24))
        : Math.max(1, Math.min(rows, 24))
      stdout.write(buildLineRangeAboveActiveBandClearSequence(1, clearRows))
    } catch {
      // Ignore write failures; Ink will still handle the state transition.
    }
  }, [SCROLLBACK_MODE, stdout])
  const reclaimShortcutsOverlay = useCallback(() => {
    reclaimOverlayRegion("close")
  }, [reclaimOverlayRegion])
  const reclaimOwnedLiveModalRegion = useCallback(() => {
    if (SCROLLBACK_MODE) return
    if (!stdout?.isTTY) return
    try {
      const rows = Math.max(1, resolveActualStdoutRows(stdout?.rows))
      const reclaimRows = Math.min(rows, 24)
      stdout.write(buildBottomAnchoredClearSequence(reclaimRows))
    } catch {
      // Ignore reclaim failures; Ink will still render the modal state.
    }
  }, [SCROLLBACK_MODE, stdout])
  const openModelMenuInTransientSurface = useCallback(async () => {
    await onModelMenuOpen()
  }, [onModelMenuOpen])
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
  useInput((input, key) => {
    if (!shortcutsOpen) return
    const isCloseKey = input === "?" || input === "\u001b" || key.escape
    if (!isCloseKey) return
    if (input === "?" && process.env.BREADBOARD_SHORTCUTS_STICKY === "1") return
    const openedAt = shortcutsOpenedAtRef.current
    if (input === "?" && openedAt && Date.now() - openedAt < 200) return
    shortcutsOpenedAtRef.current = null
    reclaimShortcutsOverlay()
    setShortcutsOpen(false)
  })
  const shortcutsOpenedDuringRecoveryRef = useRef(false)
  useEffect(() => {
    if (!shortcutsOpen) {
      shortcutsOpenedDuringRecoveryRef.current = false
      return
    }
    const lifecycleText = `${status ?? ""}`.toLowerCase()
    const recoveryStateActive =
      pendingResponse ||
      disconnected ||
      lifecycleText.includes("reconnect") ||
      lifecycleText.includes("restart") ||
      lifecycleText.includes("recover") ||
      lifecycleText.includes("session missing")
    if (!recoveryStateActive) return
    shortcutsOpenedDuringRecoveryRef.current = true
    if (shortcutsOpenedDuringRecoveryRef.current && (disconnected || !pendingResponse)) {
      shortcutsOpenedAtRef.current = null
      reclaimShortcutsOverlay()
      setShortcutsOpen(false)
    }
  }, [
    disconnected,
    pendingResponse,
    reclaimShortcutsOverlay,
    setShortcutsOpen,
    shortcutsOpen,
    shortcutsOpenedAtRef,
    status,
  ])
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
  const [taskActionNotice, setTaskActionNotice] = useState<string | null>(null)
  const [taskSearchQuery, setTaskSearchQuery] = useState("")
  const taskboardInputQuarantineUntilRef = useRef(0)
  const [taskStatusFilter, setTaskStatusFilter] = useState<
    "all" | "running" | "blocked" | "completed" | "failed" | "cancelled" | "pending"
  >("all")
  const [taskLaneFilter, setTaskLaneFilter] = useState<string>("all")
  const [taskGroupMode, setTaskGroupMode] = useState<"status" | "lane">("status")
  const [taskCollapsedGroupKeys, setTaskCollapsedGroupKeys] = useState<Set<string>>(new Set())
  const [taskSelectedTaskId, setTaskSelectedTaskId] = useState<string | null>(null)
  const [taskTailLines, setTaskTailLines] = useState<string[]>([])
  const [taskTailPath, setTaskTailPath] = useState<string | null>(null)
  const [taskFocusLaneId, setTaskFocusLaneId] = useState<string | null>(null)
  const [taskFocusViewOpen, setTaskFocusViewOpen] = useState(false)
  const [taskFocusFollowTail, setTaskFocusFollowTail] = useState(true)
  const [taskFocusRawMode, setTaskFocusRawMode] = useState(false)
  const previousTaskRowIdSignatureRef = useRef("")
  const taskFocusDefaultTailLines = useMemo(
    () => parseIntEnv(process.env.BREADBOARD_SUBAGENTS_FOCUS_MAX_LINES_INITIAL, DEFAULT_TASK_FOCUS_TAIL_LINES, 8, 400),
    [],
  )
  const taskFocusRefreshMs = useMemo(
    () => parseIntEnv(process.env.BREADBOARD_SUBAGENTS_FOCUS_REFRESH_MS, DEFAULT_TASK_FOCUS_REFRESH_MS, 250, 15000),
    [],
  )
  const taskFocusMode = tuiConfig.subagents.focusMode === "swap" ? "swap" : "lane"
  const [taskFocusTailLines, setTaskFocusTailLines] = useState(taskFocusDefaultTailLines)
  const taskFocusCadenceRef = useRef<TaskFocusCadenceState | null>(null)
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
  const [transcriptViewerRawMode, setTranscriptViewerRawMode] = useState(false)
  const [transcriptViewerScroll, setTranscriptViewerScroll] = useState(0)
  const [transcriptViewerFollowTail, setTranscriptViewerFollowTail] = useState(true)
  const [transcriptSearchQuery, setTranscriptSearchQuery] = useState("")
  const [transcriptSearchOpen, setTranscriptSearchOpen] = useState(false)
  const [transcriptSearchIndex, setTranscriptSearchIndex] = useState(0)
  const [transcriptExportNotice, setTranscriptExportNotice] = useState<string | null>(null)
  const [transcriptToolIndex, setTranscriptToolIndex] = useState(0)
  const [transcriptNudge, setTranscriptNudge] = useState(0)
  const [resultDetailOpen, setResultDetailOpen] = useState(false)
  const [resultDetailScroll, setResultDetailScroll] = useState(0)
  const [artifactPreviewOpen, setArtifactPreviewOpen] = useState(false)
  const [artifactPreviewScroll, setArtifactPreviewScroll] = useState(0)
  const [artifactPreviewLines, setArtifactPreviewLines] = useState<string[]>([])
  const [artifactPreviewPath, setArtifactPreviewPath] = useState<string | null>(null)
  const [artifactPreviewNotice, setArtifactPreviewNotice] = useState<string | null>(null)
  const transcriptInspectionTargetRef = useRef<TranscriptToolTarget | null>(null)
  const [recentSessionsOpen, setRecentSessionsOpen] = useState(false)
  const [recentSessionsStatus, setRecentSessionsStatus] = useState<"idle" | "loading" | "ready" | "error">("idle")
  const [recentSessionsError, setRecentSessionsError] = useState<string | null>(null)
  const [recentSessionsRows, setRecentSessionsRows] = useState<RecentSessionRow[]>([])
  const [recentSessionsIndex, setRecentSessionsIndex] = useState(0)
  const [recentSessionsScroll, setRecentSessionsScroll] = useState(0)
  const [recentSessionsAttachingId, setRecentSessionsAttachingId] = useState<string | null>(null)
  const recentSessionsOpenedAtRef = useRef<number | null>(null)
  const [collapsedDetailOpen, setCollapsedDetailOpen] = useState(false)
  const [collapsedDetailScroll, setCollapsedDetailScroll] = useState(0)
  const draftAppliedRef = useRef(false)
  const draftLoadSeq = useRef(0)
  const draftSaveTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const lastSavedDraftRef = useRef<{ text: string; cursor: number } | null>(null)
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
      if (shouldSuppressAssistantToolEcho(entry, toolEventsProp)) return false
      return true
    })
    if (!viewClearAt) return filtered
    return filtered.filter((entry) => entry.createdAt >= viewClearAt)
  }, [conversationProp, toolEventsProp, viewClearAt])
  const suppressThinkingPreview = useMemo(
    () =>
      shouldSuppressThinkingPreview({
        activityPrimary: activity?.primary ?? null,
        conversation,
        pendingResponse,
        previewEventCount: Number((thinkingPreview as ThinkingPreviewState | null | undefined)?.eventCount ?? 0),
      }),
    [activity?.primary, conversation, pendingResponse, thinkingPreview],
  )
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
  const taskboardDefaultView = useMemo(() => {
    const raw = (process.env.BREADBOARD_SUBAGENTS_DEFAULT_VIEW ?? "tasks").trim().toLowerCase()
    if (raw === "todos" || raw === "combined") return raw
    return "tasks"
  }, [])
  const isBreadboardProfile = profile.name === "breadboard_v1"
  const claudeChrome = chromeMode === "claude"
  const filePickerResources = useMemo(() => loadFilePickerResources(), [])
  const [, forceRedraw] = useState(0)
  const repaintOwnedLiveModalRegion = useCallback(() => {
    reclaimOwnedLiveModalRegion()
    if (SCROLLBACK_MODE) return
    setTimeout(() => {
      reclaimOwnedLiveModalRegion()
      forceRedraw((value) => value + 1)
    }, 30)
  }, [SCROLLBACK_MODE, forceRedraw, reclaimOwnedLiveModalRegion])
  useEffect(() => {
    configureShikiTheme(tuiConfig.markdown.shikiTheme)
    const unsubscribe = subscribeShiki(() => {
      forceShikiRefresh((value) => value + 1)
    })
    ensureShikiLoaded()
    return unsubscribe
  }, [forceShikiRefresh, tuiConfig.markdown.shikiTheme])
  const fixedFrameWidthRaw = (process.env.BREADBOARD_TUI_FRAME_WIDTH ?? "").toString().trim()
  const fixedFrameWidth = fixedFrameWidthRaw ? Number(fixedFrameWidthRaw) : NaN
  const resolvedColumns = terminalSize.columns
  const columnWidth =
    Number.isFinite(fixedFrameWidth) && fixedFrameWidth > 0
      ? Math.min(fixedFrameWidth, resolvedColumns)
      : resolvedColumns
  const contentWidth = useMemo(
    () => Math.max(10, columnWidth - (claudeChrome ? 0 : 2)),
    [claudeChrome, columnWidth],
  )
  const rowCount = terminalSize.rows
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
      setPendingStartedAt(pendingStartedAtRef.current)
      return
    }
    if (pendingStartedAtRef.current != null) {
      const duration = Date.now() - pendingStartedAtRef.current
      pendingStartedAtRef.current = null
      setLastDurationMs(duration)
    }
    setPendingStartedAt(null)
  }, [pendingResponse])
  const refreshRecentSessions = useCallback(async () => {
    setRecentSessionsStatus("loading")
    setRecentSessionsError(null)
    try {
      const rows = await onListRecentSessions()
      setRecentSessionsRows(rows)
      setRecentSessionsStatus("ready")
      setRecentSessionsIndex((prev) => Math.max(0, Math.min(prev, Math.max(0, rows.length - 1))))
      setRecentSessionsScroll(0)
      return rows
    } catch (error) {
      setRecentSessionsRows([])
      setRecentSessionsStatus("error")
      setRecentSessionsError((error as Error).message || String(error))
      return []
    }
  }, [onListRecentSessions])
  const attachRecentSession = useCallback(
    async (targetSessionId: string) => {
      const openedAt = recentSessionsOpenedAtRef.current
      if (openedAt && Date.now() - openedAt < 200) {
        return false
      }
      recentSessionsOpenedAtRef.current = null
      const next = String(targetSessionId ?? "").trim()
      if (!next) return false
      setRecentSessionsAttachingId(next)
      try {
        const ok = await onAttachSession(next)
        if (ok) {
          setRecentSessionsOpen(false)
          setRecentSessionsScroll(0)
          setRecentSessionsIndex(0)
          setRecentSessionsStatus("idle")
          setRecentSessionsError(null)
          setCollapsedDetailOpen(false)
          setCollapsedDetailScroll(0)
        }
        return ok
      } finally {
        setRecentSessionsAttachingId((prev) => (prev === next ? null : prev))
      }
    },
    [onAttachSession],
  )
  useEffect(() => {
    if (!recentSessionsOpen) return
    if (recentSessionsStatus === "loading") return
    if (recentSessionsStatus === "ready" && recentSessionsRows.length > 0) return
    void refreshRecentSessions()
  }, [recentSessionsOpen, recentSessionsRows.length, recentSessionsStatus, refreshRecentSessions])
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
    shortcutsOpenedAtRef,
    usageOpen,
    permissionRequest,
    rewindMenu,
    todosOpen,
    tasksOpen,
    ctreeOpen,
    transcriptViewerOpen,
    transcriptViewerRawMode,
    recentSessionsOpen,
    collapsedDetailOpen,
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
    transcriptToolIndex,
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
    todoStore,
    todos,
    tasks,
    workGraph,
    subagentTaskboardEnabled: tuiConfig.subagents.taskboardEnabled,
    taskIndex,
    taskFocusLaneId,
    taskFocusViewOpen,
    taskGroupMode,
    taskCollapsedGroupKeys,
    taskSearchQuery,
    taskStatusFilter,
    taskLaneFilter,
    taskNotice,
    taskActionNotice,
    taskTailLines,
    taskTailPath,
    setTaskNotice,
    setTaskActionNotice,
    setTaskTailLines,
    setTaskTailPath,
    onTaskAction,
    ctreeTree,
    ctreeCollapsedNodes,
    ctreeIndex,
    ctreeIncludePreviews,
    onReadFile,
    onReadWorkingTreeDiff,
    onExportWorkingTreeDiffPatch,
    onCopyWorkingTreeDiffPatch,
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
    searchHistory,
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
    transcriptViewerExportLines,
    transcriptToolLines,
    selectedTranscriptToolTarget,
    transcriptViewerBodyRows,
    transcriptViewerMaxScroll,
    transcriptViewerEffectiveScroll,
    transcriptSearchMatches,
    transcriptSearchSafeIndex,
    transcriptSearchActiveLine,
    transcriptSearchLineMatches,
    jumpTranscriptToLine,
    jumpTranscriptToAnchor,
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
    taskGroups,
    taskLaneOrder,
    taskLaneFilterLabel,
    taskFocusLaneLabel,
    taskActionsEnabled,
    taskViewportRows,
    taskMaxScroll,
    selectedTaskIndex,
    selectedTaskRow,
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
    exportTaskLog,
    runTaskAction,
  } = panels
  const taskRowIdSignature = useMemo(
    () => taskRows.map((row: any) => String(row?.id ?? "")).join("\u001f"),
    [taskRows],
  )
  const taskRowIds = useMemo(
    () => taskRowIdSignature.split("\u001f").filter((id) => id.length > 0),
    [taskRowIdSignature],
  )
  const taskGroupKeySignature = useMemo(
    () => taskGroups.map((group: any) => String(group?.key ?? "")).join("\u001f"),
    [taskGroups],
  )
  const taskCollapsedGroupSignature = useMemo(
    () => Array.from(taskCollapsedGroupKeys).sort().join("\u001f"),
    [taskCollapsedGroupKeys],
  )

  const todoPreviewModel = useMemo(() => {
    if (!claudeChrome) return null
    if (overlayActive) return null
    if (!tuiConfig.composer.todoPreviewAboveInput) return null
    if (rowCount < tuiConfig.composer.todoPreviewMinRowsToShow) return null
    const maxItems =
      rowCount < 14
        ? Math.min(tuiConfig.composer.todoPreviewMaxItems, tuiConfig.composer.todoPreviewSmallRowsMaxItems)
        : tuiConfig.composer.todoPreviewMaxItems
    return buildTodoPreviewModel(todoStore, {
      maxItems,
      strategy: tuiConfig.composer.todoPreviewSelection,
      showHiddenCount: tuiConfig.composer.todoPreviewShowHiddenCount,
      scopeKey: todoScopeKey,
      scopeLabel: todoScopeLabel,
      stale: todoScopeStale,
      style: tuiConfig.composer.todoPreviewStyle,
      cols: contentWidth,
    })
  }, [
    claudeChrome,
    overlayActive,
    rowCount,
    contentWidth,
    todoStore,
    todoScopeKey,
    todoScopeLabel,
    todoScopeStale,
    tuiConfig.composer.todoPreviewAboveInput,
    tuiConfig.composer.todoPreviewMaxItems,
    tuiConfig.composer.todoPreviewSelection,
    tuiConfig.composer.todoPreviewShowHiddenCount,
    tuiConfig.composer.todoPreviewStyle,
    tuiConfig.composer.todoPreviewMinRowsToShow,
    tuiConfig.composer.todoPreviewSmallRowsMaxItems,
  ])

  const thinkingPreviewModel = useMemo(() => {
    if (!claudeChrome) return null
    if (overlayActive) return null
    if (runtimeFlags?.thinkingPreviewEnabled === false) return null
    // Once answer text is visible, the task-tree/thinking preview becomes a
    // high-churn secondary surface. Hiding it keeps the active composer band
    // stable during tall streamed responses.
    if (suppressThinkingPreview) return null
    return buildThinkingPreviewModel(thinkingPreview as ThinkingPreviewState | null, thinkingArtifact as ThinkingArtifact | null, {
      cols: contentWidth,
      maxLines: Math.max(1, Number(runtimeFlags?.thinkingPreviewMaxLines ?? 5)),
      showWhenClosed: pendingResponse,
    })
  }, [suppressThinkingPreview, claudeChrome, contentWidth, overlayActive, pendingResponse, runtimeFlags, thinkingArtifact, thinkingPreview])

  const runtimeStatusChips = useMemo(() => {
    if (disconnected) {
      return [{ id: "disconnected", label: "disconnected", tone: "error" as const }]
    }
    if (pendingResponse) {
      const phase = String(activity?.primary ?? "").trim().toLowerCase()
      const label = phase.includes("think") ? "thinking" : "responding"
      return [{ id: label, label, tone: "info" as const }]
    }
    if (activity?.primary === "reconnecting") {
      return [{ id: "reconnecting", label: "reconnecting", tone: "warning" as const }]
    }
    if (activity?.primary === "halted" || status === "halted") {
      return [{ id: "halted", label: "halted", tone: "warning" as const }]
    }
    return []
  }, [activity, disconnected, pendingResponse, status])

  const phaseLineState = useMemo(
    () =>
      disconnected
        ? ({ id: "disconnected", label: "disconnected", tone: "error" } as const)
        : pendingResponse
          ? ({ id: "responding", label: "responding", tone: "info" } as const)
          : activity?.primary === "reconnecting"
            ? ({ id: "reconnecting", label: "reconnecting", tone: "warning" } as const)
          : activity?.primary === "halted" || status === "halted"
            ? ({ id: "halted", label: "halted", tone: "warning" } as const)
            : ({ id: "ready", label: "ready", tone: "muted" } as const),
    [activity?.primary, disconnected, pendingResponse, status],
  )

  useEffect(() => {
    const liveKeys = new Set(
      Array.isArray(taskGroups)
        ? taskGroups
            .map((group: any) => (typeof group?.key === "string" ? group.key : null))
            .filter((value: string | null): value is string => Boolean(value))
        : [],
    )
    setTaskCollapsedGroupKeys((prev) => {
      if (!(prev instanceof Set) || prev.size === 0) return prev
      let changed = false
      const next = new Set<string>()
      for (const key of prev) {
        if (liveKeys.has(key)) next.add(key)
        else changed = true
      }
      return changed ? next : prev
    })
  }, [taskGroupKeySignature, taskGroups])
  useEffect(() => {
    const activeSessionId = String(sessionId ?? "").trim()
    if (!activeSessionId) return
    const stored = TASKBOARD_SESSION_PREFS.get(activeSessionId)
    if (!stored) return
    setTaskStatusFilter(stored.statusFilter)
    setTaskGroupMode(stored.groupMode)
    setTaskLaneFilter(stored.laneFilter)
    setTaskCollapsedGroupKeys((prev) => {
      const next = new Set(stored.collapsedGroups)
      if (prev.size === next.size && Array.from(prev).every((key) => next.has(key))) return prev
      return next
    })
  }, [sessionId])
  useEffect(() => {
    const activeSessionId = String(sessionId ?? "").trim()
    if (!activeSessionId) return
    TASKBOARD_SESSION_PREFS.set(activeSessionId, {
      statusFilter: taskStatusFilter,
      groupMode: taskGroupMode,
      laneFilter: taskLaneFilter,
      collapsedGroups: Array.from(taskCollapsedGroupKeys),
    })
  }, [sessionId, taskCollapsedGroupSignature, taskCollapsedGroupKeys, taskGroupMode, taskLaneFilter, taskStatusFilter])
  useEffect(() => {
    const previousSignature = previousTaskRowIdSignatureRef.current
    const rowsChanged = previousSignature !== taskRowIdSignature
    previousTaskRowIdSignatureRef.current = taskRowIdSignature
    if (!tasksOpen) return

    if (rowsChanged && taskSelectedTaskId) {
      const stableIndex = taskRowIds.indexOf(taskSelectedTaskId)
      if (stableIndex >= 0 && stableIndex !== selectedTaskIndex) {
        setTaskIndex((prev) => (prev === stableIndex ? prev : stableIndex))
        return
      }
    }

    const selectedId = typeof selectedTask?.id === "string" && selectedTask.id.length > 0 ? selectedTask.id : null
    if (selectedId !== taskSelectedTaskId) {
      setTaskSelectedTaskId(selectedId)
    }
  }, [selectedTask?.id, selectedTaskIndex, taskRowIdSignature, taskRowIds, taskSelectedTaskId, tasksOpen])
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
    setTaskActionNotice,
    setTaskSearchQuery,
    setTaskStatusFilter,
    setTaskLaneFilter,
    setTaskGroupMode,
    setTaskCollapsedGroupKeys,
    setTaskTailLines,
    setTaskTailPath,
    setTaskFocusLaneId,
    taskFocusViewOpen,
    setTaskFocusViewOpen,
    setTaskFocusFollowTail,
    setTaskFocusRawMode,
    setTaskFocusTailLines,
    taskFocusDefaultTailLines,
    exportTaskLog,
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
    liveShellOwnershipMode,
    liveShellRendererHost,
    transcriptViewerOpen,
    transcriptViewerScroll,
    transcriptViewerFollowTail,
    setTranscriptViewerRawMode,
    setTranscriptViewerOpen,
    setTranscriptViewerFollowTail,
    setTranscriptViewerScroll,
    setTranscriptSearchOpen,
    setTranscriptSearchQuery,
    setTranscriptSearchIndex,
    setTranscriptExportNotice,
    transcriptViewerLines,
    transcriptViewerExportLines,
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
    queuedPrompt,
    clearScreen,
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
    onListRecentSessions,
    onAttachSession,
    handleLineEdit,
    onReadFile,
    onReadWorkingTreeDiff,
    onExportWorkingTreeDiffPatch,
    onCopyWorkingTreeDiffPatch,
    pushCommandResult,
    setSuggestIndex,
    setSuppressSuggestions,
    attachments,
    clearAttachments,
    onSubmit,
    setTodosOpen,
    setUsageOpen,
    setTasksOpen,
    setRecentSessionsOpen,
    recentSessionsOpenedAtRef,
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
    jumpTranscriptToAnchor,
    keymap,
    modelIndex,
    modelMenu,
    modelProviderFilter,
    modelProviderOrder,
    modelSearch,
    modelVisibleRows: MODEL_VISIBLE_ROWS,
    normalizeProviderKey,
    reclaimOverlayRegion,
    onCtreeRefresh,
    onModelMenuOpen: openModelMenuInTransientSurface,
    onModelMenuCancel,
    onModelSelect,
    onPermissionDecision,
    onRewindClose,
    onRewindRestore,
    onSkillsMenuCancel,
    onSkillsMenuOpen,
    pendingResponse,
    disconnected,
    permissionMode,
    permissionQueueDepth,
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
    setTaskNotice,
    setTaskActionNotice,
    setTaskSearchQuery,
    setTaskStatusFilter,
    setTaskLaneFilter,
    setTaskGroupMode,
    setTaskCollapsedGroupKeys,
    setTaskFocusLaneId,
    setTaskFocusViewOpen,
    setTaskFocusFollowTail,
    setTaskFocusRawMode,
    setTaskFocusTailLines,
    taskboardInputQuarantineUntilRef,
    setTodoScroll,
    setTranscriptSearchIndex,
    setTranscriptSearchOpen,
    setTranscriptSearchQuery,
    setTranscriptToolIndex,
    setTranscriptViewerFollowTail,
    setTranscriptViewerScroll,
    transcriptViewerInputQuarantineUntilRef: menus.transcriptViewerInputQuarantineUntilRef,
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
    taskGroups,
    taskLaneOrder,
    taskScroll,
    taskSearchQuery,
    taskStatusFilter,
    taskLaneFilter,
    taskLaneFilterLabel,
    taskGroupMode,
    taskCollapsedGroupKeys,
    taskFocusLaneId,
    taskFocusViewOpen,
    taskFocusFollowTail,
    taskFocusRawMode,
    taskFocusTailLines,
    taskFocusMode,
    taskFocusDefaultTailLines,
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
    selectedTaskRow,
    selectedTask,
    requestTaskTail,
    exportTaskLog,
    usageOpen,
    suggestIndex,
    suggestions,
    removeLastAttachment,
    pushHistoryEntry,
    recallHistory,
    searchHistory,
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
  const landingAlways = useMemo(() => {
    const raw = (process.env.BREADBOARD_TUI_LANDING_ALWAYS ?? "").trim().toLowerCase()
    return ["1", "true", "yes", "on"].includes(raw)
  }, [])

  const {
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
    networkBanner,
    compactMode,
    bodyBudgetRows,
    activeBodyMinRows,
    composerRowsAboveCursor,
    managedViewportRowsAboveCursor,
    managedViewportResetKey,
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
    completionHint:
      claudeChrome && lastDurationMs != null && tuiConfig.statusLine.showOnComplete
        ? formatConfiguredCompletionLine(tuiConfig, formatDuration(lastDurationMs))
        : null,
    statusLinePosition: tuiConfig.statusLine.position,
    statusLineAlign: tuiConfig.statusLine.align,
    renderConversationEntryForFeed,
    renderToolEntryForFeed,
    renderConversationEntryRef,
    renderToolEntryRef,
    pushCommandResultRef,
    conversation,
    toolEvents,
    SCROLLBACK_MODE,
    screenReaderMode: SCREEN_READER_MODE,
    screenReaderProfile: SCREEN_READER_PROFILE,
    ASCII_HEADER,
    setCollapsedVersion,
    collapsedEntriesRef,
    collapsedVersion,
    selectedCollapsibleEntryId,
    setSelectedCollapsibleEntryId,
    animationTick,
    spinner,
    liveShellOwnershipMode,
    liveSlots,
    keymap,
    footerV2Enabled: FOOTER_V2_ENABLED,
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
    tuiConfig,
    landingAlways,
  })
  const volatileActiveBand = useMemo(
    () =>
      SCROLLBACK_MODE &&
      !overlayActive &&
      !pendingResponse &&
      !transcriptViewerOpen &&
      suggestions.length === 0 &&
      (
        filePickerActive ||
        input.length > 0 ||
        attachments.length > 0 ||
        fileMentions.length > 0
      ),
    [
      SCROLLBACK_MODE,
      attachments.length,
      fileMentions.length,
      filePickerActive,
      input.length,
      overlayActive,
      pendingResponse,
      suggestions.length,
      transcriptViewerOpen,
    ],
  )
  const recentSessionsViewportRows = useMemo(() => Math.max(8, Math.min(16, Math.floor(rowCount * 0.4))), [rowCount])
  const recentSessionsMaxScroll = useMemo(
    () => Math.max(0, recentSessionsRows.length - recentSessionsViewportRows),
    [recentSessionsRows.length, recentSessionsViewportRows],
  )
  const recentSessionsVisible = useMemo(
    () => recentSessionsRows.slice(recentSessionsScroll, recentSessionsScroll + recentSessionsViewportRows),
    [recentSessionsRows, recentSessionsScroll, recentSessionsViewportRows],
  )
  useEffect(() => {
    if (!recentSessionsOpen) return
    setRecentSessionsIndex((prev) => Math.max(0, Math.min(prev, Math.max(0, recentSessionsRows.length - 1))))
    setRecentSessionsScroll((prev) => Math.max(0, Math.min(prev, recentSessionsMaxScroll)))
  }, [recentSessionsMaxScroll, recentSessionsOpen, recentSessionsRows.length])

  const selectedCollapsedEntry = useMemo(
    () =>
      (collapsibleEntries.find(
        (entry): entry is TranscriptMessageItem =>
          entry.kind === "message" && entry.id === selectedCollapsibleEntryId,
      ) ?? null),
    [collapsibleEntries, selectedCollapsibleEntryId],
  )
  const collapsedDetailLines = useMemo(() => {
    if (!selectedCollapsedEntry) return []
    const text = normalizeNewlines(selectedCollapsedEntry.text ?? "")
    return text.length > 0 ? text.split("\n") : [""]
  }, [selectedCollapsedEntry])
  const resolveTranscriptToolTarget = useCallback(() => {
    if (selectedTranscriptToolTarget) return selectedTranscriptToolTarget
    const toolEntries = toolEvents.filter((entry) => entry.kind === "call" || entry.kind === "result" || entry.kind === "command")
    if (toolEntries.length === 0) return null
    const safeIndex = Math.max(0, Math.min(transcriptToolIndex, toolEntries.length - 1))
    const entry = toolEntries[safeIndex]
    if (!entry) return null
    const display = (entry.display ?? null) as ToolDisplayPayload | null
    const artifactRef = display?.detail_artifact ?? null
    const title =
      typeof display?.title === "string" && display.title.trim().length > 0
        ? display.title.trim()
        : normalizeNewlines(entry.text ?? "").split("\n")[0]?.trim() || "Tool"
    return {
      line: transcriptToolLines[safeIndex] ?? 0,
      itemId: entry.id,
      title,
      summaryLines: normalizeToolDisplayLines(display?.summary ?? null),
      detailLines: normalizeToolDisplayLines(display?.detail ?? null),
      artifactPath:
        typeof artifactRef?.path === "string" && artifactRef.path.trim().length > 0 ? artifactRef.path.trim() : null,
      artifactPreviewLines: normalizeToolDisplayLines(artifactRef?.preview?.lines ?? null),
      artifactPreviewNote:
        typeof artifactRef?.preview?.note === "string" && artifactRef.preview.note.trim().length > 0
          ? artifactRef.preview.note.trim()
          : null,
    }
  }, [selectedTranscriptToolTarget, toolEvents, transcriptToolIndex, transcriptToolLines])
  const activeTranscriptToolTarget = useMemo(
    () => resolveTranscriptToolTarget() ?? transcriptInspectionTargetRef.current,
    [resolveTranscriptToolTarget],
  )
  const collapsedDetailViewportRows = useMemo(() => Math.max(10, Math.min(24, Math.floor(rowCount * 0.55))), [rowCount])
  const collapsedDetailMaxScroll = useMemo(
    () => Math.max(0, collapsedDetailLines.length - collapsedDetailViewportRows),
    [collapsedDetailLines.length, collapsedDetailViewportRows],
  )
  const collapsedDetailVisible = useMemo(
    () => collapsedDetailLines.slice(collapsedDetailScroll, collapsedDetailScroll + collapsedDetailViewportRows),
    [collapsedDetailLines, collapsedDetailScroll, collapsedDetailViewportRows],
  )
  const resultDetailLines = useMemo(() => {
    if (!activeTranscriptToolTarget) return []
    if (activeTranscriptToolTarget.detailLines.length > 0) return activeTranscriptToolTarget.detailLines
    return activeTranscriptToolTarget.summaryLines
  }, [activeTranscriptToolTarget])
  const resultDetailViewportRows = useMemo(() => Math.max(10, Math.min(24, Math.floor(rowCount * 0.55))), [rowCount])
  const resultDetailMaxScroll = useMemo(
    () => Math.max(0, resultDetailLines.length - resultDetailViewportRows),
    [resultDetailLines.length, resultDetailViewportRows],
  )
  const resultDetailVisible = useMemo(
    () => resultDetailLines.slice(resultDetailScroll, resultDetailScroll + resultDetailViewportRows),
    [resultDetailLines, resultDetailScroll, resultDetailViewportRows],
  )
  const artifactPreviewViewportRows = useMemo(() => Math.max(10, Math.min(24, Math.floor(rowCount * 0.55))), [rowCount])
  const artifactPreviewMaxScroll = useMemo(
    () => Math.max(0, artifactPreviewLines.length - artifactPreviewViewportRows),
    [artifactPreviewLines.length, artifactPreviewViewportRows],
  )
  const artifactPreviewVisible = useMemo(
    () => artifactPreviewLines.slice(artifactPreviewScroll, artifactPreviewScroll + artifactPreviewViewportRows),
    [artifactPreviewLines, artifactPreviewScroll, artifactPreviewViewportRows],
  )
  useEffect(() => {
    if (!collapsedDetailOpen) return
    if (!selectedCollapsedEntry) {
      setCollapsedDetailOpen(false)
      setCollapsedDetailScroll(0)
      return
    }
    setCollapsedDetailScroll((prev) => Math.max(0, Math.min(prev, collapsedDetailMaxScroll)))
  }, [collapsedDetailMaxScroll, collapsedDetailOpen, selectedCollapsedEntry])
  useEffect(() => {
    if (!resultDetailOpen) return
    if (!activeTranscriptToolTarget) {
      traceKeyDiagnostic({ phase: "result-detail-effect-close", reason: "missing-active-target" })
      setResultDetailOpen(false)
      setResultDetailScroll(0)
      return
    }
    traceKeyDiagnostic({
      phase: "result-detail-effect-open",
      title: activeTranscriptToolTarget.title,
      detailLines: activeTranscriptToolTarget.detailLines.length,
      summaryLines: activeTranscriptToolTarget.summaryLines.length,
      artifactPath: activeTranscriptToolTarget.artifactPath,
    })
    setResultDetailScroll((prev) => Math.max(0, Math.min(prev, resultDetailMaxScroll)))
  }, [activeTranscriptToolTarget, resultDetailMaxScroll, resultDetailOpen])
  useEffect(() => {
    if (!artifactPreviewOpen) return
    setArtifactPreviewScroll((prev) => Math.max(0, Math.min(prev, artifactPreviewMaxScroll)))
  }, [artifactPreviewMaxScroll, artifactPreviewOpen])
  const openSelectedCollapsedDetail = useCallback(() => {
    if (!selectedCollapsedEntry) return false
    setCollapsedDetailScroll(0)
    setCollapsedDetailOpen(true)
    return true
  }, [selectedCollapsedEntry])
  const openSelectedTranscriptResultDetail = useCallback(() => {
    const target = resolveTranscriptToolTarget()
    traceKeyDiagnostic({
      phase: "open-selected-transcript-result-detail",
      found: Boolean(target),
      title: target?.title ?? null,
      detailLines: target?.detailLines.length ?? null,
      summaryLines: target?.summaryLines.length ?? null,
      artifactPath: target?.artifactPath ?? null,
    })
    if (!target) return false
    transcriptInspectionTargetRef.current = target
    setResultDetailScroll(0)
    exitTranscriptViewer()
    repaintOwnedLiveModalRegion()
    setResultDetailOpen(true)
    return true
  }, [exitTranscriptViewer, repaintOwnedLiveModalRegion, resolveTranscriptToolTarget])
  const openSelectedTranscriptArtifactPreview = useCallback(() => {
    const target = resolveTranscriptToolTarget() ?? transcriptInspectionTargetRef.current
    if (!target?.artifactPath) return false
    transcriptInspectionTargetRef.current = target
    setArtifactPreviewLines(
      target.artifactPreviewLines.length > 0
        ? [...target.artifactPreviewLines]
        : ["Artifact preview unavailable."],
    )
    setArtifactPreviewPath(target.artifactPath)
    setArtifactPreviewNotice(target.artifactPreviewNote ?? null)
    setArtifactPreviewScroll(0)
    exitTranscriptViewer()
    repaintOwnedLiveModalRegion()
    setArtifactPreviewOpen(true)
    return true
  }, [exitTranscriptViewer, repaintOwnedLiveModalRegion, resolveTranscriptToolTarget])
  useInput((input, key) => {
    if (!resultDetailOpen && !artifactPreviewOpen) return
    const isReturnKey = key.return || input === "\r" || input === "\n"
    if (artifactPreviewOpen) {
      if (key.escape || input === "\u001b") {
        setArtifactPreviewOpen(false)
      }
      return
    }
    if (key.escape || input === "\u001b") {
      setResultDetailOpen(false)
      return
    }
    if (isReturnKey && activeTranscriptToolTarget?.artifactPath) {
      openSelectedTranscriptArtifactPreview()
    }
  })
  const handleOverlayKeys = useOverlayKeys({
    alwaysAllowScope: ALWAYS_ALLOW_SCOPE,
    applySkillsSelection,
    clearScreen,
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
    enterTranscriptViewer,
    escPrimedAt,
    exitTranscriptViewer,
    filteredModels,
    handleLineEdit,
    inputValueRef,
    inspectMenu,
    inspectRawMaxScroll,
    inspectRawOpen,
    inspectRawViewportRows,
    resultDetailOpen,
    resultDetailScroll,
    resultDetailMaxScroll,
    resultDetailViewportRows,
    resultDetailArtifactPath: activeTranscriptToolTarget?.artifactPath ?? null,
    resultDetailSourceLine: activeTranscriptToolTarget?.line ?? null,
    setResultDetailOpen,
    setResultDetailScroll,
    artifactPreviewOpen,
    artifactPreviewScroll,
    artifactPreviewMaxScroll,
    artifactPreviewViewportRows,
    artifactPreviewSourceLine: activeTranscriptToolTarget?.line ?? null,
    setArtifactPreviewOpen,
    setArtifactPreviewScroll,
    jumpTranscriptToLine,
    jumpTranscriptToAnchor,
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
    onSubmit,
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
    recentSessionsOpen,
    recentSessionsRows,
    recentSessionsIndex,
    recentSessionsScroll,
    recentSessionsMaxScroll,
    recentSessionsViewportRows,
    recentSessionsOpenedAtRef,
    refreshRecentSessions,
    attachRecentSession,
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
    setTaskNotice,
    setTaskActionNotice,
    setTaskSearchQuery,
    setTaskStatusFilter,
    setTaskLaneFilter,
    setTaskGroupMode,
    setTaskCollapsedGroupKeys,
    setTaskFocusLaneId,
    setTaskFocusViewOpen,
    setTaskFocusFollowTail,
    setTaskFocusRawMode,
    setTaskFocusTailLines,
    setTasksOpen,
    taskboardInputQuarantineUntilRef,
    setRecentSessionsIndex,
    setRecentSessionsOpen,
    setRecentSessionsScroll,
    setTodoScroll,
    setTodosOpen,
    setUsageOpen,
    setTranscriptSearchIndex,
    setTranscriptSearchOpen,
    setTranscriptSearchQuery,
    setTranscriptToolIndex,
    setTranscriptViewerFollowTail,
    setTranscriptViewerScroll,
    transcriptViewerInputQuarantineUntilRef: menus.transcriptViewerInputQuarantineUntilRef,
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
    taskGroups,
    taskLaneOrder,
    taskScroll,
    taskSearchQuery,
    taskStatusFilter,
    taskLaneFilter,
    taskLaneFilterLabel,
    taskGroupMode,
    taskCollapsedGroupKeys,
    taskFocusLaneId,
    taskFocusViewOpen,
    taskFocusFollowTail,
    taskFocusRawMode,
    taskFocusTailLines,
    taskFocusMode,
    taskFocusDefaultTailLines,
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
    activeTranscriptToolTarget,
    openSelectedTranscriptResultDetail,
    openSelectedTranscriptArtifactPreview,
    collapsedDetailOpen,
    collapsedDetailScroll,
    collapsedDetailMaxScroll,
    collapsedDetailViewportRows,
    setCollapsedDetailOpen,
    setCollapsedDetailScroll,
    selectedCTreeIndex,
    selectedCTreeRow,
    selectedTaskIndex,
    selectedTaskRow,
    selectedTask,
    requestTaskTail,
    exportTaskLog,
    runTaskAction,
    usageOpen,
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
    inputValueRef,
    keymap,
    modelMenu,
    onGuardrailDismiss,
    onGuardrailToggle,
    onModelMenuCancel,
    onModelMenuOpen: openModelMenuInTransientSurface,
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
    searchHistory,
    reclaimShortcutsOverlay,
    reclaimOverlayRegion,
    scrollbackMode: SCROLLBACK_MODE,
    setCtrlCPrimedAt,
    setCtreeOpen,
    setEscPrimedAt,
    setTasksOpen,
    setTodosOpen,
    taskboardDefaultView,
    setTranscriptNudge,
    setVerboseOutput,
    skillsMenu,
    openSelectedCollapsedDetail,
    toggleSelectedCollapsibleEntry,
    transcriptViewerBodyRows,
    transcriptViewerOpen,
  })

  useReplKeyRouter({
    overlayFlags,
    modal: handleOverlayKeys,
    palette: handlePaletteKeys,
    global: handleGlobalKeys,
  })

  const footerOverlayLabel = useMemo(() => {
    if (!overlayActive) return null
    if (transcriptSearchOpen) return "transcript search"
    if (artifactPreviewOpen) return "artifact"
    if (resultDetailOpen) return "result detail"
    if (transcriptViewerOpen) return "transcript"
    if (recentSessionsOpen) return "sessions"
    if (collapsedDetailOpen) return "detail"
    if (tasksOpen) return "tasks"
    if (todosOpen) return "todos"
    if (usageOpen) return "usage"
    if (filePickerActive) return "files"
    if (modelMenu.status !== "hidden") return "model"
    return "overlay"
  }, [artifactPreviewOpen, collapsedDetailOpen, filePickerActive, modelMenu.status, overlayActive, recentSessionsOpen, resultDetailOpen, tasksOpen, todosOpen, transcriptSearchOpen, transcriptViewerOpen, usageOpen])

  const showLandingInline = SCROLLBACK_MODE && warmLandingVisible

  const composerPanelContext = {
    claudeChrome, scrollbackMode: SCROLLBACK_MODE, modelMenu, pendingClaudeStatus, todoPreviewModel, promptRule, input, cursor, inputLocked,
    attachments, fileMentions, inputMaxVisibleLines, handleLineEditGuarded, handleLineSubmit, handleEditorKeys, handleAttachment, queuedPrompt,
    overlayActive, filePickerActive, fileIndexMeta, fileMenuMode, filePicker, fileMenuRows, fileMenuHasLarge,
    fileMenuWindow, fileMenuIndex, fileMenuNeedlePending, filePickerQueryParts, filePickerConfig, fileMentionConfig,
    selectedFileIsLarge, columnWidth, todosOpen, todos, todoRows, todoScroll, todoMaxScroll, todoViewportRows,
    suggestions, suggestionWindow, suggestionPrefix, suggestionLayout,
    buildSuggestionLines, suggestIndex, activeSlashQuery, hintNodes, shortcutHintNodes,
    shortcutsOpen,
    runtimeStatusChips,
    thinkingPreviewModel,
    footerV2Enabled: FOOTER_V2_ENABLED,
    keymap,
    pendingResponse,
    mainFollowTail,
    conversationLength: conversation.length,
    phaseLineState,
    disconnected,
    status,
    overlayLabel: footerOverlayLabel,
    spinner,
    pendingStartedAtMs: pendingStartedAt,
    lastDurationMs,
    clockNowMs: Date.now(),
    tasks,
    stats,
    sessionId,
    transcriptViewerOpen,
    transcriptSearchOpen,
    scrollbackSuppressIdlePlaceholder: SCROLLBACK_MODE && !pendingResponse && conversation.length > 0,
    composerPromptPrefix: tuiConfig.composer.promptPrefix,
    composerPlaceholderClassic: tuiConfig.composer.placeholderClassic,
    composerPlaceholderClaude: tuiConfig.composer.placeholderClaude,
    composerShowTopRule: tuiConfig.composer.showTopRule,
    composerShowBottomRule: tuiConfig.composer.showBottomRule,
  }
  const baseContent = (
    <ReplViewBaseContent
      claudeChrome={claudeChrome}
      footerV2Enabled={FOOTER_V2_ENABLED}
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
      guardrailNotice={SCROLLBACK_MODE ? null : guardrailNotice ?? null}
      networkBanner={networkBanner}
      networkBannerWidth={contentWidth}
      showLandingInline={showLandingInline}
      showSessionHeaderInline={showSessionHeaderInline}
      landingNode={landingNode}
      sessionHeaderNode={sessionHeaderNode}
      transcriptNodes={transcriptNodes}
      toolNodes={toolNodes}
      overlayActive={overlayActive}
      subagentStripNode={subagentStripNode}
      liveSlotNodes={liveSlotNodes}
      collapsedHintNode={collapsedHintNode}
      virtualizationHintNode={virtualizationHintNode}
      activeBodyMinRows={SCROLLBACK_MODE ? activeBodyMinRows : 0}
      composerPanelContext={composerPanelContext}
    />
  )

  const transcriptDetailLabel = useMemo(() =>
    buildTranscriptViewerDetailLabel({
      followTail: transcriptViewerFollowTail,
      lineCount: transcriptViewerLines.length,
      effectiveScroll: transcriptViewerEffectiveScroll,
      bodyRows: transcriptViewerBodyRows,
      searchOpen: transcriptSearchOpen,
      searchQuery: transcriptSearchQuery,
      searchMatchCount: transcriptSearchMatches.length,
      searchSafeIndex: transcriptSearchSafeIndex,
      verboseOutput,
      exportNotice: transcriptExportNotice,
      selectedToolLabel: activeTranscriptToolTarget?.title ?? null,
      selectedToolOrdinal:
        activeTranscriptToolTarget != null && transcriptToolLines.length > 0 ? `${transcriptToolIndex + 1}/${transcriptToolLines.length}` : null,
      selectedToolHasArtifact: Boolean(activeTranscriptToolTarget?.artifactPath),
      rawMode: transcriptViewerRawMode,
    }),
  [
    transcriptExportNotice,
    transcriptSearchMatches.length,
    transcriptSearchOpen,
    transcriptSearchQuery,
    transcriptSearchSafeIndex,
    transcriptViewerBodyRows,
    transcriptViewerEffectiveScroll,
    transcriptViewerFollowTail,
    transcriptViewerLines.length,
    transcriptViewerRawMode,
    transcriptToolIndex,
    transcriptToolLines.length,
    verboseOutput,
    activeTranscriptToolTarget,
  ])

  useEffect(() => {
    const nextCadence: TaskFocusCadenceState = {
      tasksOpen,
      focusViewOpen: taskFocusViewOpen,
      followTail: taskFocusFollowTail,
      selectedTaskId: selectedTask?.id ?? null,
      rawMode: taskFocusRawMode,
      tailLines: taskFocusTailLines,
      refreshMs: taskFocusRefreshMs,
    }
    const cadencePlan = deriveTaskFocusCadencePlan(taskFocusCadenceRef.current, nextCadence)
    taskFocusCadenceRef.current = nextCadence
    if (!cadencePlan.active || !selectedTask) return
    if (cadencePlan.immediate) {
      void requestTaskTail(buildTaskFocusCadenceRequest(nextCadence))
    }
    const interval = setInterval(() => {
      void requestTaskTail(buildTaskFocusCadenceRequest(nextCadence))
    }, taskFocusRefreshMs)
    return () => clearInterval(interval)
  }, [requestTaskTail, selectedTask, taskFocusFollowTail, taskFocusRawMode, taskFocusTailLines, taskFocusViewOpen, taskFocusRefreshMs, tasksOpen])

  const modalStack = useReplViewModalStack({
    confirmState,
    shortcutsOpen,
    claudeChrome,
    isBreadboardProfile,
    columnWidth,
    rowCount,
    scrollbackMode: SCROLLBACK_MODE,
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
    recentSessionsOpen,
    recentSessionsStatus,
    recentSessionsError,
    recentSessionsRows,
    recentSessionsVisible,
    recentSessionsIndex,
    recentSessionsScroll,
    recentSessionsMaxScroll,
    recentSessionsViewportRows,
    recentSessionsAttachingId,
    refreshRecentSessions,
    attachRecentSession,
    todoScroll,
    todos,
    usageOpen,
    inspectMenu,
    inspectRawOpen,
    inspectRawScroll,
    resultDetailOpen,
    resultDetailScroll,
    resultDetailMaxScroll,
    resultDetailViewportRows,
    resultDetailVisible,
    resultDetailSelectedTitle: activeTranscriptToolTarget?.title ?? null,
    resultDetailArtifactPath: activeTranscriptToolTarget?.artifactPath ?? null,
    artifactPreviewOpen,
    artifactPreviewScroll,
    artifactPreviewMaxScroll,
    artifactPreviewViewportRows,
    artifactPreviewVisible,
    artifactPreviewPath,
    artifactPreviewNotice,
    collapsedDetailOpen,
    collapsedDetailScroll,
    collapsedDetailMaxScroll,
    collapsedDetailViewportRows,
    collapsedDetailVisible,
    collapsedDetailSelectedId: selectedCollapsedEntry?.id ?? null,
    tasks,
    tasksOpen,
    taskFocusViewOpen,
    taskFocusFollowTail,
    taskFocusRawMode,
    taskFocusTailLines,
    taskFocusMode,
    taskFocusLaneId,
    taskFocusLaneLabel,
    taskActionsEnabled,
    taskScroll,
    taskSearchQuery,
    taskStatusFilter,
    taskLaneFilter,
    taskLaneFilterLabel,
    taskGroupMode,
    taskCollapsedGroupKeys,
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
    liveShellOwnershipMode,
    liveShellRendererHost,
    liveShellSceneStrategy,
    sessionId,
    status,
    pendingResponse,
    disconnected,
    stats,
    scrollbackMode: SCROLLBACK_MODE,
    staticFeed,
    composerRowsAboveCursor,
    managedViewportRowsAboveCursor,
    managedViewportResetKey,
    volatileActiveBand,
    conversationCount: conversation.length,
    transcriptViewerOpen,
    transcriptViewerRawMode,
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
