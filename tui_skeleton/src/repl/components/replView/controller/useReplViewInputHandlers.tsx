import { useCallback, useEffect, useMemo } from "react"
import { getSessionDraft, updateSessionDraft } from "../../../../cache/sessionCache.js"
import { parseAtCommand } from "../features/filePicker/atCommands.js"
import { useFilePickerController } from "../features/filePicker/useFilePickerController.js"
import { useReplCommands } from "./useReplCommands.js"
import { useEditorKeys } from "./keyHandlers/useEditorKeys.js"
import { useOverlayKeys } from "./keyHandlers/useOverlayKeys.js"
import { usePaletteKeys } from "./keyHandlers/usePaletteKeys.js"

type InputHandlersContext = Record<string, any>

export const useReplViewInputHandlers = (context: InputHandlersContext) => {
  const {
    input,
    cursor,
    configPath,
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
    doubleCtrlWindowMs,
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
    modelVisibleRows,
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
    setTaskFocusLaneId,
    setTaskFocusViewOpen,
    setTaskFocusFollowTail,
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
    skillsVisibleRows,
    stdout,
    taskMaxScroll,
    taskRows,
    taskLaneOrder,
    taskScroll,
    taskSearchQuery,
    taskStatusFilter,
    taskFocusLaneId,
    taskFocusViewOpen,
    taskFocusFollowTail,
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
    selectedTask,
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
  } = context

  const activeAtCommand = useMemo(() => {
    if (inputLocked) return false
    const command = parseAtCommand(input)
    return Boolean(command)
  }, [input, inputLocked])

  const filePickerController = useFilePickerController({
    input,
    cursor,
    inputLocked,
    inputTextVersion,
    claudeChrome,
    rowCount,
    activeAtCommand,
    filePickerConfig,
    filePickerResources,
    fileMentionConfig,
    onListFiles,
    handleLineEdit,
  })

  useEffect(() => {
    if (!sessionId) return
    let cancelled = false
    const seq = ++draftLoadSeq.current
    draftAppliedRef.current = false
    void (async () => {
      try {
        const draft = await getSessionDraft(sessionId)
        if (cancelled || seq !== draftLoadSeq.current) return
        if (!draft || draftAppliedRef.current) return
        if (inputValueRef.current.trim().length > 0) return
        const nextCursor = Math.max(0, Math.min(draft.cursor, draft.text.length))
        handleLineEdit(draft.text, nextCursor)
        draftAppliedRef.current = true
      } catch {
        // ignore draft load errors
      }
    })()
    return () => {
      cancelled = true
    }
  }, [handleLineEdit, inputValueRef, sessionId, draftLoadSeq, draftAppliedRef])

  useEffect(() => {
    if (!sessionId) return
    if (draftSaveTimerRef.current) {
      clearTimeout(draftSaveTimerRef.current)
      draftSaveTimerRef.current = null
    }
    const text = inputValueRef.current
    const cursorNow = Math.max(0, Math.min(cursor, text.length))
    draftSaveTimerRef.current = setTimeout(() => {
      if (!sessionId) return
      const trimmed = text.trim()
      if (!trimmed) {
        if (lastSavedDraftRef.current) {
          lastSavedDraftRef.current = null
          void updateSessionDraft(sessionId, null)
        }
        return
      }
      const prev = lastSavedDraftRef.current
      if (prev && prev.text === text && prev.cursor === cursorNow) return
      lastSavedDraftRef.current = { text, cursor: cursorNow }
      void updateSessionDraft(sessionId, {
        text,
        cursor: cursorNow,
        updatedAt: new Date().toISOString(),
      })
    }, 450)
    return () => {
      if (draftSaveTimerRef.current) {
        clearTimeout(draftSaveTimerRef.current)
        draftSaveTimerRef.current = null
      }
    }
  }, [cursor, input, inputValueRef, sessionId, draftSaveTimerRef, lastSavedDraftRef])

  useEffect(() => {
    const active = Boolean(permissionRequest)
    const wasActive = permissionActiveRef.current
    if (active && !wasActive) {
      permissionInputSnapshotRef.current = { value: inputValueRef.current, cursor }
    }
    if (!active && wasActive) {
      const snapshot = permissionInputSnapshotRef.current
      if (snapshot) {
        handleLineEdit(snapshot.value, snapshot.cursor)
      }
      permissionInputSnapshotRef.current = null
      if (permissionNote) {
        setPermissionNote("")
        setPermissionNoteCursor(0)
      }
    }
    permissionActiveRef.current = active
  }, [cursor, handleLineEdit, inputValueRef, permissionNote, permissionRequest, setPermissionNote, setPermissionNoteCursor, permissionActiveRef, permissionInputSnapshotRef])

  const { applySuggestion, applyPaletteItem, handleAtCommand, handleLineSubmit } = useReplCommands({
    configPath,
    closeFilePicker: filePickerController.closeFilePicker,
    fileMentionConfig,
    handleLineEdit,
    onListFiles,
    onReadFile,
    pushCommandResult,
    setSuggestIndex,
    input,
    cursor,
    attachments,
    fileMentions: filePickerController.fileMentions,
    onSubmit,
    setTodosOpen,
    setUsageOpen,
    setTasksOpen,
    enterTranscriptViewer,
    inputLocked,
    closePalette,
  })

  const clearScreenHandler = useCallback(() => {
    if (!stdout?.isTTY) return
    try {
      stdout.write("[2J")
      stdout.write("[H")
    } catch {
      // ignore
    }
    forceRedraw((value: number) => value + 1)
  }, [forceRedraw, stdout])

  const handleOverlayKeys = useOverlayKeys({
    alwaysAllowScope: context.ALWAYS_ALLOW_SCOPE,
    applySkillsSelection,
    clearScreen: clearScreenHandler,
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
    doubleCtrlWindowMs,
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
    jumpTranscriptToLine,
    keymap,
    modelIndex,
    modelMenu,
    modelProviderFilter,
    modelProviderOrder,
    modelSearch,
    modelVisibleRows,
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
    setTaskFocusLaneId,
    setTaskFocusViewOpen,
    setTaskFocusFollowTail,
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
    skillsVisibleRows,
    stdout,
    taskMaxScroll,
    taskRows,
    taskLaneOrder,
    taskScroll,
    taskSearchQuery,
    taskStatusFilter,
    taskFocusLaneId,
    taskFocusViewOpen,
    taskFocusFollowTail,
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
    selectedTask,
    requestTaskTail,
    usageOpen,
  })

  const handleEditorKeys = useEditorKeys({
    activeAtMention: filePickerController.activeAtMention,
    applySuggestion,
    attachments,
    closeFilePicker: filePickerController.closeFilePicker,
    cursor,
    ensureFileIndexScan: filePickerController.ensureFileIndexScan,
    fileIndexItems: filePickerController.fileIndexItems,
    fileIndexMeta: filePickerController.fileIndexMeta,
    fileMenuIndex: filePickerController.fileMenuIndex,
    fileMenuMaxRows: filePickerController.fileMenuMaxRows,
    fileMenuMode: filePickerController.fileMenuMode,
    fileMenuRows: filePickerController.fileMenuRows,
    fileMenuRowsRef: filePickerController.fileMenuRowsRef,
    filePicker: filePickerController.filePicker,
    filePickerActive: filePickerController.filePickerActive,
    filePickerConfig,
    filePickerFilteredItems: filePickerController.filePickerFilteredItems,
    filePickerIndexRef: filePickerController.filePickerIndexRef,
    filePickerQueryParts: filePickerController.filePickerQueryParts,
    handleLineEdit,
    input,
    inputTextVersion,
    inputValueRef,
    insertDirectoryMention: filePickerController.insertDirectoryMention,
    insertFileMention: filePickerController.insertFileMention,
    insertResourceMention: filePickerController.insertResourceMention,
    keymap,
    loadFilePickerDirectory: filePickerController.loadFilePickerDirectory,
    modelMenu,
    moveCursorVertical,
    overlayActive: context.overlayActive,
    pendingResponse,
    pushHistoryEntry,
    queueFileMention: filePickerController.queueFileMention,
    rawFilePickerNeedle: filePickerController.rawFilePickerNeedle,
    recallHistory,
    removeLastAttachment,
    setEscPrimedAt,
    setFilePicker: filePickerController.setFilePicker,
    setFilePickerDismissed: filePickerController.setFilePickerDismissed,
    setShortcutsOpen,
    setSuggestIndex,
    setSuppressSuggestions,
    shortcutsOpenedAtRef,
    suggestIndex,
    suggestions,
  })

  const handlePaletteKeys = usePaletteKeys({
    applyPaletteItem,
    closePalette,
    ctrlCPrimedAt,
    doubleCtrlWindowMs,
    enterTranscriptViewer,
    exitTranscriptViewer,
    keymap,
    onSubmit,
    paletteItems: context.paletteItems,
    paletteState: context.paletteState,
    setCtrlCPrimedAt,
    setPaletteState: context.setPaletteState,
    setTasksOpen,
    setTodosOpen,
    transcriptViewerOpen,
  })

  return {
    filePickerController,
    applySuggestion,
    applyPaletteItem,
    handleAtCommand,
    handleLineSubmit,
    clearScreen: clearScreenHandler,
    handleOverlayKeys,
    handleEditorKeys,
    handlePaletteKeys,
  }
}
