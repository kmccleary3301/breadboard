import React, { useEffect, useMemo, useRef, useState } from "react"
import { Box, Static } from "ink"
import { useStdout } from "ink"
import { ModalHost } from "../ModalHost.js"
import { TranscriptViewer } from "../TranscriptViewer.js"
import type { ReplViewController } from "./controller/useReplViewController.js"
import { createAltBufferSession } from "./altBufferSession.js"

const parseBoolEnv = (value: string | undefined, fallback: boolean): boolean => {
  if (value == null) return fallback
  const normalized = value.trim().toLowerCase()
  if (!normalized) return fallback
  if (["1", "true", "yes", "on"].includes(normalized)) return true
  if (["0", "false", "no", "off"].includes(normalized)) return false
  return fallback
}

export const ReplViewShell: React.FC<{ controller: ReplViewController }> = ({ controller }) => {
  const { stdout } = useStdout()
  const {
    scrollbackMode,
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
  } = controller
  const altBufferEnabled = useMemo(() => parseBoolEnv(process.env.BREADBOARD_TUI_ALT_BUFFER_VIEWER, false), [])
  const resetOnResizeEnabled = useMemo(
    () => parseBoolEnv(process.env.BREADBOARD_TUI_SCROLLBACK_RESET_ON_RESIZE, true),
    [],
  )
  const altBufferSessionRef = useRef<ReturnType<typeof createAltBufferSession> | null>(null)
  const previousSizeRef = useRef<{ cols: number; rows: number } | null>(null)
  const [scrollbackEpoch, setScrollbackEpoch] = useState(0)

  const cols = stdout?.columns && Number.isFinite(stdout.columns) && stdout.columns > 0 ? stdout.columns : 0
  const rows = stdout?.rows && Number.isFinite(stdout.rows) && stdout.rows > 0 ? stdout.rows : 0

  useEffect(() => {
    const isTty = Boolean(stdout && (stdout as NodeJS.WriteStream).isTTY)
    const writer = (chunk: string): void => {
      if (!stdout?.write) return
      stdout.write(chunk)
    }
    altBufferSessionRef.current = createAltBufferSession(writer, altBufferEnabled && isTty)
    return () => {
      altBufferSessionRef.current?.reset()
      altBufferSessionRef.current = null
    }
  }, [altBufferEnabled, stdout])

  useEffect(() => {
    altBufferSessionRef.current?.sync(altBufferEnabled && transcriptViewerOpen)
  }, [altBufferEnabled, transcriptViewerOpen])

  useEffect(() => {
    if (!scrollbackMode) {
      previousSizeRef.current = cols > 0 && rows > 0 ? { cols, rows } : null
      return
    }
    if (!resetOnResizeEnabled) {
      previousSizeRef.current = cols > 0 && rows > 0 ? { cols, rows } : null
      return
    }
    if (!stdout?.isTTY || cols <= 0 || rows <= 0) return
    const previous = previousSizeRef.current
    if (!previous) {
      previousSizeRef.current = { cols, rows }
      return
    }
    if (previous.cols === cols && previous.rows === rows) return
    previousSizeRef.current = { cols, rows }
    try {
      // Full reset keeps resize churn from leaving stale artifacts in scrollback.
      stdout.write("\u001b[3J\u001b[2J\u001b[H")
    } catch {
      // Ignore write failures; Ink will still re-render.
    }
    setScrollbackEpoch((value) => value + 1)
  }, [cols, resetOnResizeEnabled, rows, scrollbackMode, stdout])

  const transcriptViewerDetailLabel =
    altBufferEnabled && transcriptViewerOpen
      ? transcriptDetailLabel
        ? `${transcriptDetailLabel} • alt-buffer`
        : "alt-buffer"
      : transcriptDetailLabel || undefined

  return (
    <Box flexDirection="column">
      {scrollbackMode && (
        <Static key={`static-${scrollbackEpoch}`} items={staticFeed}>
          {(item) => {
            const entry = item as { id: string; node: React.ReactNode }
            return <React.Fragment key={entry.id}>{entry.node}</React.Fragment>
          }}
        </Static>
      )}
      {transcriptViewerOpen ? (
        <TranscriptViewer
          lines={transcriptViewerLines}
          cols={columnWidth}
          rows={rowCount}
          scroll={transcriptViewerEffectiveScroll}
          searchQuery={transcriptSearchOpen ? transcriptSearchQuery : ""}
          matchLines={transcriptSearchOpen ? transcriptSearchLineMatches : undefined}
          matchCount={transcriptSearchOpen ? transcriptSearchMatches.length : undefined}
          activeMatchIndex={transcriptSearchOpen && transcriptSearchMatches.length > 0 ? transcriptSearchSafeIndex : undefined}
          activeMatchLine={transcriptSearchOpen ? transcriptSearchActiveLine : null}
          toggleHint={keymap === "claude" ? "Ctrl+O transcript" : "Ctrl+T transcript"}
          detailLabel={transcriptViewerDetailLabel}
          variant={keymap === "claude" ? "claude" : "default"}
        />
      ) : (
        <ModalHost key={`viewport-${scrollbackEpoch}`} stack={modalStack}>
          {baseContent}
        </ModalHost>
      )}
    </Box>
  )
}
