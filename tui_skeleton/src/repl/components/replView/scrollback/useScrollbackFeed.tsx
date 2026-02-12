import React, { useCallback, useEffect, useLayoutEffect, useRef, useState } from "react"
import { Box, Text } from "ink"
import type { TranscriptItem } from "../../../transcriptModel.js"
import { buildCommandResultEntry, COMMAND_RESULT_LIMIT } from "./commandResults.js"
import type { StaticFeedItem } from "../types.js"

const parseBoolEnv = (value: string | undefined, fallback: boolean): boolean => {
  if (value == null) return fallback
  const normalized = value.trim().toLowerCase()
  if (!normalized) return fallback
  if (["1", "true", "yes", "on"].includes(normalized)) return true
  if (["0", "false", "no", "off"].includes(normalized)) return false
  return fallback
}

const LANDING_ALWAYS = parseBoolEnv(process.env.BREADBOARD_TUI_LANDING_ALWAYS, false)

interface ScrollbackFeedOptions {
  readonly enabled: boolean
  readonly sessionId: string
  readonly viewClearAt?: number | null
  readonly headerLines: string[]
  readonly headerSubtitleLines: string[]
  readonly landingNode: React.ReactNode | null
  readonly transcriptEntries: TranscriptItem[]
  readonly streamingEntries: TranscriptItem[]
  readonly renderTranscriptEntry: (entry: TranscriptItem, key?: string) => React.ReactNode
  readonly transcriptViewerOpen: boolean
}

export interface ScrollbackFeedState {
  readonly staticFeed: StaticFeedItem[]
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
    landingNode,
    transcriptEntries,
    streamingEntries,
    renderTranscriptEntry,
    transcriptViewerOpen,
  } = options
  const [staticFeed, setStaticFeed] = useState<StaticFeedItem[]>([])
  const headerPrintedRef = useRef(false)
  const landingPrintedRef = useRef(false)
  const printedTranscriptIdsRef = useRef(new Set<string>())
  const lastConversationSignatureRef = useRef<{ sig: string; createdAt?: number | null } | null>(null)

  const pushCommandResult = useCallback((title: string, lines: string[]) => {
    const entry = buildCommandResultEntry(title, lines)
    setStaticFeed((prev) => {
      const next = [...prev, entry]
      if (next.length <= COMMAND_RESULT_LIMIT) return next
      return next.slice(next.length - COMMAND_RESULT_LIMIT)
    })
  }, [])

  useEffect(() => {
    if (!enabled) return
    headerPrintedRef.current = false
    landingPrintedRef.current = false
    printedTranscriptIdsRef.current.clear()
    lastConversationSignatureRef.current = null
    setStaticFeed([])
  }, [enabled, sessionId, viewClearAt])

  useLayoutEffect(() => {
    if (!enabled) return
    if (transcriptViewerOpen) return
    setStaticFeed((prev) => {
      let next = prev
      let changed = false

      const landingActive =
        Boolean(landingNode) &&
        (LANDING_ALWAYS || (transcriptEntries.length === 0 && streamingEntries.length === 0))

      if (!headerPrintedRef.current) {
        if (!landingActive) {
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
          next = [...next, { id: `header-${sessionId}`, node: headerNode }]
          changed = true
        }
        headerPrintedRef.current = true
      }

      if (landingActive && landingNode && !landingPrintedRef.current) {
        next = [...next, { id: `landing-${sessionId}`, node: landingNode }]
        landingPrintedRef.current = true
        changed = true
      }

      const pending: Array<{ id: string; node: React.ReactNode; order: number; seq: number }> = []
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
          })
        }
      }

      if (pending.length > 0) {
        pending.sort((a, b) => (a.order - b.order) || (a.seq - b.seq))
        const spaced: StaticFeedItem[] = []
        let needsSpacer = next.length > 0
        let spacerIndex = 0
        for (const item of pending) {
          if (needsSpacer) {
            spaced.push({ id: `spacer-${sessionId}-${spacerIndex++}`, node: <Text> </Text> })
          }
          spaced.push({ id: item.id, node: item.node })
          needsSpacer = true
        }
        next = [...next, ...spaced]
        changed = true
      }

      return changed ? next : prev
    })
  }, [
    enabled,
    headerLines,
    headerSubtitleLines,
    landingNode,
    transcriptEntries,
    streamingEntries,
    renderTranscriptEntry,
    sessionId,
    transcriptViewerOpen,
  ])

  return {
    staticFeed,
    pushCommandResult,
    headerPrintedRef,
    landingPrintedRef,
    printedTranscriptIdsRef,
  }
}
