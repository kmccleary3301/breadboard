import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { Box, Text } from "ink"
import type { TranscriptItem } from "../../../transcriptModel.js"
import { buildCommandResultEntry, COMMAND_RESULT_LIMIT } from "./commandResults.js"
import type { StaticFeedItem } from "../types.js"
import { parseBooleanEnv } from "../../../../utils/envBoolean.js"
import { writeScrollbackFeedDebugRecord } from "../controller/qcDebugLog.js"

const LANDING_ALWAYS = parseBooleanEnv(process.env.BREADBOARD_TUI_LANDING_ALWAYS, true)

interface ScrollbackFeedOptions {
  readonly enabled: boolean
  readonly sessionId: string
  readonly viewClearAt?: number | null
  readonly headerLines: string[]
  readonly headerSubtitleLines: string[]
  readonly historyLandingNode: React.ReactNode | null
  readonly historyLandingLineCount: number
  readonly appendHeaderToFeed: boolean
  readonly appendLandingToFeed: boolean
  readonly transcriptEntries: ReadonlyArray<TranscriptItem>
  readonly streamingEntries: ReadonlyArray<TranscriptItem>
  readonly staticMarkdownChunks?: ReadonlyArray<StaticFeedItem>
  readonly allowTranscriptAppend?: boolean
  readonly renderTranscriptEntry: (entry: TranscriptItem, key?: string) => React.ReactNode
  readonly measureTranscriptEntryLines: (entry: TranscriptItem) => number
  readonly transcriptViewerOpen: boolean
}

export interface ScrollbackFeedState {
  readonly staticFeed: StaticFeedItem[]
  readonly staticFeedLineCount: number
  readonly pushCommandResult: (title: string, lines: string[]) => void
  readonly headerPrintedRef: React.MutableRefObject<boolean>
  readonly landingPrintedRef: React.MutableRefObject<boolean>
  readonly printedTranscriptIdsRef: React.MutableRefObject<Set<string>>
}

export const useScrollbackFeed = (options: ScrollbackFeedOptions): ScrollbackFeedState => {
  const {
    enabled,
    sessionId,
    viewClearAt,
    headerLines,
    headerSubtitleLines,
    historyLandingNode,
    historyLandingLineCount,
    appendHeaderToFeed,
    appendLandingToFeed,
    transcriptEntries,
    streamingEntries,
    staticMarkdownChunks = [],
    allowTranscriptAppend = true,
    renderTranscriptEntry,
    measureTranscriptEntryLines,
    transcriptViewerOpen,
  } = options
  const [staticFeed, setStaticFeed] = useState<StaticFeedItem[]>([])
  const headerPrintedRef = useRef(false)
  const landingPrintedRef = useRef(false)
  const printedTranscriptIdsRef = useRef(new Set<string>())
  const lastConversationSignatureRef = useRef<{ sig: string; createdAt?: number | null } | null>(null)
  const spacerCounterRef = useRef(0)
  const feedEpoch = viewClearAt ?? "initial"

  const pushCommandResult = useCallback((title: string, lines: string[]) => {
    const entry = buildCommandResultEntry(title, lines)
    setStaticFeed((prev) => {
      const next = [...prev, entry]
      if (next.length <= COMMAND_RESULT_LIMIT) return next
      return next.slice(next.length - COMMAND_RESULT_LIMIT)
    })
  }, [])

  useLayoutEffect(() => {
    if (!enabled) return
    headerPrintedRef.current = false
    landingPrintedRef.current = false
    printedTranscriptIdsRef.current.clear()
    lastConversationSignatureRef.current = null
    spacerCounterRef.current = 0
    setStaticFeed([])
    writeScrollbackFeedDebugRecord({
      event: "feed_reset",
      sessionId,
      viewClearAt: viewClearAt ?? null,
      enabled,
    })
  }, [enabled, sessionId, viewClearAt])

  useLayoutEffect(() => {
    if (!enabled) return
    if (transcriptViewerOpen) return
    setStaticFeed((prev) => {
      let next = prev
      let changed = false

      if (!headerPrintedRef.current && appendHeaderToFeed) {
        const itemId = `header-${sessionId}-${feedEpoch}`
        const headerNode = (
          <Box flexDirection="column">
            {headerLines.map((line, index) => (
              <Text key={`header-${index}`}>{line}</Text>
            ))}
            {headerSubtitleLines.map((line, index) => (
              <Text key={`header-sub-${index}`}>{line}</Text>
            ))}
          </Box>
        )
        next = [...next, { id: itemId, node: headerNode, lineCount: headerLines.length + headerSubtitleLines.length }]
        changed = true
        headerPrintedRef.current = true
        writeScrollbackFeedDebugRecord({
          event: "append_header",
          sessionId,
          itemId,
          lineCount: headerLines.length + headerSubtitleLines.length,
        })
      }

      if (appendLandingToFeed && historyLandingNode && !landingPrintedRef.current) {
        const itemId = `landing-${sessionId}-${feedEpoch}`
        next = [...next, { id: itemId, node: historyLandingNode, lineCount: historyLandingLineCount }]
        landingPrintedRef.current = true
        changed = true
        writeScrollbackFeedDebugRecord({
          event: "append_landing",
          sessionId,
          itemId,
          lineCount: historyLandingLineCount,
        })
      }

      if (allowTranscriptAppend) {
        const pending: Array<{ id: string; node: React.ReactNode; order: number; seq: number; lineCount: number }> = []
        let seq = 0

        for (const entry of transcriptEntries) {
          if (printedTranscriptIdsRef.current.has(entry.id)) continue
          if (entry.kind === "message") {
            const sig = `${entry.speaker}|${entry.text}`
            const createdAt = Number.isFinite(entry.createdAt) ? entry.createdAt : null
            const lastSig = lastConversationSignatureRef.current
            const isDup =
              lastSig &&
              lastSig.sig === sig &&
              ((createdAt != null && lastSig.createdAt != null && Math.abs(createdAt - lastSig.createdAt) < 2000) ||
                (createdAt == null && lastSig.createdAt == null))
            if (isDup) {
              printedTranscriptIdsRef.current.add(entry.id)
              continue
            }
            lastConversationSignatureRef.current = { sig, createdAt }
          }
          printedTranscriptIdsRef.current.add(entry.id)
          const node = renderTranscriptEntry(entry, `static-${entry.id}`)
          if (node) {
            pending.push({
              id: `tx-${entry.id}`,
              node,
              order: Number.isFinite(entry.createdAt) ? entry.createdAt : Date.now(),
              seq: seq++,
              lineCount: Math.max(1, measureTranscriptEntryLines(entry)),
            })
          }
        }

        if (pending.length > 0) {
          pending.sort((a, b) => (a.order - b.order) || (a.seq - b.seq))
          const spaced: StaticFeedItem[] = []
          const lastExistingId = next.length > 0 ? next[next.length - 1]?.id ?? "" : ""
          let needsSpacer = next.length > 0 && !lastExistingId.startsWith("md-static-")
          for (const item of pending) {
            if (needsSpacer) {
              const spacerId = spacerCounterRef.current++
              spaced.push({ id: `spacer-${sessionId}-${spacerId}`, node: <Text> </Text>, lineCount: 1 })
            }
            spaced.push({ id: item.id, node: item.node, lineCount: item.lineCount })
            needsSpacer = true
          }
          next = [...next, ...spaced]
          changed = true
          writeScrollbackFeedDebugRecord({
            event: "append_transcript_batch",
            sessionId,
            appendedIds: pending.map((item) => item.id),
            appendedCount: pending.length,
            spacerCount: spaced.filter((item) => item.id.startsWith(`spacer-${sessionId}-`)).length,
            totalStaticItems: next.length,
          })
        }
      }

      if (staticMarkdownChunks.length > 0) {
        const pendingChunks = staticMarkdownChunks.filter((item) => !printedTranscriptIdsRef.current.has(item.id))
        if (pendingChunks.length > 0) {
          next = [...next, ...pendingChunks]
          for (const item of pendingChunks) printedTranscriptIdsRef.current.add(item.id)
          changed = true
          writeScrollbackFeedDebugRecord({
            event: "append_markdown_static_chunks",
            sessionId,
            appendedIds: pendingChunks.map((item) => item.id),
            appendedCount: pendingChunks.length,
            totalStaticItems: next.length,
          })
        }
      }

      return changed ? next : prev
    })
  }, [
    enabled,
    appendHeaderToFeed,
    appendLandingToFeed,
    headerLines,
    headerSubtitleLines,
    historyLandingNode,
    historyLandingLineCount,
    feedEpoch,
    transcriptEntries,
    streamingEntries,
    staticMarkdownChunks,
    allowTranscriptAppend,
    renderTranscriptEntry,
    measureTranscriptEntryLines,
    sessionId,
    transcriptViewerOpen,
  ])

  return {
    staticFeed,
    staticFeedLineCount: staticFeed.reduce((total, item) => total + Math.max(1, item.lineCount ?? 1), 0),
    pushCommandResult,
    headerPrintedRef,
    landingPrintedRef,
    printedTranscriptIdsRef,
  }
}
