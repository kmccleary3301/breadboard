import { useCallback } from "react"
import type { KeyHandler } from "../../../../hooks/useKeyRouter.js"
import { matchesActionBinding, normalizeKeyProfile } from "../../keybindings/actionKeymap.js"

type GlobalKeyHandlerContext = Record<string, any>

export const useGlobalKeys = (context: GlobalKeyHandlerContext): KeyHandler => {
  const {
    clearScreen,
    closePalette,
    confirmState,
    ctrlCPrimedAt,
    ctreeOpen,
    cycleCollapsibleSelection,
    doubleCtrlWindowMs,
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
    scrollbackMode,
    setCtrlCPrimedAt,
    setCtreeOpen,
    setEscPrimedAt,
    setTasksOpen,
    setTodosOpen,
    taskboardDefaultView,
    setTranscriptNudge,
    setVerboseOutput,
    skillsMenu,
    toggleSelectedCollapsibleEntry,
    transcriptViewerBodyRows,
    transcriptViewerOpen,
  } = context

  return useCallback<KeyHandler>(
    (char, key) => {
      const lowerChar = char?.toLowerCase()
      const profile = normalizeKeyProfile(keymap)
      const isEscapeKey = key.escape || char === "\u001b"
      const isCtrlT = matchesActionBinding(profile, "toggle_todos_panel", char, key)
      const isCtrlShiftT = matchesActionBinding(profile, "toggle_transcript_viewer", char, key)
      const isCtrlB = matchesActionBinding(profile, "toggle_tasks_panel", char, key)
      const isCtrlY = matchesActionBinding(profile, "toggle_ctree_panel", char, key) || char === "\u0019"
      const isCtrlG = matchesActionBinding(profile, "toggle_skills_panel", char, key) || char === "\u0007"
      const isCtrlU = (key.ctrl && lowerChar === "u") || char === "\u0015"
      if (isCtrlShiftT) {
        setCtreeOpen(false)
        if (transcriptViewerOpen) {
          exitTranscriptViewer()
        } else {
          enterTranscriptViewer()
        }
        return true
      }
      if (isCtrlT) {
        setCtreeOpen(false)
        if (keymap === "claude") {
          setTodosOpen((prev: boolean) => !prev)
        } else if (transcriptViewerOpen) {
          exitTranscriptViewer()
        } else {
          enterTranscriptViewer()
        }
        return true
      }
      if (isCtrlB) {
        setCtreeOpen(false)
        if (taskboardDefaultView === "todos") {
          setTasksOpen(false)
          setTodosOpen((prev: boolean) => !prev)
          return true
        }
        if (taskboardDefaultView === "combined") {
          setTasksOpen((prev: boolean) => {
            const next = !prev
            setTodosOpen(next)
            return next
          })
          return true
        }
        setTodosOpen(false)
        setTasksOpen((prev: boolean) => !prev)
        return true
      }
      if (isCtrlY) {
        if (!ctreeOpen) {
          setTodosOpen(false)
          setTasksOpen(false)
        }
        setCtreeOpen((prev: boolean) => !prev)
        return true
      }
      if (key.ctrl && lowerChar === "r") {
        if (rewindMenu.status === "hidden") {
          void onSubmit("/rewind")
        } else {
          onRewindClose()
        }
        return true
      }
      if (matchesActionBinding(profile, "clear_screen", char, key)) {
        clearScreen()
        return true
      }
      if ((key.ctrl && lowerChar === "o") || char === "\u000f") {
        if (keymap === "claude") {
          if (transcriptViewerOpen) {
            exitTranscriptViewer()
          } else {
            enterTranscriptViewer()
          }
        } else {
          setVerboseOutput((prev: boolean) => {
            const next = !prev
            pushCommandResult("Detailed transcript", [next ? "ON" : "OFF"])
            return next
          })
        }
        return true
      }
      if (!scrollbackMode && !overlayActive && !transcriptViewerOpen) {
        if (key.pageUp) {
          setTranscriptNudge((prev: number) => prev + transcriptViewerBodyRows)
          return true
        }
        if (key.pageDown) {
          setTranscriptNudge((prev: number) => Math.max(0, prev - transcriptViewerBodyRows))
          return true
        }
      }
      if (key.meta && !key.ctrl && lowerChar === "p") {
        if (modelMenu.status === "hidden") {
          void onModelMenuOpen()
        } else {
          onModelMenuCancel()
        }
        return true
      }
      if (key.ctrl && (lowerChar === "c" || char === "\u0003")) {
        const now = Date.now()
        if (ctrlCPrimedAt && now - ctrlCPrimedAt < doubleCtrlWindowMs) {
          setCtrlCPrimedAt(null)
          void onSubmit("/quit")
          process.exit(0)
          return true
        }
        setCtrlCPrimedAt(now)
        return true
      }
      if (
        isEscapeKey &&
        !permissionRequest &&
        rewindMenu.status === "hidden" &&
        confirmState.status === "hidden" &&
        modelMenu.status === "hidden" &&
        paletteState.status === "hidden"
      ) {
        if (transcriptViewerOpen) {
          exitTranscriptViewer()
          return true
        }
        if (pendingResponse) {
          setEscPrimedAt(null)
          void onSubmit("/stop")
          return true
        }
        if (key.meta) {
          setEscPrimedAt(null)
          handleLineEdit("", 0)
          return true
        }
        const now = Date.now()
        if (escPrimedAt && now - escPrimedAt < 650) {
          setEscPrimedAt(null)
          handleLineEdit("", 0)
          return true
        }
        setEscPrimedAt(now)
        return true
      }
      if (!key.ctrl && !key.meta) {
        if (guardrailNotice) {
          if (lowerChar === "e") {
            onGuardrailToggle()
            return true
          }
          if (lowerChar === "x") {
            onGuardrailDismiss()
            return true
          }
        } else {
          if (lowerChar === "e" && toggleSelectedCollapsibleEntry()) {
            return true
          }
        }
        if (char === "[" && cycleCollapsibleSelection(-1)) {
          return true
        }
        if (char === "]" && cycleCollapsibleSelection(1)) {
          return true
        }
      }
      if (key.ctrl && lowerChar === "k") {
        if (modelMenu.status === "hidden") {
          void onModelMenuOpen()
        } else {
          onModelMenuCancel()
        }
        return true
      }
      if (isCtrlG) {
        if (skillsMenu.status === "hidden") {
          void onSkillsMenuOpen()
        } else {
          onSkillsMenuCancel()
        }
        return true
      }
      if (isCtrlU) {
        void onSubmit("/todo-scope next")
        return true
      }
      if (matchesActionBinding(profile, "inspect_panel", char, key)) {
        void onSubmit("/inspect")
        return true
      }
      if (key.ctrl && key.shift && lowerChar === "c") {
        openConfirm("Clear conversation and tool logs?", async () => {
          await onSubmit("/clear")
        })
        return true
      }
      if (matchesActionBinding(profile, "toggle_palette", char, key)) {
        if (paletteState.status === "open") closePalette()
        else openPalette()
        return true
      }
      if (isEscapeKey && modelMenu.status !== "hidden") {
        onModelMenuCancel()
        return true
      }
      return false
    },
    [
      cycleCollapsibleSelection,
      confirmState.status,
      ctrlCPrimedAt,
      ctreeOpen,
      doubleCtrlWindowMs,
      enterTranscriptViewer,
      escPrimedAt,
      exitTranscriptViewer,
      guardrailNotice,
      handleLineEdit,
      keymap,
      closePalette,
      modelMenu.status,
      skillsMenu.status,
      onModelMenuCancel,
      onModelMenuOpen,
      onSkillsMenuCancel,
      onSkillsMenuOpen,
      openConfirm,
      openPalette,
      onSubmit,
      onGuardrailDismiss,
      onGuardrailToggle,
      overlayActive,
      paletteState.status,
      pendingResponse,
      permissionRequest,
      pushCommandResult,
      rewindMenu.status,
      scrollbackMode,
      taskboardDefaultView,
      transcriptViewerBodyRows,
      transcriptViewerOpen,
      toggleSelectedCollapsibleEntry,
    ],
  )
}
