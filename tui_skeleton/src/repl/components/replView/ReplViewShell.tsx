import React, { useEffect, useMemo, useRef } from "react"
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
  const altBufferSessionRef = useRef<ReturnType<typeof createAltBufferSession> | null>(null)

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

  const transcriptViewerDetailLabel =
    altBufferEnabled && transcriptViewerOpen
      ? transcriptDetailLabel
        ? `${transcriptDetailLabel} • alt-buffer`
        : "alt-buffer"
      : transcriptDetailLabel || undefined

  return (
    <Box flexDirection="column">
      {scrollbackMode && (
        <Static items={staticFeed}>
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
        <ModalHost stack={modalStack}>{baseContent}</ModalHost>
      )}
    </Box>
  )
}
