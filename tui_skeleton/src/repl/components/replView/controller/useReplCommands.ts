import { useCallback } from "react"
import type { SessionFileInfo } from "../../../../api/types.js"
import type { SlashCommandInfo, SlashSuggestion } from "../../../slashCommands.js"
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

// Intentionally broad while we continue to split the controller.
type CommandsContext = Record<string, any>

export const useReplCommands = (context: CommandsContext) => {
  const {
    closeFilePicker,
    configPath,
    fileMentionConfig,
    handleLineEdit,
    onListFiles,
    onReadFile,
    pushCommandResult,
    setSuggestIndex,
    input,
    cursor,
    attachments,
    fileMentions,
    onSubmit,
    setTodosOpen,
    setUsageOpen,
    setTasksOpen,
    enterTranscriptViewer,
    inputLocked,
    closePalette,
  } = context

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

  const handleLineSubmit = useCallback(
    async (value: string) => {
      const trimmed = value.trim()
      if (!trimmed || inputLocked) return
      const normalized = value.trimEnd()
      if (trimmed.startsWith("/")) {
        const [command] = trimmed.slice(1).split(/\s+/)
        if (command === "tool-display") {
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
        if (command === "todos") {
          setTodosOpen(true)
          handleLineEdit("", 0)
          return
        }
        if (command === "usage") {
          setUsageOpen(true)
          handleLineEdit("", 0)
          return
        }
        if (command === "tasks") {
          setTasksOpen(true)
          handleLineEdit("", 0)
          return
        }
        if (command === "transcript") {
          enterTranscriptViewer()
          handleLineEdit("", 0)
          return
        }
      }
      const command = parseAtCommand(value)
      if (command) {
        await handleAtCommand(command)
        return
      }
      const segments: string[] = [normalized]
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
      await onSubmit(payload, attachments)
      setSuggestIndex(0)
      handleLineEdit("", 0)
    },
    [
      attachments,
      configPath,
      enterTranscriptViewer,
      fileMentionConfig,
      fileMentions,
      handleAtCommand,
      handleLineEdit,
      inputLocked,
      onReadFile,
      onSubmit,
      pushCommandResult,
      setSuggestIndex,
      setTasksOpen,
      setTodosOpen,
      setUsageOpen,
    ],
  )

  return {
    applySuggestion,
    applyPaletteItem,
    handleAtCommand,
    handleLineSubmit,
  }
}
