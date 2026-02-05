import { useMemo } from "react"
import type { GuardrailNotice } from "../../../types.js"
import type { FileIndexMeta, FilePickerState } from "../features/filePicker/types.js"

interface ReplLayoutOptions {
  readonly rowCount: number
  readonly headerLinesLength: number
  readonly scrollbackMode: boolean
  readonly guardrailNotice?: GuardrailNotice | null
  readonly claudeChrome: boolean
  readonly pendingClaudeStatus: string | null
  readonly overlayActive: boolean
  readonly filePickerActive: boolean
  readonly filePickerStatus: FilePickerState["status"]
  readonly fileMenuMode: "tree" | "fuzzy"
  readonly fileMenuWindow: { hiddenAbove: number; hiddenBelow: number; lineCount: number }
  readonly fileMenuRowsLength: number
  readonly fileMenuNeedlePending: boolean
  readonly fileIndexMetaStatus: FileIndexMeta["status"]
  readonly fileIndexMetaTruncated: boolean
  readonly suggestionsLength: number
  readonly suggestionWindow: { hiddenAbove: number; hiddenBelow: number; lineCount: number }
  readonly hintsLength: number
  readonly attachmentsLength: number
  readonly fileMentionsLength: number
  readonly selectedFileIsLarge: boolean
}

export const useReplLayout = (options: ReplLayoutOptions) => {
  const {
    rowCount,
    headerLinesLength,
    scrollbackMode,
    guardrailNotice,
    claudeChrome,
    pendingClaudeStatus,
    overlayActive,
    filePickerActive,
    filePickerStatus,
    fileMenuMode,
    fileMenuWindow,
    fileMenuRowsLength,
    fileMenuNeedlePending,
    fileIndexMetaStatus,
    fileIndexMetaTruncated,
    suggestionsLength,
    suggestionWindow,
    hintsLength,
    attachmentsLength,
    fileMentionsLength,
    selectedFileIsLarge,
  } = options

  const headerReserveRows = useMemo(() => {
    if (scrollbackMode) return 0
    return claudeChrome ? 0 : 3
  }, [scrollbackMode, claudeChrome])

  const guardrailReserveRows = useMemo(() => {
    if (!guardrailNotice) return 0
    const expanded = Boolean(guardrailNotice.detail && guardrailNotice.expanded)
    return expanded ? 7 : 6
  }, [guardrailNotice])

  const composerReserveRows = useMemo(() => {
    const outerMargin = 1
    const promptLine = 1
    const promptRuleRows = claudeChrome ? 2 : 0
    const pendingStatusRows = claudeChrome && pendingClaudeStatus ? 1 : 0
    const suggestionRows = (() => {
      if (overlayActive) return 1
      if (filePickerActive) {
        const chromeRows = claudeChrome ? 0 : 2
        const listMargin = claudeChrome ? 0 : 1
        const base = chromeRows + listMargin
        if (fileMenuMode === "tree") {
          if (filePickerStatus === "loading" || filePickerStatus === "hidden") return base + 1
          if (filePickerStatus === "error") return base + 2
        } else {
          if (fileIndexMetaStatus === "idle" || fileIndexMetaStatus === "scanning") {
            if (fileMenuRowsLength === 0) return base + 1
          }
          if (fileIndexMetaStatus === "error" && fileMenuRowsLength === 0) return base + 2
        }
        if (fileMenuRowsLength === 0) return base + 1
        const hiddenRows = (fileMenuWindow.hiddenAbove > 0 ? 1 : 0) + (fileMenuWindow.hiddenBelow > 0 ? 1 : 0)
        const fuzzyStatusRows =
          fileMenuMode === "fuzzy"
            ? (fileIndexMetaStatus === "idle" || fileIndexMetaStatus === "scanning" ? 1 : 0) +
              (fileIndexMetaTruncated ? 1 : 0) +
              (fileMenuNeedlePending ? 1 : 0)
            : 0
        const largeHintRows = selectedFileIsLarge ? 1 : 0
        return base + fileMenuWindow.lineCount + hiddenRows + fuzzyStatusRows + largeHintRows
      }
      if (suggestionsLength === 0) return 1
      const hiddenRows =
        (suggestionWindow.hiddenAbove > 0 ? 1 : 0) + (suggestionWindow.hiddenBelow > 0 ? 1 : 0)
      return 1 + suggestionWindow.lineCount + hiddenRows
    })()
    const hintCount = overlayActive ? 0 : Math.min(4, hintsLength)
    const hintRows = overlayActive ? 0 : claudeChrome ? 1 : hintCount > 0 ? 1 + hintCount : 0
    const attachmentRows = overlayActive ? 0 : attachmentsLength > 0 ? attachmentsLength + 3 : 0
    const fileMentionRows = overlayActive ? 0 : fileMentionsLength > 0 ? fileMentionsLength + 3 : 0
    return (
      outerMargin +
      pendingStatusRows +
      promptRuleRows +
      promptLine +
      suggestionRows +
      hintRows +
      attachmentRows +
      fileMentionRows
    )
  }, [
    attachmentsLength,
    claudeChrome,
    fileMentionsLength,
    fileIndexMetaStatus,
    fileIndexMetaTruncated,
    fileMenuNeedlePending,
    fileMenuRowsLength,
    fileMenuWindow.hiddenAbove,
    fileMenuWindow.hiddenBelow,
    fileMenuWindow.lineCount,
    fileMenuMode,
    filePickerActive,
    filePickerStatus,
    hintsLength,
    overlayActive,
    pendingClaudeStatus,
    selectedFileIsLarge,
    suggestionWindow.hiddenAbove,
    suggestionWindow.hiddenBelow,
    suggestionWindow.lineCount,
    suggestionsLength,
  ])

  const overlayReserveRows = useMemo(() => {
    if (!overlayActive) return 0
    return Math.min(Math.max(10, Math.floor(rowCount * 0.55)), Math.max(0, rowCount - 8))
  }, [overlayActive, rowCount])

  const bodyTopMarginRows = 1
  const bodyBudgetRows = useMemo(() => {
    const available =
      rowCount - headerReserveRows - guardrailReserveRows - composerReserveRows - bodyTopMarginRows - overlayReserveRows
    return Math.max(0, available)
  }, [composerReserveRows, guardrailReserveRows, headerReserveRows, overlayReserveRows, rowCount])

  return {
    headerReserveRows,
    guardrailReserveRows,
    composerReserveRows,
    overlayReserveRows,
    bodyTopMarginRows,
    bodyBudgetRows,
  }
}
