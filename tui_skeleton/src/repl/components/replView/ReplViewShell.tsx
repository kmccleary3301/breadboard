import React from "react"
import { Box, Static } from "ink"
import { ModalHost } from "../ModalHost.js"
import { TranscriptViewer } from "../TranscriptViewer.js"
import type { ReplViewController } from "./controller/useReplViewController.js"

export const ReplViewShell: React.FC<{ controller: ReplViewController }> = ({ controller }) => {
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
          detailLabel={transcriptDetailLabel || undefined}
          variant={keymap === "claude" ? "claude" : "default"}
        />
      ) : (
        <ModalHost stack={modalStack}>{baseContent}</ModalHost>
      )}
    </Box>
  )
}
