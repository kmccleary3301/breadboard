import { useCallback } from "react"
import type { KeyHandler } from "../../../../hooks/useKeyRouter.js"
import { SLASH_COMMANDS } from "../../../../slashCommands.js"
import { rankFuzzyFileItems } from "../../../../fileRanking.js"
import { displayPathForCwd } from "../../features/filePicker/path.js"
import { longestCommonPrefix } from "../../utils/text.js"

type EditorKeyHandlerContext = Record<string, any>

type SlashSuggestionLike = {
  readonly command?: string
  readonly availability?: string
}

export type SlashEnterDecision =
  | { readonly kind: "none" }
  | { readonly kind: "allow-submit-exact" }
  | { readonly kind: "disabled-suggestion" }
  | { readonly kind: "submit-suggestion"; readonly command: string }
  | { readonly kind: "apply-suggestion"; readonly index: number }

export type EmptyInputRemovalDecision =
  | { readonly kind: "none" }
  | { readonly kind: "attachment" }
  | { readonly kind: "file-mention" }

export const resolveSlashEnterDecision = (args: {
  readonly input: string
  readonly suggestions: readonly SlashSuggestionLike[]
  readonly suggestIndex: number
  readonly hasSubmitSuggestedSlashCommand: boolean
}): SlashEnterDecision => {
  const { input, suggestions, suggestIndex, hasSubmitSuggestedSlashCommand } = args
  if (suggestions.length === 0) return { kind: "none" }
  const trimmed = input.trim()
  if (trimmed.startsWith("/")) {
    const body = trimmed.slice(1).trim()
    const [commandName] = body.split(/\s+/)
    const isExactCommand = Boolean(commandName) && SLASH_COMMANDS.some((cmd) => cmd.name === commandName)
    if (isExactCommand) {
      return { kind: "allow-submit-exact" }
    }
    const choice = suggestions[Math.max(0, Math.min(suggestIndex, suggestions.length - 1))]
    if (choice?.availability && choice.availability !== "available") {
      return { kind: "disabled-suggestion" }
    }
    if (choice?.command?.startsWith("/") && hasSubmitSuggestedSlashCommand) {
      return { kind: "submit-suggestion", command: choice.command }
    }
  }
  return { kind: "apply-suggestion", index: Math.max(0, Math.min(suggestIndex, suggestions.length - 1)) }
}

export const resolveEmptyInputRemovalDecision = (args: {
  readonly attachmentCount: number
  readonly fileMentionCount: number
  readonly inputLength: number
  readonly cursor: number
  readonly overlayActive: boolean
  readonly isBackspace: boolean
}): EmptyInputRemovalDecision => {
  if (!args.isBackspace || args.inputLength !== 0 || args.cursor !== 0 || args.overlayActive) return { kind: "none" }
  if (args.attachmentCount > 0) return { kind: "attachment" }
  if (args.fileMentionCount > 0) return { kind: "file-mention" }
  return { kind: "none" }
}

export const useEditorKeys = (context: EditorKeyHandlerContext): KeyHandler => {
  const {
    activeAtMention,
    applySuggestion,
    attachments = [],
    fileMentions = [],
    closeFilePicker,
    cursor,
    ensureFileIndexScan,
    fileIndexItems,
    fileIndexMeta,
    fileMenuIndex,
    fileMenuMaxRows,
    fileMenuMode,
    fileMenuRows,
    fileMenuRowsRef,
    filePicker,
    filePickerActive,
    filePickerConfig,
    filePickerFilteredItems,
    filePickerIndexRef,
    filePickerQueryParts,
    handleLineEdit,
    input,
    inputTextVersion,
    inputValueRef,
    insertDirectoryMention,
    insertFileMention,
    insertResourceMention,
    keymap,
    loadFilePickerDirectory,
    modelMenu,
    moveCursorVertical,
    overlayActive,
    pendingResponse,
    pushHistoryEntry,
    queueFileMention,
    rawFilePickerNeedle,
    recallHistory,
    searchHistory,
    removeLastAttachment,
    removeLastFileMention,
    setEscPrimedAt,
    setFilePicker,
    setFilePickerDismissed,
    setShortcutsOpen,
    setSuggestIndex,
    setSuppressSuggestions,
    submitSuggestedSlashCommand,
    shortcutsOpenedAtRef,
    suggestIndex,
    suggestions,
  } = context

  return useCallback<KeyHandler>(
    (char, key) => {
      const isTabKey = key.tab || (typeof char === "string" && (char.includes("\t") || char.includes("\u001b[Z")))
      const isReturnKey = key.return || char === "\r" || char === "\n"
      const lowerChar = char?.toLowerCase()
      const keyName = typeof (key as any).name === "string" ? String((key as any).name).toLowerCase() : ""
      const isCtrlR = (key.ctrl && (lowerChar === "r" || keyName === "r")) || char === "\u0012"
      const isCtrlS = (key.ctrl && (lowerChar === "s" || keyName === "s")) || char === "\u0013"
      const isQuestionMark = lowerChar === "?" || (char === "/" && key.shift)
      if (!key.ctrl && !key.meta && isQuestionMark && inputValueRef.current.trim() === "") {
        shortcutsOpenedAtRef.current = Date.now()
        setShortcutsOpen(true)
        handleLineEdit("", 0)
        return true
      }
      if (isCtrlS) {
        const stashValue = inputValueRef.current
        if (stashValue.trim().length > 0 && typeof searchHistory === "function" && searchHistory(1)) {
          return true
        }
        if (stashValue.trim().length > 0) {
          pushHistoryEntry(stashValue)
          handleLineEdit("", 0)
        }
        return true
      }
      if (isCtrlR) {
        if (typeof searchHistory === "function") {
          searchHistory(-1)
        }
        return true
      }
      const emptyInputRemoval = resolveEmptyInputRemovalDecision({
        attachmentCount: attachments.length,
        fileMentionCount: fileMentions.length,
        inputLength: inputValueRef.current.length,
        cursor,
        overlayActive: Boolean(overlayActive),
        isBackspace: Boolean(key.backspace),
      })
      if (emptyInputRemoval.kind === "attachment") {
        removeLastAttachment()
        return true
      }
      if (emptyInputRemoval.kind === "file-mention") {
        if (typeof removeLastFileMention === "function") {
          removeLastFileMention()
        }
        return true
      }
      if (modelMenu.status !== "hidden") {
        return false
      }
      if (filePickerActive) {
        const menuRows = fileMenuRowsRef.current.length > 0 ? fileMenuRowsRef.current : fileMenuRows
        if (key.escape) {
          if (pendingResponse) return false
          if (key.meta) {
            setEscPrimedAt(null)
            handleLineEdit("", 0)
            closeFilePicker()
            return true
          }
          setEscPrimedAt(Date.now())
          if (activeAtMention) {
            setFilePickerDismissed({ tokenStart: activeAtMention.start, textVersion: inputTextVersion })
          }
          closeFilePicker()
          return true
        }
        const menuStatus =
          fileMenuMode === "tree"
            ? filePicker.status
            : fileIndexMeta.status === "idle"
              ? "scanning"
              : fileIndexMeta.status
        const allowSelectionFallback =
          (isTabKey || (isReturnKey && !key.shift)) && menuRows.length === 0 && filePickerFilteredItems.length > 0
        if (menuStatus === "loading" || menuStatus === "scanning") {
          if (menuRows.length === 0 && !allowSelectionFallback) return true
        }
        if (menuStatus === "error") {
          if (isTabKey) {
            if (fileMenuMode === "tree") {
              void loadFilePickerDirectory(filePickerQueryParts.cwd)
            } else {
              ensureFileIndexScan()
            }
            return true
          }
          return true
        }
        if (key.upArrow) {
          const count = menuRows.length
          if (count > 0) {
            const baseIndex = Math.max(0, Math.min(filePickerIndexRef.current, count - 1))
            const nextIndex = (baseIndex - 1 + count) % count
            filePickerIndexRef.current = nextIndex
            setFilePicker((prev: any) => (prev.status === "hidden" ? prev : { ...prev, index: nextIndex }))
          }
          return true
        }
        if (key.downArrow) {
          const count = menuRows.length
          if (count > 0) {
            const baseIndex = Math.max(0, Math.min(filePickerIndexRef.current, count - 1))
            const nextIndex = (baseIndex + 1) % count
            filePickerIndexRef.current = nextIndex
            setFilePicker((prev: any) => (prev.status === "hidden" ? prev : { ...prev, index: nextIndex }))
          }
          return true
        }
        if (key.pageUp || key.pageDown) {
          const count = menuRows.length
          if (count > 0) {
            const baseIndex = Math.max(0, Math.min(filePickerIndexRef.current, count - 1))
            const jump = Math.max(1, Math.min(fileMenuMaxRows, count - 1))
            const delta = key.pageUp ? -jump : jump
            const nextIndex = Math.max(0, Math.min(count - 1, baseIndex + delta))
            filePickerIndexRef.current = nextIndex
            setFilePicker((prev: any) => (prev.status === "hidden" ? prev : { ...prev, index: nextIndex }))
          }
          return true
        }
        if (isTabKey || (isReturnKey && !key.shift)) {
          if (!activeAtMention) return true
          let rows = menuRows
          if (rows.length === 0) {
            const treeRows = filePickerFilteredItems.map((item: any) => ({ kind: "file" as const, item }))
            if (treeRows.length > 0) {
              rows = treeRows
            } else if (fileMenuMode !== "tree") {
              const cwd = filePickerQueryParts.cwd
              const prefix = cwd === "." ? "" : `${cwd}/`
              const candidates = prefix
                ? fileIndexItems.filter((item: any) => item.path === cwd || item.path.startsWith(prefix))
                : fileIndexItems
              const ranked = rankFuzzyFileItems(
                candidates,
                rawFilePickerNeedle,
                filePickerConfig.maxResults,
                (item: any) => displayPathForCwd(item.path, cwd),
              )
              rows = ranked.map((item: any) => ({ kind: "file" as const, item }))
            }
          }
          const count = rows.length
          if (count === 0) return true

          const resolvedIndex = Math.max(0, Math.min(filePickerIndexRef.current, count - 1))
          const current = rows[resolvedIndex]
          if (!current) return true
          if (current.kind === "resource") {
            insertResourceMention(current.resource, activeAtMention)
            closeFilePicker()
            return true
          }

          const completionCandidates = rows
            .filter((row: any) => row.kind === "file")
            .map((row: any) => {
              const item = row.item
              return item.type === "directory" ? `${item.path.replace(/\/+$/, "")}/` : item.path
            })
          const commonPrefix = longestCommonPrefix(completionCandidates)
          const rawQuery = activeAtMention.query ?? ""
          const leadingDot = rawQuery.match(/^\.\/+/)?.[0] ?? ""
          const normalizedQuery = rawQuery.replace(/^\.\/+/, "")

          if (commonPrefix && commonPrefix.length > normalizedQuery.length) {
            const tokenContentStart = activeAtMention.start + (activeAtMention.quoted ? 2 : 1)
            const afterCursor = input.slice(cursor)
            const nextQuery = `${leadingDot}${commonPrefix}`
            const nextValue = `${input.slice(0, tokenContentStart)}${nextQuery}${afterCursor}`
            handleLineEdit(nextValue, tokenContentStart + nextQuery.length)
            filePickerIndexRef.current = 0
            setFilePicker((prev: any) => (prev.status === "hidden" ? prev : { ...prev, index: 0 }))
            if (commonPrefix.endsWith("/")) {
              const nextCwd = commonPrefix.replace(/\/+$/, "") || "."
              void loadFilePickerDirectory(nextCwd)
            }
            return true
          }

          if (current.item.type === "directory") {
            insertDirectoryMention(current.item.path, activeAtMention)
            void loadFilePickerDirectory(current.item.path.replace(/\/+$/, "") || ".")
            return true
          }

          insertFileMention(current.item.path, activeAtMention)
          queueFileMention(current.item)
          closeFilePicker()
          return true
        }
        return false
      }
      if (suggestions.length > 0) {
        if (key.escape) {
          setSuggestIndex(0)
          setSuppressSuggestions(true)
          return true
        }
        if (key.downArrow) {
          setSuggestIndex((index: number) => (index + 1) % suggestions.length)
          return true
        }
        if (key.upArrow) {
          setSuggestIndex((index: number) => (index - 1 + suggestions.length) % suggestions.length)
          return true
        }
        if (isReturnKey && !key.shift) {
          const decision = resolveSlashEnterDecision({
            input: inputValueRef.current,
            suggestions,
            suggestIndex,
            hasSubmitSuggestedSlashCommand: typeof submitSuggestedSlashCommand === "function",
          })
          if (decision.kind === "allow-submit-exact") return false
          if (decision.kind === "disabled-suggestion") return true
          if (decision.kind === "submit-suggestion") {
            setSuggestIndex(0)
            setSuppressSuggestions(true)
            void submitSuggestedSlashCommand(decision.command)
            return true
          }
          const choice = suggestions[decision.kind === "apply-suggestion" ? decision.index : Math.max(0, Math.min(suggestIndex, suggestions.length - 1))]
          applySuggestion(choice)
          return true
        }
        if (isTabKey) {
          if (key.shift) {
            setSuggestIndex((index: number) => (index - 1 + suggestions.length) % suggestions.length)
            return true
          }
          const choice = suggestions[Math.max(0, Math.min(suggestIndex, suggestions.length - 1))]
          applySuggestion(choice)
          return true
        }
        return false
      }
      if (key.upArrow) {
        if (moveCursorVertical(-1)) return true
        recallHistory(-1)
        return true
      }
      if (key.downArrow) {
        if (moveCursorVertical(1)) return true
        recallHistory(1)
        return true
      }
      if (key.ctrl && lowerChar === "p" && keymap !== "claude") {
        if (inputValueRef.current.trim().length === 0) return false
        recallHistory(-1)
        return true
      }
      if (key.ctrl && lowerChar === "n") {
        recallHistory(1)
        return true
      }
      return false
    },
    [
      activeAtMention,
      attachments.length,
      applySuggestion,
      closeFilePicker,
      cursor,
      ensureFileIndexScan,
      fileIndexMeta.status,
      fileMenuIndex,
      fileMenuMaxRows,
      fileMenuRows,
      fileMenuMode,
      filePicker.status,
      filePickerActive,
      filePickerConfig.maxResults,
      filePickerFilteredItems,
      filePickerQueryParts.cwd,
      fileIndexItems,
      insertDirectoryMention,
      insertFileMention,
      insertResourceMention,
      handleLineEdit,
      inputTextVersion,
      keymap,
      loadFilePickerDirectory,
      modelMenu.status,
      moveCursorVertical,
      overlayActive,
      pendingResponse,
      pushHistoryEntry,
      queueFileMention,
      rawFilePickerNeedle,
      recallHistory,
      searchHistory,
      removeLastAttachment,
      setSuggestIndex,
      setSuppressSuggestions,
      setShortcutsOpen,
      submitSuggestedSlashCommand,
      suggestIndex,
      suggestions,
    ],
  )
}
