import { useCallback, useEffect, useRef, useState } from "react"
import type { SessionFileInfo } from "../../../../api/types.js"
import { SLASH_COMMANDS, type SlashCommandInfo, type SlashSuggestion } from "../../../slashCommands.js"
import {
  findNearestSlashCommands,
  findSlashCommandEntry,
  resolveSlashCommandAvailability,
  validateSlashCommandArguments,
} from "../../../slashCommandRegistry.js"
import {
  clampCommandLines,
  formatListCommandLines,
  parseAtCommand,
  type AtCommandKind,
} from "../features/filePicker/atCommands.js"
import { formatSizeDetail } from "../utils/files.js"
import {
  guessFenceLang,
  makeSnippet,
  measureBytes,
  normalizeNewlines,
  normalizeSessionPath,
} from "../utils/text.js"
import { formatBytes } from "../utils/format.js"
import { GLYPHS } from "../theme.js"
import { loadToolDisplayRules } from "../../../toolDisplayConfig.js"
import { resolveComposerSubmission } from "../composer/composerSubmissionPolicy.js"

// Intentionally broad while we continue to split the controller.
type CommandsContext = Record<string, any>

interface DiffPatchSection {
  readonly file: string
  readonly lines: readonly string[]
}

interface DiffPatchHunk {
  readonly file: string
  readonly ordinal: number
  readonly lines: readonly string[]
}

const splitWorkingTreePatchSections = (patch: string): DiffPatchSection[] => {
  const lines = patch.replace(/\r\n?/g, "\n").trimEnd().split("\n")
  if (lines.length === 1 && lines[0] === "") return []
  const sections: Array<{ file: string; lines: string[] }> = []
  let current: { file: string; lines: string[] } | null = null
  const push = () => {
    if (current && current.lines.length > 0) sections.push(current)
    current = null
  }
  for (const line of lines) {
    const match = line.match(/^diff --git a\/(.+?) b\/(.+)$/)
    if (match) {
      push()
      current = { file: match[2] ?? match[1] ?? "diff", lines: [line] }
      continue
    }
    if (!current) current = { file: "diff", lines: [] }
    current.lines.push(line)
  }
  push()
  return sections
}

const collectWorkingTreeHunks = (sections: readonly DiffPatchSection[]): DiffPatchHunk[] => {
  const hunks: DiffPatchHunk[] = []
  for (const section of sections) {
    let current: string[] | null = null
    const push = () => {
      if (!current) return
      hunks.push({ file: section.file, ordinal: hunks.length + 1, lines: current })
      current = null
    }
    for (const line of section.lines) {
      if (line.startsWith("@@")) {
        push()
        current = [line]
        continue
      }
      if (current) current.push(line)
    }
    push()
  }
  return hunks
}

const resolvePatchSection = (sections: readonly DiffPatchSection[], target: string): DiffPatchSection | null => {
  const trimmed = target.trim()
  if (!trimmed) return sections[0] ?? null
  const numeric = Number(trimmed)
  if (Number.isInteger(numeric) && numeric > 0) return sections[numeric - 1] ?? null
  const lowered = trimmed.toLowerCase()
  return sections.find((section) => section.file.toLowerCase() === lowered || section.file.toLowerCase().includes(lowered)) ?? null
}

const clampDiffLines = (lines: readonly string[], maxLines: number): string[] => {
  if (lines.length <= maxLines) return [...lines]
  return [...lines.slice(0, maxLines), `… ${lines.length - maxLines} more diff line${lines.length - maxLines === 1 ? "" : "s"} omitted from preview.`]
}

export const useReplCommands = (context: CommandsContext) => {
  const {
    closeFilePicker,
    configPath,
    permissionMode,
    permissionRequest,
    permissionQueueDepth,
    permissionScope,
    fileMentionConfig,
    handleLineEdit,
    onListFiles,
    onReadFile,
    onReadWorkingTreeDiff,
    onExportWorkingTreeDiffPatch,
    onCopyWorkingTreeDiffPatch,
    pushCommandResult,
    pushHistoryEntry,
    setSuggestIndex,
    input,
    cursor,
    attachments,
    clearAttachments,
    fileMentions,
    clearFileMentions,
    onSubmit,
    setTodosOpen,
    setUsageOpen,
    setTasksOpen,
    setTaskSearchQuery,
    setTaskStatusFilter,
    setTaskLaneFilter,
    setTaskIndex,
    setTaskScroll,
    taskLaneOrder,
    selectedTask,
    setTaskFocusLaneId,
    setTaskFocusViewOpen,
    setTaskFocusFollowTail,
    setTaskFocusRawMode,
    setTaskFocusTailLines,
    taskFocusDefaultTailLines,
    taskFocusFollowTail,
    requestTaskTail,
    taskboardInputQuarantineUntilRef,
    setShortcutsOpen,
    enterTranscriptViewer,
    jumpTranscriptToAnchor,
    onListRecentSessions,
    onAttachSession,
    refreshRecentSessions,
    recentSessionsOpenedAtRef,
    setRecentSessionsOpen,
    onModelMenuOpen,
    onSkillsMenuOpen,
    inputLocked,
    pendingResponse,
    disconnected,
    closePalette,
    saveTranscriptExport,
  } = context
  const submittingPromptRef = useRef(false)
  const [queuedPrompt, setQueuedPrompt] = useState<string | null>(null)
  const queuedPromptRef = useRef<string | null>(null)

  const setQueuedPromptValue = useCallback((value: string | null) => {
    queuedPromptRef.current = value
    setQueuedPrompt(value)
  }, [])

  const dispatchPrompt = useCallback(
    async (payload: string, attachmentsForSubmit?: any) => {
      submittingPromptRef.current = true
      try {
        await onSubmit(payload, attachmentsForSubmit)
      } finally {
        submittingPromptRef.current = false
      }
    },
    [onSubmit],
  )

  useEffect(() => {
    if (pendingResponse || submittingPromptRef.current) return
    const queued = queuedPromptRef.current
    if (!queued) return
    setQueuedPromptValue(null)
    void dispatchPrompt(queued)
  }, [dispatchPrompt, pendingResponse, setQueuedPromptValue])

  const findFileMetadata = useCallback(
    async (targetPath: string): Promise<SessionFileInfo | null> => {
      try {
        const normalized = normalizeSessionPath(targetPath)
        const parent = normalized.includes("/") ? normalized.slice(0, normalized.lastIndexOf("/")) : "."
        const scope = parent === "." ? undefined : parent
        const entries = await onListFiles(scope)
        return entries.find((entry: SessionFileInfo) => normalizeSessionPath(entry.path) === normalized) ?? null
      } catch {
        return null
      }
    },
    [onListFiles],
  )

  const handleAtCommand = useCallback(
    async (command: { readonly kind: AtCommandKind; readonly argument?: string }) => {
      closeFilePicker()
      try {
        if (command.kind === "list") {
          const displayTarget =
            command.argument && command.argument.trim().length > 0 ? command.argument.trim() : "."
          const normalizedTarget = displayTarget === "." ? "." : normalizeSessionPath(displayTarget)
          const listScope = normalizedTarget === "." ? undefined : normalizedTarget
          const entries = await onListFiles(listScope)
          const headerLine = `Listing files in ${displayTarget === "." ? "workspace root" : displayTarget}:`
          const title = displayTarget === "." ? "Files" : `Files: ${displayTarget}`
          const lines = clampCommandLines(
            [headerLine, ...formatListCommandLines(entries)],
            `${GLYPHS.ellipsis}Listing truncated to keep output manageable.`,
          )
          pushCommandResult(title, lines)
          return
        }
        if (command.kind === "read") {
          if (!command.argument) {
            pushCommandResult("@read", ["Please provide a file path to read (e.g., @read src/index.ts)."])
            return
          }
          const rawArg = command.argument.trim()
          const normalizedTarget = normalizeSessionPath(rawArg)
          const metadata = await findFileMetadata(normalizedTarget)
          const preferCat =
            metadata?.size != null && metadata.size <= fileMentionConfig.maxInlineBytesPerFile
          const readOptions:
            | { mode: "cat" }
            | { mode: "snippet"; headLines: number; tailLines: number; maxBytes: number } = preferCat
            ? { mode: "cat" }
            : {
                mode: "snippet",
                headLines: fileMentionConfig.snippetHeadLines,
                tailLines: fileMentionConfig.snippetTailLines,
                maxBytes: fileMentionConfig.snippetMaxBytes,
              }
          const result = await onReadFile(normalizedTarget, readOptions)
          let content = normalizeNewlines(result.content ?? "")
          let truncated = result.truncated === true
          if (preferCat) {
            const byteCount = measureBytes(content)
            if (byteCount > fileMentionConfig.maxInlineBytesPerFile) {
              truncated = true
              content = makeSnippet(content, fileMentionConfig.snippetHeadLines, fileMentionConfig.snippetTailLines)
            }
          } else {
            truncated = true
          }
          const sizeBytes = metadata?.size ?? result.total_bytes ?? null
          const headerSize = formatSizeDetail(sizeBytes)
          const headerLine = `### File: ${rawArg}${headerSize ? ` (${headerSize})` : ""}`
          const fenceLang = guessFenceLang(normalizedTarget)
          const contentLines = content.length === 0 ? [""] : content.split("\n")
          const blockLines = [`\`\`\`${fenceLang}`, ...contentLines, "```"]
          const resultLines = [headerLine, "", ...blockLines]
          let truncatedNotice: string | undefined
          if (truncated) {
            truncatedNotice = `${GLYPHS.ellipsis}This file is truncated, as it is too large${headerSize ? ` (${headerSize})` : ""} to fully display. You may read and search through the full file using your provided tools.`
            resultLines.push("", truncatedNotice)
          }
          const finalLines = clampCommandLines(resultLines, truncatedNotice)
          pushCommandResult(`File: ${rawArg}`, finalLines)
          return
        }
      } catch (error) {
        const message = (error as Error).message || String(error)
        pushCommandResult(`@${command.kind}`, [`Error: ${message}`])
      } finally {
        setSuggestIndex(0)
        handleLineEdit("", 0)
      }
    },
    [
      closeFilePicker,
      fileMentionConfig,
      findFileMetadata,
      handleLineEdit,
      onListFiles,
      onReadFile,
      pushCommandResult,
      setSuggestIndex,
    ],
  )

  const applySuggestion = useCallback(
    (choice?: SlashSuggestion) => {
      if (!choice) return
      const beforeCursor = input.slice(0, cursor)
      const afterCursor = input.slice(cursor)
      let replaceStart = beforeCursor.lastIndexOf(" ")
      replaceStart = replaceStart === -1 ? 0 : replaceStart + 1
      if (beforeCursor[replaceStart] !== "/") {
        replaceStart = 0
      }
      const prefix = input.slice(0, replaceStart)
      const inserted = `${choice.command}${choice.usage ? ` ${choice.usage}` : ""}`
      const newValue = `${prefix}${inserted}${afterCursor}`
      const newCursor = prefix.length + inserted.length
      handleLineEdit(newValue, newCursor)
      setSuggestIndex(0)
    },
    [cursor, handleLineEdit, input, setSuggestIndex],
  )

  const applyPaletteItem = useCallback(
    (item?: SlashCommandInfo) => {
      if (!item) return
      const inserted = `/${item.name}${item.usage ? " " : " "}`
      handleLineEdit(inserted, inserted.length)
      closePalette()
    },
    [closePalette, handleLineEdit],
  )

  const openTaskFocusInspector = useCallback(
    (options: { readonly raw?: boolean; readonly follow?: boolean } = {}) => {
      const selectedLane =
        typeof selectedTask?.laneId === "string" && selectedTask.laneId.trim().length > 0
          ? selectedTask.laneId.trim()
          : Array.isArray(taskLaneOrder)
            ? taskLaneOrder.find((lane: unknown): lane is string => typeof lane === "string" && lane.trim().length > 0) ?? null
            : null
      if (!selectedLane) {
        pushCommandResult("/inspect task", [
          "No task is available to inspect yet.",
          "Run /tasks after multiagent/background work starts, then use /inspect task.",
        ])
        return false
      }
      if (taskboardInputQuarantineUntilRef && typeof taskboardInputQuarantineUntilRef === "object") {
        taskboardInputQuarantineUntilRef.current = Date.now() + 300
      }
      if (typeof setTaskSearchQuery === "function") setTaskSearchQuery("")
      if (typeof setTaskStatusFilter === "function") setTaskStatusFilter("all")
      if (typeof setTaskLaneFilter === "function") setTaskLaneFilter(selectedLane)
      if (typeof setTaskIndex === "function") setTaskIndex(0)
      if (typeof setTaskScroll === "function") setTaskScroll(0)
      if (typeof setTaskFocusLaneId === "function") setTaskFocusLaneId(selectedLane)
      if (typeof setTaskFocusFollowTail === "function") setTaskFocusFollowTail(options.follow !== false)
      if (typeof setTaskFocusRawMode === "function") setTaskFocusRawMode(Boolean(options.raw))
      if (typeof setTaskFocusTailLines === "function") setTaskFocusTailLines(taskFocusDefaultTailLines ?? 24)
      if (typeof setTasksOpen === "function") setTasksOpen(true)
      if (typeof setTaskFocusViewOpen === "function") setTaskFocusViewOpen(true)
      if (typeof requestTaskTail === "function") {
        void requestTaskTail({ raw: Boolean(options.raw), tailLines: taskFocusDefaultTailLines ?? 24 })
      }
      return true
    },
    [
      pushCommandResult,
      requestTaskTail,
      selectedTask,
      setTaskFocusFollowTail,
      setTaskFocusLaneId,
      setTaskFocusRawMode,
      setTaskFocusTailLines,
      setTaskFocusViewOpen,
      setTaskIndex,
      setTaskLaneFilter,
      setTaskScroll,
      setTaskSearchQuery,
      setTaskStatusFilter,
      setTasksOpen,
      taskFocusDefaultTailLines,
      taskLaneOrder,
      taskboardInputQuarantineUntilRef,
    ],
  )

  const handleLineSubmit = useCallback(
    async (value: string) => {
      const plan = resolveComposerSubmission(value)
      const trimmed = plan.normalized.trim()
      if (plan.kind === "empty" || inputLocked || submittingPromptRef.current) return
      if (plan.kind === "slash") {
        const [command, ...commandArgs] = trimmed.slice(1).split(/\s+/)
        const knownCommand = findSlashCommandEntry(command)
        const stageSlashHistory = () => {
          if (knownCommand?.historyBehavior === "record" && typeof pushHistoryEntry === "function") {
            pushHistoryEntry(trimmed)
          }
        }
        if (knownCommand) {
          const availability = resolveSlashCommandAvailability(knownCommand, { pendingResponse, disconnected })
          if (availability.availability !== "available") {
            stageSlashHistory()
            pushCommandResult(`/${knownCommand.name}`, [
              availability.disabledReason ?? `Command is ${availability.availability}.`,
              `Status: ${availability.availability}.`,
              "Type /help to view available commands.",
            ])
            handleLineEdit("", 0)
            return
          }
          const argumentValidation = validateSlashCommandArguments(knownCommand, commandArgs)
          if (!argumentValidation.ok) {
            pushCommandResult(`/${knownCommand.name}`, [...(argumentValidation.errorLines ?? [`Invalid arguments for /${knownCommand.name}.`])])
            handleLineEdit("", 0)
            return
          }
        }
        if (command === "debug-config" && knownCommand?.dispatchKind === "debug-local") {
          pushCommandResult("/debug-config", [
            `config: ${configPath ?? "(default)"}`,
            `engine mode: ${process.env.BREADBOARD_ENGINE_MODE ?? "(default)"}`,
            `api: ${process.env.BREADBOARD_API_URL ?? "(default)"}`,
            `pending response: ${pendingResponse ? "yes" : "no"}`,
          ])
          handleLineEdit("", 0)
          return
        }
        if (command === "permissions") {
          stageSlashHistory()
          pushCommandResult("/permissions", [
            "Permission status (read-only)",
            `Config: ${configPath ?? "(default)"}`,
            `Launch permission mode: ${permissionMode ? String(permissionMode) : "(not specified)"}`,
            `Engine mode: ${process.env.BREADBOARD_ENGINE_MODE ?? "(default)"}`,
            `Active approval: ${permissionRequest ? "yes" : "none"}`,
            `Approval queue: ${Number(permissionQueueDepth ?? 0)}`,
            `Current approval scope: ${permissionScope ? String(permissionScope) : "project"}`,
            "Policy editing: not productized in this TUI yet.",
            "This command does not change permission policy.",
          ])
          handleLineEdit("", 0)
          return
        }
        if (command === "diff") {
          stageSlashHistory()
          const diffAction = commandArgs[0]?.toLowerCase()
          const diffTarget = commandArgs.slice(1).join(" ").trim()
          if (diffAction === "export") {
            if (typeof onExportWorkingTreeDiffPatch !== "function") {
              pushCommandResult("/diff export", ["Working-tree patch export is unavailable in this view."])
            } else {
              const result = await onExportWorkingTreeDiffPatch(diffTarget || null)
              pushCommandResult(
                "/diff export",
                result.ok
                  ? [
                    "Working-tree patch exported.",
                    `Path: ${result.path}`,
                    `Bytes: ${result.bytes ?? 0}`,
                    "Review/approval actions: read-only; exporting a patch does not approve or apply changes.",
                  ]
                  : ["Working-tree patch export failed.", result.error ?? "Unknown export error."],
              )
            }
            handleLineEdit("", 0)
            return
          }
          if (diffAction === "copy") {
            if (typeof onCopyWorkingTreeDiffPatch !== "function") {
              pushCommandResult("/diff copy", ["Working-tree patch copy is unavailable in this view."])
            } else {
              const result = await onCopyWorkingTreeDiffPatch()
              pushCommandResult(
                "/diff copy",
                result.ok
                  ? [
                    "Working-tree patch copied.",
                    `Method: ${result.method}`,
                    `Bytes: ${result.bytes ?? 0}`,
                    "Review/approval actions: read-only; copying a patch does not approve or apply changes.",
                  ]
                  : ["Working-tree patch copy failed.", result.error ?? "Unknown clipboard error."],
              )
            }
            handleLineEdit("", 0)
            return
          }
          const workingTree = typeof onReadWorkingTreeDiff === "function" ? await onReadWorkingTreeDiff() : null
          if (workingTree?.kind === "dirty") {
            const patchSections = splitWorkingTreePatchSections(workingTree.patch ?? "")
            if (diffAction === "file") {
              const section = resolvePatchSection(patchSections, diffTarget)
              pushCommandResult(
                "/diff file",
                section
                  ? [
                    `Working-tree diff file ${patchSections.indexOf(section) + 1}/${patchSections.length}: ${section.file}`,
                    "```diff",
                    ...clampDiffLines(section.lines, 100),
                    "```",
                    "Navigation: /diff file <n|path> · /diff hunk <n> · /diff export [path] · /diff copy",
                  ]
                  : [
                    `No tracked patch section matched "${diffTarget || "1"}".`,
                    `Available files: ${patchSections.map((section, index) => `${index + 1}:${section.file}`).join(", ") || "(none)"}`,
                  ],
              )
              handleLineEdit("", 0)
              return
            }
            if (diffAction === "hunk") {
              const hunks = collectWorkingTreeHunks(patchSections)
              const numeric = Number(diffTarget || "1")
              const hunk = Number.isInteger(numeric) && numeric > 0 ? hunks[numeric - 1] : null
              pushCommandResult(
                "/diff hunk",
                hunk
                  ? [
                    `Working-tree diff hunk ${hunk.ordinal}/${hunks.length}: ${hunk.file}`,
                    "```diff",
                    ...clampDiffLines(hunk.lines, 100),
                    "```",
                    "Navigation: /diff file <n|path> · /diff hunk <n> · /diff export [path] · /diff copy",
                  ]
                  : [
                    `No hunk matched "${diffTarget || "1"}".`,
                    `Available hunks: ${hunks.map((hunk) => `${hunk.ordinal}:${hunk.file}`).join(", ") || "(none)"}`,
                  ],
              )
              handleLineEdit("", 0)
              return
            }
            const fileLines = workingTree.changedFiles.slice(0, 12).map((file: { readonly path: string; readonly status: string; readonly staged: boolean; readonly unstaged: boolean; readonly untracked: boolean }) => {
              const flags = [
                file.staged ? "staged" : null,
                file.unstaged ? "unstaged" : null,
                file.untracked ? "untracked" : null,
              ].filter(Boolean).join(", ")
              return `${GLYPHS.bullet} ${file.status} ${file.path}${flags ? ` (${flags})` : ""}`
            })
            const patchLines = workingTree.patch
              ? ["", "Unified diff preview:", "```diff", ...workingTree.patch.split("\n").slice(0, 80), "```"]
              : ["", "Unified diff preview: unavailable for current dirty state."]
            const hiddenFiles = Math.max(0, workingTree.changedFiles.length - fileLines.length)
            const hiddenPatchLines = workingTree.patch ? Math.max(0, workingTree.patch.split("\n").length - 80) : 0
            pushCommandResult("/diff", [
              "Working-tree diff (read-only)",
              `Repo: ${workingTree.repoRoot ?? workingTree.workspace}`,
              `Dirty state: ${workingTree.changedFiles.length} file${workingTree.changedFiles.length === 1 ? "" : "s"} · +${workingTree.additions}/-${workingTree.deletions}`,
              "Changed files:",
              ...fileLines,
              ...(hiddenFiles > 0 ? [`… ${hiddenFiles} more file${hiddenFiles === 1 ? "" : "s"}`] : []),
              ...workingTree.warnings.map((warning: string) => `Warning: ${warning}`),
              ...patchLines,
              ...(hiddenPatchLines > 0 ? [`… ${hiddenPatchLines} more diff line${hiddenPatchLines === 1 ? "" : "s"} omitted from preview.`] : []),
              `Navigation: ${patchSections.length > 0 ? `/diff file 1-${patchSections.length}` : "/diff file unavailable"} · /diff hunk <n> · /diff export [path] · /diff copy`,
              "Review/approval actions: read-only in this TUI surface; no policy or approval state changed.",
            ])
            handleLineEdit("", 0)
            return
          }
          if (workingTree?.kind === "clean") {
            pushCommandResult("/diff", [
              "Working-tree diff (read-only)",
              `Repo: ${workingTree.repoRoot ?? workingTree.workspace}`,
              "Working tree clean.",
              "No patch preview to show.",
            ])
            handleLineEdit("", 0)
            return
          }
          if (workingTree?.kind === "not-git") {
            pushCommandResult("/diff", [
              "Working-tree diff unavailable.",
              ...workingTree.warnings,
              "Falling back to transcript diff, if available.",
            ])
          } else if (workingTree?.kind === "error") {
            pushCommandResult("/diff", [
              "Working-tree diff failed.",
              workingTree.error ?? "Unknown git error.",
              "Falling back to transcript diff, if available.",
            ])
          }
          const hasDiff = typeof jumpTranscriptToAnchor === "function" ? jumpTranscriptToAnchor("diff") : false
          if (!hasDiff) {
            pushCommandResult("/diff", [
              "No transcript diff found.",
              "Make a git-tracked edit in this workspace or run a coding/editing turn that emits a patch, then try /diff again.",
              "Diff approval workflow remains unclaimed until the full viewer/approval surface is productized.",
            ])
            handleLineEdit("", 0)
            return
          }
          enterTranscriptViewer()
          queueMicrotask(() => {
            try {
              jumpTranscriptToAnchor("diff")
            } catch {
              // Best-effort positioning; the transcript viewer itself is the product surface.
            }
          })
          handleLineEdit("", 0)
          return
        }
        if (command === "help") {
          const lines = SLASH_COMMANDS.map((entry) =>
            `${GLYPHS.bullet} /${entry.name}${entry.usage ? ` ${entry.usage}` : ""} — ${entry.summary}`,
          )
          pushCommandResult("Slash commands", lines)
          handleLineEdit("", 0)
          return
        }
        if (command === "tool-display") {
          stageSlashHistory()
          const parts = trimmed.split(/\s+/).slice(1)
          const subcommand = parts[0] ?? "list"
          if (subcommand !== "list") {
            pushCommandResult("/tool-display", ["Usage: /tool-display list"])
            handleLineEdit("", 0)
            return
          }
          const rules = await loadToolDisplayRules(configPath ?? undefined)
          if (!rules.length) {
            pushCommandResult("/tool-display", ["No tool display rules found."])
            handleLineEdit("", 0)
            return
          }
          const lines = rules.map((rule) => {
            const match = rule.match ? JSON.stringify(rule.match) : "match: *"
            const priority = rule.priority != null ? `p${rule.priority}` : "p?"
            const category = rule.category ? ` · ${rule.category}` : ""
            return `${GLYPHS.bullet} ${rule.id} (${priority}) ${match}${category}`
          })
          pushCommandResult("Tool display rules", lines)
          handleLineEdit("", 0)
          return
        }
        if (command === "files") {
          stageSlashHistory()
          const argument = commandArgs.join(" ").trim()
          await handleAtCommand({ kind: "list", argument: argument || undefined })
          return
        }
        if (command === "todos") {
          stageSlashHistory()
          setTodosOpen(true)
          handleLineEdit("", 0)
          return
        }
        if (command === "usage") {
          stageSlashHistory()
          setUsageOpen(true)
          handleLineEdit("", 0)
          return
        }
        if (command === "tasks" || command === "agents") {
          stageSlashHistory()
          if (taskboardInputQuarantineUntilRef && typeof taskboardInputQuarantineUntilRef === "object") {
            taskboardInputQuarantineUntilRef.current = Date.now() + 300
          }
          if (typeof setTaskSearchQuery === "function") setTaskSearchQuery("")
          if (typeof setTaskStatusFilter === "function") setTaskStatusFilter("all")
          if (typeof setTaskLaneFilter === "function") setTaskLaneFilter("all")
          if (typeof setTaskIndex === "function") setTaskIndex(0)
          if (typeof setTaskScroll === "function") setTaskScroll(0)
          setTasksOpen(true)
          handleLineEdit("", 0)
          return
        }
        if (command === "inspect" && commandArgs[0]?.toLowerCase() === "task") {
          stageSlashHistory()
          openTaskFocusInspector({ raw: false, follow: true })
          handleLineEdit("", 0)
          return
        }
        if (command === "follow" && commandArgs[0]?.toLowerCase() === "task") {
          stageSlashHistory()
          const taskAction = (commandArgs[1] ?? "status").toLowerCase()
          if (!["status", "pause", "resume"].includes(taskAction)) {
            pushCommandResult("/follow task", ["Usage: /follow task [status|pause|resume]"])
            handleLineEdit("", 0)
            return
          }
          if (taskAction === "status") {
            const followState = taskFocusFollowTail ? "resumed" : "paused"
            pushCommandResult("/follow task", [
              `Task Focus follow: ${followState}.`,
              "Scope: local Task Focus tail only; main transcript follow is controlled by /follow status|pause|resume.",
            ])
            handleLineEdit("", 0)
            return
          }
          const followLive = taskAction === "resume"
          if (typeof setTaskFocusFollowTail === "function") setTaskFocusFollowTail(followLive)
          openTaskFocusInspector({ raw: false, follow: followLive })
          pushCommandResult("/follow task", [
            `Task Focus follow ${followLive ? "resumed" : "paused"}.`,
            "Scope: local Task Focus tail only; no task state mutation or model submission occurred.",
          ])
          handleLineEdit("", 0)
          return
        }
        if (command === "shortcuts") {
          stageSlashHistory()
          if (typeof setShortcutsOpen === "function") {
            setShortcutsOpen(true)
          } else {
            pushCommandResult("/shortcuts", ["Shortcuts overlay is unavailable in this view."])
          }
          handleLineEdit("", 0)
          return
        }
        if (command === "transcript") {
          stageSlashHistory()
          enterTranscriptViewer()
          handleLineEdit("", 0)
          return
        }
        if (command === "raw") {
          stageSlashHistory()
          enterTranscriptViewer({ raw: true })
          handleLineEdit("", 0)
          return
        }
        if (command === "copy") {
          stageSlashHistory()
          if (typeof saveTranscriptExport === "function") {
            await saveTranscriptExport()
          } else {
            pushCommandResult("/copy", ["Transcript export is unavailable in this view."])
          }
          handleLineEdit("", 0)
          return
        }
        if (command === "attach" || command === "mention") {
          stageSlashHistory()
          handleLineEdit("@", 1)
          setSuggestIndex(0)
          return
        }
        if (command === "sessions") {
          stageSlashHistory()
          if (typeof setRecentSessionsOpen === "function") {
            if (recentSessionsOpenedAtRef) {
              recentSessionsOpenedAtRef.current = Date.now()
            }
            setRecentSessionsOpen(true)
            try {
              if (typeof refreshRecentSessions === "function") {
                await refreshRecentSessions()
              } else if (typeof onListRecentSessions === "function") {
                await onListRecentSessions()
              }
            } catch {
              // controller-owned fetch state surfaces the error
            }
          } else if (typeof onAttachSession === "function") {
            pushCommandResult(`/${command}`, ["Recent sessions overlay is unavailable in this view."])
          }
          handleLineEdit("", 0)
          return
        }
        if (command === "resume" && !disconnected) {
          stageSlashHistory()
          if (typeof setRecentSessionsOpen === "function") {
            if (recentSessionsOpenedAtRef) {
              recentSessionsOpenedAtRef.current = Date.now()
            }
            setRecentSessionsOpen(true)
            try {
              if (typeof refreshRecentSessions === "function") {
                await refreshRecentSessions()
              } else if (typeof onListRecentSessions === "function") {
                await onListRecentSessions()
              }
            } catch {
              // controller-owned fetch state surfaces the error
            }
          } else {
            pushCommandResult("/resume", [
              "Session is already active.",
              "Recent sessions overlay is unavailable in this view. Use /sessions where supported.",
            ])
          }
          handleLineEdit("", 0)
          return
        }
        if (command === "models") {
          stageSlashHistory()
          if (typeof onModelMenuOpen === "function") {
            await onModelMenuOpen()
          } else {
            pushCommandResult("/models", ["Model picker is unavailable in this view."])
          }
          handleLineEdit("", 0)
          return
        }
        if (command === "skills") {
          stageSlashHistory()
          if (typeof onSkillsMenuOpen === "function") {
            await onSkillsMenuOpen()
          } else {
            pushCommandResult("/skills", ["Skills menu is unavailable in this view."])
          }
          handleLineEdit("", 0)
          return
        }
        if (!knownCommand) {
          const nearest = findNearestSlashCommands(command, 3)
          const lines = nearest.length > 0
            ? [
                `Unknown slash command: /${command}`,
                `Did you mean ${nearest.map((entry) => `/${entry.name}`).join(", ")}?`,
              ]
            : [`Unknown slash command: /${command}`, "Type /help to view available commands."]
          pushCommandResult("Unknown command", lines)
          handleLineEdit("", 0)
          return
        }
        if (knownCommand.dispatchKind === "controller") {
          stageSlashHistory()
          handleLineEdit("", 0)
          await onSubmit(trimmed)
          return
        }
        if (knownCommand.dispatchKind === "deferred") {
          stageSlashHistory()
          pushCommandResult(`/${knownCommand.name}`, [
            knownCommand.disabledReason ?? `/${knownCommand.name} is deferred and not currently executable.`,
            "Status: deferred.",
            "Type /help to view available commands.",
          ])
          handleLineEdit("", 0)
          return
        }
      }
      const command = parseAtCommand(value)
      if (command) {
        await handleAtCommand(command)
        return
      }
      const segments: string[] = [plan.payload]
      if (attachments.length > 0) {
        const summaryLines = attachments.map(
          (attachment: any, index: number) =>
            `[Attachment ${index + 1}: ${attachment.mime} ${formatBytes(attachment.size)}]`,
        )
        segments.push(summaryLines.join("\n"))
      }
      if (fileMentions.length > 0 && fileMentionConfig.mode !== "reference") {
        let remainingInlineBudget = fileMentionConfig.maxInlineBytesTotal
        const blocks: string[] = []
        for (const entry of fileMentions) {
          if (entry.requestedMode === "reference") continue
          const sizeBytes = entry.size ?? null
          const shouldInline =
            entry.requestedMode === "inline"
              ? sizeBytes == null ||
                (sizeBytes <= fileMentionConfig.maxInlineBytesPerFile && sizeBytes <= remainingInlineBudget)
              : sizeBytes != null &&
                sizeBytes <= fileMentionConfig.maxInlineBytesPerFile &&
                sizeBytes <= remainingInlineBudget

          const fetchMode: "cat" | "snippet" = shouldInline ? "cat" : "snippet"
          let content = ""
          let truncated = fetchMode === "snippet"
          try {
            const result = await onReadFile(
              entry.path,
              fetchMode === "cat"
                ? { mode: "cat" }
                : {
                    mode: "snippet",
                    headLines: fileMentionConfig.snippetHeadLines,
                    tailLines: fileMentionConfig.snippetTailLines,
                    maxBytes: fileMentionConfig.snippetMaxBytes,
                  },
            )
            content = normalizeNewlines(result.content ?? "")
            truncated = result.truncated === true || fetchMode === "snippet"
            const byteCount = measureBytes(content)
            if (fetchMode === "cat" && byteCount > fileMentionConfig.maxInlineBytesPerFile) {
              truncated = true
              content = makeSnippet(content, fileMentionConfig.snippetHeadLines, fileMentionConfig.snippetTailLines)
            }
          } catch (error) {
            const message = (error as Error).message || String(error)
            blocks.push(`File ${entry.path}: Error: ${message}`)
            continue
          }
          const sizeBytesFinal = entry.size ?? null
          const headerSize = formatSizeDetail(sizeBytesFinal)
          const fenceLang = guessFenceLang(entry.path)
          const contentLines = content.length === 0 ? [""] : content.split("\n")
          const blockLines = [`\`\`\`${fenceLang}`, ...contentLines, "```"]
          const headerLine = `### File: ${entry.path}${headerSize ? ` (${headerSize})` : ""}`
          blocks.push(headerLine, "", ...blockLines)
          if (truncated) {
            blocks.push(
              "",
              `${GLYPHS.ellipsis}This file is truncated, as it is too large${headerSize ? ` (${headerSize})` : ""} to fully display. You may read and search through the full file using your provided tools.`,
            )
          }
          blocks.push("")
          remainingInlineBudget = Math.max(0, remainingInlineBudget - (sizeBytesFinal ?? 0))
        }
        if (blocks.length > 0) {
          segments.push(blocks.join("\n"))
        }
      }
      const payload = segments.join("\n\n")
      setSuggestIndex(0)
      if (pendingResponse && attachments.length > 0) {
        pushCommandResult("Queued prompt", [
          "Prompts with queued attachments cannot be queued while another response is running.",
          "Wait for the current response to finish, then submit again so attachments are uploaded with that turn.",
        ])
        return
      }
      if (typeof pushHistoryEntry === "function") {
        pushHistoryEntry(plan.normalized)
      }
      handleLineEdit("", 0)
      if (pendingResponse) {
        if (fileMentions.length > 0 && typeof clearFileMentions === "function") {
          clearFileMentions()
        }
        setQueuedPromptValue(payload)
        return
      }
      const attachmentsForSubmit = attachments.length > 0 ? [...attachments] : attachments
      if (attachmentsForSubmit.length > 0 && typeof clearAttachments === "function") {
        clearAttachments()
      }
      if (fileMentions.length > 0 && typeof clearFileMentions === "function") {
        clearFileMentions()
      }
      await dispatchPrompt(payload, attachmentsForSubmit)
    },
    [
      attachments,
      clearAttachments,
      clearFileMentions,
      configPath,
      permissionMode,
      permissionRequest,
      permissionQueueDepth,
      permissionScope,
      enterTranscriptViewer,
      jumpTranscriptToAnchor,
      saveTranscriptExport,
      onModelMenuOpen,
      onSkillsMenuOpen,
      fileMentionConfig,
      fileMentions,
      handleAtCommand,
      handleLineEdit,
      inputLocked,
      onReadFile,
      onListRecentSessions,
      onAttachSession,
      recentSessionsOpenedAtRef,
      pendingResponse,
      disconnected,
      pushCommandResult,
      pushHistoryEntry,
      dispatchPrompt,
      setQueuedPromptValue,
      setRecentSessionsOpen,
      setSuggestIndex,
      setTaskIndex,
      setTaskLaneFilter,
      setTaskScroll,
      setTaskSearchQuery,
      setTaskStatusFilter,
      setTaskFocusFollowTail,
      taskFocusFollowTail,
      setTasksOpen,
      setTodosOpen,
      setUsageOpen,
      taskboardInputQuarantineUntilRef,
      openTaskFocusInspector,
    ],
  )

  return {
    applySuggestion,
    applyPaletteItem,
    handleAtCommand,
    handleLineSubmit,
    queuedPrompt,
  }
}
