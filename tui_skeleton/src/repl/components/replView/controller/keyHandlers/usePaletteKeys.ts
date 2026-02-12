import { useCallback } from "react"
import type { KeyHandler } from "../../../../hooks/useKeyRouter.js"

type PaletteKeyHandlerContext = Record<string, any>

export const usePaletteKeys = (context: PaletteKeyHandlerContext): KeyHandler => {
  const {
    applyPaletteItem,
    closePalette,
    ctrlCPrimedAt,
    enterTranscriptViewer,
    exitTranscriptViewer,
    keymap,
    onSubmit,
    paletteItems,
    paletteState,
    setCtrlCPrimedAt,
    setPaletteState,
    setTasksOpen,
    setTodosOpen,
    transcriptViewerOpen,
    doubleCtrlWindowMs,
  } = context

  return useCallback<KeyHandler>(
    (char, key) => {
      const isTabKey = key.tab || (typeof char === "string" && (char.includes("\t") || char.includes("\u001b[Z")))
      if (paletteState.status !== "open") return false
      const lowerChar = char?.toLowerCase()
      const isCtrlT = key.ctrl && lowerChar === "t"
      const isCtrlShiftT = key.ctrl && key.shift && lowerChar === "t"
      const isCtrlB = key.ctrl && lowerChar === "b"
      if (isCtrlShiftT) {
        if (transcriptViewerOpen) {
          exitTranscriptViewer()
        } else {
          enterTranscriptViewer()
        }
        return true
      }
      if (isCtrlT) {
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
        setTasksOpen((prev: boolean) => !prev)
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
      if (key.escape) {
        closePalette()
        return true
      }
      if (key.return) {
        applyPaletteItem(paletteItems[Math.max(0, Math.min(paletteState.index, paletteItems.length - 1))])
        return true
      }
      if (paletteItems.length > 0 && (key.downArrow || isTabKey)) {
        setPaletteState((prev: any) => ({
          ...prev,
          index: (prev.index + 1) % paletteItems.length,
        }))
        return true
      }
      if (paletteItems.length > 0 && key.upArrow) {
        setPaletteState((prev: any) => ({
          ...prev,
          index: (prev.index - 1 + paletteItems.length) % paletteItems.length,
        }))
        return true
      }
      if (key.backspace) {
        setPaletteState((prev: any) => ({
          ...prev,
          query: prev.query.slice(0, -1),
          index: 0,
        }))
        return true
      }
      if (char && !key.ctrl && !key.meta) {
        setPaletteState((prev: any) => ({
          ...prev,
          query: prev.query + char,
          index: 0,
        }))
        return true
      }
      return true
    },
    [
      applyPaletteItem,
      closePalette,
      ctrlCPrimedAt,
      doubleCtrlWindowMs,
      enterTranscriptViewer,
      exitTranscriptViewer,
      keymap,
      paletteItems,
      paletteState.index,
      paletteState.status,
      onSubmit,
      transcriptViewerOpen,
    ],
  )
}
