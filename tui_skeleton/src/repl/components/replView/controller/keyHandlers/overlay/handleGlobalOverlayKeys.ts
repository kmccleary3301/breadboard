import { matchCtrl, runKeymap } from "../../../keybindings/keymaps.js"
import type { OverlayHandlerContext, OverlayHandlerResult, OverlayKeyInfo } from "./types.js"

export const handleGlobalOverlayKeys = (
  context: OverlayHandlerContext,
  info: OverlayKeyInfo,
): OverlayHandlerResult => {
  const {
    clearScreen,
    shortcutsOpen,
    shortcutsOpenedAtRef,
    setShortcutsOpen,
    usageOpen,
    setUsageOpen,
    inspectMenu,
    inspectRawMaxScroll,
    inspectRawOpen,
    inspectRawViewportRows,
    setInspectRawOpen,
    setInspectRawScroll,
    keymap,
    setCtreeOpen,
    transcriptViewerOpen,
    enterTranscriptViewer,
    exitTranscriptViewer,
    setTodosOpen,
    setTasksOpen,
    ctreeOpen,
    skillsMenu,
    onSkillsMenuOpen,
    onSkillsMenuCancel,
    ctrlCPrimedAt,
    setCtrlCPrimedAt,
    doubleCtrlWindowMs,
    onSubmit,
    inputValueRef,
  } = context
  const { char, key, lowerChar, isCtrlT, isCtrlShiftT, isCtrlB, isCtrlY, isCtrlG } = info

  const keymapHandled = runKeymap(
    [
      {
        match: matchCtrl("l"),
        action: () => {
          clearScreen()
          return true
        },
      },
      {
        match: matchCtrl("d"),
        action: () => {
          void onSubmit("/quit")
          process.exit(0)
          return true
        },
      },
    ],
    char ?? "",
    key,
  )
  if (keymapHandled) return true

  if (shortcutsOpen && (char === "?" || key.escape)) {
    if (char === "?" && process.env.BREADBOARD_SHORTCUTS_STICKY === "1") {
      return true
    }
    const openedAt = shortcutsOpenedAtRef.current
    if (char === "?" && openedAt && Date.now() - openedAt < 200) {
      return true
    }
    shortcutsOpenedAtRef.current = null
    setShortcutsOpen(false)
    return true
  }
  if (usageOpen && (key.escape || char === "\u001b")) {
    setUsageOpen(false)
    return true
  }
  if (inspectMenu.status !== "hidden") {
    const clampInspectScroll = (value: number) => Math.max(0, Math.min(value, inspectRawMaxScroll))
    if (key.escape || char === "\u001b") {
      void onSubmit("/inspect close")
      return true
    }
    if (!key.ctrl && !key.meta && lowerChar === "r") {
      void onSubmit("/inspect refresh")
      return true
    }
    if (!key.ctrl && !key.meta && lowerChar === "j") {
      setInspectRawOpen((prev: boolean) => !prev)
      setInspectRawScroll(0)
      return true
    }
    if (inspectRawOpen) {
      if (key.upArrow) {
        setInspectRawScroll((prev: number) => clampInspectScroll(prev - 1))
        return true
      }
      if (key.downArrow) {
        setInspectRawScroll((prev: number) => clampInspectScroll(prev + 1))
        return true
      }
      if (key.pageUp) {
        setInspectRawScroll((prev: number) => clampInspectScroll(prev - inspectRawViewportRows))
        return true
      }
      if (key.pageDown) {
        setInspectRawScroll((prev: number) => clampInspectScroll(prev + inspectRawViewportRows))
        return true
      }
    }
  }
  if (isCtrlShiftT && keymap !== "claude") {
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
  if (isCtrlG) {
    if (skillsMenu.status === "hidden") {
      void onSkillsMenuOpen()
    } else {
      onSkillsMenuCancel()
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
  if (key.ctrl && lowerChar === "d") {
    void onSubmit("/quit")
    process.exit(0)
    return true
  }
  if (key.ctrl && lowerChar === "z") {
    if (inputValueRef.current.trim().length === 0) {
      try {
        process.kill(process.pid, "SIGTSTP")
      } catch {
        // ignore
      }
    }
    return true
  }
  return undefined
}
