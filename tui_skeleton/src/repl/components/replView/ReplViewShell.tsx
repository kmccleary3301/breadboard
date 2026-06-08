import React, { useEffect, useLayoutEffect, useMemo, useRef, useState } from "react"
import { appendFileSync } from "node:fs"
import { Box, Static } from "ink"
import { useStdout } from "ink"
import { ModalHost } from "../ModalHost.js"
import { OwnedLiveShellHost } from "./OwnedLiveShellHost.js"
import { DedicatedSceneBufferShellHost } from "./DedicatedSceneBufferShellHost.js"
import { SceneOwnedRuntimeShellHost } from "./SceneOwnedRuntimeShellHost.js"
import { TranscriptViewer } from "../TranscriptViewer.js"
import { writeRenderTimelineDebugRecord } from "./controller/qcDebugLog.js"
import type { ReplViewController } from "./controller/useReplViewController.js"
import { createAltBufferSession } from "./altBufferSession.js"
import { resolveStdoutRowDiagnostics } from "../../inkScrollbackStdout.js"
import { useTerminalSize } from "../../hooks/useTerminalSize.js"
import type { LiveShellOwnershipMode, LiveShellRendererHost, LiveShellSceneStrategy } from "../../../config/frontendMode.js"

const parseBoolEnv = (value: string | undefined, fallback: boolean): boolean => {
  if (value == null) return fallback
  const normalized = value.trim().toLowerCase()
  if (!normalized) return fallback
  if (["1", "true", "yes", "on"].includes(normalized)) return true
  if (["0", "false", "no", "off"].includes(normalized)) return false
  return fallback
}

const traceShellDiagnostic = (event: Record<string, unknown>) => {
  const target = process.env.BREADBOARD_TUI_KEY_TRACE_FILE
  if (!target) return
  try {
    appendFileSync(target, `${JSON.stringify({ timestamp: Date.now(), source: "shell", ...event })}\n`, "utf8")
  } catch {
    // Diagnostic tracing must never affect TUI behavior.
  }
}

const canWriteManagedTerminalSequence = (stdout?: NodeJS.WriteStream | null): boolean =>
  typeof stdout?.write === "function"

export const buildManagedViewportClearSequence = (rowsAboveCursor: number): string => {
  const moveUpRows = Math.max(0, Math.floor(rowsAboveCursor))
  const moveUp = moveUpRows > 0 ? `\u001b[${moveUpRows}A` : ""
  return `\r${moveUp}\r\u001b[J`
}

export const buildBottomAnchoredClearSequence = (rowsAboveBottom: number): string => {
  const moveUpRows = Math.max(0, Math.floor(rowsAboveBottom))
  const moveUp = moveUpRows > 0 ? `\u001b[${moveUpRows}A` : ""
  return `\u001b[999B\r${moveUp}\u001b[J`
}

export const buildViewportBottomAnchoredClearSequence = (rowsAboveBottom: number, terminalRows: number): string => {
  const bottomRow = Math.max(1, Math.floor(terminalRows))
  const moveUpRows = Math.max(0, Math.floor(rowsAboveBottom))
  const moveUp = moveUpRows > 0 ? `\u001b[${moveUpRows}A` : ""
  return `\u001b[${bottomRow};1H${moveUp}\u001b[J`
}

export const buildLineAboveActiveBandClearSequence = (rowsAboveCursor: number): string => {
  const moveUpRows = Math.max(1, Math.floor(rowsAboveCursor))
  return `\u001b7\r\u001b[${moveUpRows}A\u001b[2K\u001b8`
}

export const buildLineRangeAboveActiveBandClearSequence = (rowsAboveCursor: number, lineCount: number): string => {
  const startRows = Math.max(1, Math.floor(rowsAboveCursor))
  const count = Math.max(1, Math.floor(lineCount))
  return Array.from({ length: count }, (_, index) => buildLineAboveActiveBandClearSequence(startRows + index)).join("")
}

export const isEscalatedOwnedHostActive = (
  liveShellOwnershipMode: LiveShellOwnershipMode,
  liveShellRendererHost: LiveShellRendererHost,
): boolean => liveShellOwnershipMode === "owned-live" && liveShellRendererHost === "escalated-owned"

export const isDedicatedSceneBufferStrategyActive = (
  liveShellOwnershipMode: LiveShellOwnershipMode,
  liveShellRendererHost: LiveShellRendererHost,
  liveShellSceneStrategy: LiveShellSceneStrategy,
): boolean =>
  liveShellOwnershipMode === "owned-live" &&
  liveShellRendererHost === "escalated-owned" &&
  liveShellSceneStrategy === "dedicated-scene-buffer"



export const resolveManagedSurfaceKey = (args: {
  readonly liveShellOwnershipMode: LiveShellOwnershipMode
  readonly scrollbackMode: boolean
  readonly managedViewportResetKey?: string | null
  readonly surfaceEpoch: number
}): string => {
  const { liveShellOwnershipMode, scrollbackMode, managedViewportResetKey, surfaceEpoch } = args
  if (liveShellOwnershipMode === "owned-live") return `surface-${managedViewportResetKey ?? "none"}-${surfaceEpoch}`
  if (scrollbackMode) return `surface-preserved-scrollback-${surfaceEpoch}`
  return `surface-${surfaceEpoch}`
}

export const resolveManagedResizeClearSequence = (args: {
  readonly liveShellOwnershipMode: LiveShellOwnershipMode
  readonly scrollbackMode: boolean
  readonly resetOnResizeEnabled: boolean
  readonly volatileActiveBand?: boolean
  readonly reclaimRows: number
  readonly composerRowsAboveCursor?: number
  readonly preConversationIdle?: boolean
  readonly terminalRows: number
  readonly pendingResponse?: boolean
}): string => {
  const { liveShellOwnershipMode, scrollbackMode, resetOnResizeEnabled, reclaimRows } = args
  if (liveShellOwnershipMode === "owned-live") return ""
  const safeReclaimRows = Math.max(0, Math.floor(reclaimRows))
  if (scrollbackMode) {
    if (args.pendingResponse || !args.preConversationIdle) return ""
    const composerRows = Math.max(0, Math.floor(args.composerRowsAboveCursor ?? 0))
    if (args.volatileActiveBand) {
      return buildLineAboveActiveBandClearSequence(Math.min(Math.max(3, composerRows + 2), Math.max(1, args.terminalRows - 1)))
    }
    if (!resetOnResizeEnabled) return ""
    const idleActiveBandRows = Math.min(Math.max(16, safeReclaimRows, composerRows + 1), Math.max(2, args.terminalRows - 2))
    return buildViewportBottomAnchoredClearSequence(idleActiveBandRows, args.terminalRows)
  }
  return buildManagedViewportClearSequence(safeReclaimRows)
}

export const resolveManagedResetClearSequence = (args: {
  readonly liveShellOwnershipMode: LiveShellOwnershipMode
  readonly scrollbackMode: boolean
  readonly reclaimRows: number
  readonly composerRowsAboveCursor?: number
  readonly transientOverlayOpen?: boolean
  readonly terminalRows?: number
}): string => {
  const { liveShellOwnershipMode, scrollbackMode, reclaimRows } = args
  if (liveShellOwnershipMode === "owned-live") return ""
  const safeReclaimRows = Math.max(0, Math.floor(reclaimRows))
  if (scrollbackMode) {
    if (args.transientOverlayOpen) {
      if (safeReclaimRows <= 0) return ""
      const composerRows = Math.max(0, Math.floor(args.composerRowsAboveCursor ?? 0))
      const terminalRows = Math.max(0, Math.floor(args.terminalRows ?? 0))
      const clearRows = Math.min(
        Math.max(4, composerRows + 2),
        terminalRows > 0 ? Math.max(1, terminalRows - 1) : Math.max(4, safeReclaimRows),
      )
      return clearRows > 0 ? buildLineRangeAboveActiveBandClearSequence(Math.max(1, composerRows || 1), clearRows) : ""
    }
    const composerRows = Math.max(0, Math.floor(args.composerRowsAboveCursor ?? 0))
    const desiredRows = Math.max(10, safeReclaimRows, composerRows + 1)
    const terminalRows = Math.max(0, Math.floor(args.terminalRows ?? 0))
    const rows = terminalRows > 0 ? Math.min(desiredRows, Math.max(1, terminalRows - 1)) : desiredRows
    // Preserved-scrollback resets must not bottom-anchor and erase to end of
    // screen. That creates large blank gulfs in PTY/IDE terminals. Clear only
    // the app-owned active band while preserving cursor and prior scrollback.
    return buildLineRangeAboveActiveBandClearSequence(1, rows)
  }
  return buildManagedViewportClearSequence(safeReclaimRows)
}

export const extractManagedResetClearToken = (key?: string | null): string | null => {
  if (!key) return null
  const match = /(?:^|\|)clear:([^|]+)/.exec(key)
  return match ? match[1] ?? null : null
}

export const extractManagedComposerResetToken = (key?: string | null): string | null => {
  if (!key) return null
  const match = /(?:^|\|)composer:([^|]+)/.exec(key)
  return match ? match[1] ?? null : null
}

export const shouldIssuePreservedScrollbackResetClear = (args: {
  readonly previousKey?: string | null
  readonly nextKey?: string | null
  readonly transientOverlayOpen?: boolean
  readonly conversationCount?: number
}): boolean => {
  const previousClearToken = extractManagedResetClearToken(args.previousKey)
  const nextClearToken = extractManagedResetClearToken(args.nextKey)
  const previousComposerToken = extractManagedComposerResetToken(args.previousKey)
  const nextComposerToken = extractManagedComposerResetToken(args.nextKey)
  const explicitClearChanged = previousClearToken !== nextClearToken
  const composerChanged = previousComposerToken !== nextComposerToken
  if (nextComposerToken === "slash-active" && !explicitClearChanged) return false
  if (!explicitClearChanged && Math.max(0, Math.floor(args.conversationCount ?? 0)) > 0) return false
  return (
    explicitClearChanged ||
    composerChanged
  )
}

export const resolveShellAltBufferSessionEnabled = (args: {
  readonly altBufferViewerEnabled: boolean
  readonly transientOverlayOpen?: boolean
  readonly transientOverlayAltBufferEnabled?: boolean
  readonly liveShellOwnershipMode: LiveShellOwnershipMode
  readonly liveShellRendererHost: LiveShellRendererHost
}): boolean =>
  args.altBufferViewerEnabled ||
  Boolean(args.transientOverlayOpen && args.transientOverlayAltBufferEnabled) ||
  isEscalatedOwnedHostActive(args.liveShellOwnershipMode, args.liveShellRendererHost)

export const resolveShellAltBufferDesired = (args: {
  readonly altBufferViewerEnabled: boolean
  readonly transcriptViewerOpen: boolean
  readonly transientOverlayOpen?: boolean
  readonly transientOverlayAltBufferEnabled?: boolean
  readonly liveShellOwnershipMode: LiveShellOwnershipMode
  readonly liveShellRendererHost: LiveShellRendererHost
}): boolean => {
  if (isEscalatedOwnedHostActive(args.liveShellOwnershipMode, args.liveShellRendererHost)) return true
  if (args.transientOverlayOpen && args.transientOverlayAltBufferEnabled) return true
  return args.altBufferViewerEnabled && args.transcriptViewerOpen
}

export const ReplViewShell: React.FC<{ controller: ReplViewController }> = ({ controller }) => {
  // Shell ownership is intentionally narrow in default inline mode:
  // - it emits the frozen committed feed
  // - it hosts the active bottom-band viewport
  // - it may enter an explicit alt-buffer viewer mode
  // It must not simulate fullscreen ownership of the main terminal buffer by
  // clearing and replaying the whole scene on ordinary resize.
  const { stdout } = useStdout()
  const {
    liveShellOwnershipMode,
    liveShellRendererHost,
    liveShellSceneStrategy,
    scrollbackMode,
    staticFeed,
    transcriptViewerOpen,
    transcriptViewerRawMode,
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
    sessionId,
    status,
    pendingResponse,
    disconnected,
    keymap,
    modalStack,
    baseContent,
    managedViewportRowsAboveCursor,
    managedViewportResetKey,
    composerRowsAboveCursor,
    volatileActiveBand,
    conversationCount,
  } = controller
  const transientOverlayOpen = modalStack.length > 0 && scrollbackMode && !transcriptViewerOpen
  const altBufferViewerEnabled = useMemo(() => parseBoolEnv(process.env.BREADBOARD_TUI_ALT_BUFFER_VIEWER, false), [])
  const transientOverlayAltBufferEnabled = useMemo(
    () => parseBoolEnv(process.env.BREADBOARD_TUI_PRESERVED_SCROLLBACK_OVERLAY_ALT_BUFFER, false),
    [],
  )
  const resetOnResizeEnabled = useMemo(
    () => parseBoolEnv(process.env.BREADBOARD_TUI_SCROLLBACK_RESET_ON_RESIZE, false),
    [],
  )
  const altBufferSessionEnabled = useMemo(
    () =>
      resolveShellAltBufferSessionEnabled({
        altBufferViewerEnabled,
        transientOverlayOpen,
        transientOverlayAltBufferEnabled,
        liveShellOwnershipMode,
        liveShellRendererHost,
      }),
    [altBufferViewerEnabled, liveShellOwnershipMode, liveShellRendererHost, transientOverlayAltBufferEnabled, transientOverlayOpen],
  )
  const altBufferDesired = useMemo(
    () =>
      resolveShellAltBufferDesired({
        altBufferViewerEnabled,
        transcriptViewerOpen,
        transientOverlayOpen,
        transientOverlayAltBufferEnabled,
        liveShellOwnershipMode,
        liveShellRendererHost,
      }),
    [altBufferViewerEnabled, transcriptViewerOpen, transientOverlayOpen, transientOverlayAltBufferEnabled, liveShellOwnershipMode, liveShellRendererHost],
  )
  const altBufferSessionRef = useRef<ReturnType<typeof createAltBufferSession> | null>(null)
  const previousSizeRef = useRef<{ cols: number; rows: number } | null>(null)
  const managedViewportRowsRef = useRef(0)
  const previousManagedViewportResetKeyRef = useRef<string | null>(null)
  const [surfaceEpoch, setSurfaceEpoch] = useState(0)
  const [staticFeedEpoch, setStaticFeedEpoch] = useState(0)
  const scheduleSurfaceRepaint = () => {
    setTimeout(() => setSurfaceEpoch((value) => value + 1), 0)
  }

  const terminalSize = useTerminalSize(stdout)
  const cols = Math.max(1, Math.floor(columnWidth || terminalSize.columns))
  const rowDiagnostics = resolveStdoutRowDiagnostics(stdout?.rows)
  const rows = Math.max(1, Math.floor(rowCount || terminalSize.rows))
  const modalRowsAboveCursor = useMemo(() => {
    if (!transientOverlayOpen) return 0
    return modalStack.reduce((maxRows, modal) => Math.max(maxRows, Math.max(0, Math.floor(modal.estimatedRows ?? 12))), 0)
  }, [modalStack, transientOverlayOpen])
  const activeManagedRowsAboveCursor = Math.max(
    Math.max(0, Math.floor(managedViewportRowsAboveCursor ?? 0)),
    modalRowsAboveCursor,
  )

  useLayoutEffect(() => {
    const isTty = Boolean(stdout && (stdout as NodeJS.WriteStream).isTTY)
    const writer = (chunk: string): void => {
      if (!stdout?.write) return
      stdout.write(chunk)
    }
    altBufferSessionRef.current = createAltBufferSession(writer, altBufferSessionEnabled && isTty)
    return () => {
      altBufferSessionRef.current?.reset()
      altBufferSessionRef.current = null
    }
  }, [altBufferSessionEnabled, stdout])

  useLayoutEffect(() => {
    altBufferSessionRef.current?.sync(altBufferDesired)
  }, [altBufferDesired])

  useEffect(() => {
    managedViewportRowsRef.current = Math.max(
      managedViewportRowsRef.current,
      activeManagedRowsAboveCursor,
    )
  }, [activeManagedRowsAboveCursor])

  useEffect(() => {
    if (!scrollbackMode && liveShellOwnershipMode !== "owned-live") return
    writeRenderTimelineDebugRecord({
      event: "shell_geometry",
      sessionId,
      liveShellOwnershipMode,
      scrollbackMode,
      stdoutRows: stdout?.rows ?? null,
      processRows: rowDiagnostics.actualRows,
      resolvedRows: rowDiagnostics.resolvedRows,
      rowSource: rowDiagnostics.source,
      columns: cols,
    })
  }, [cols, liveShellOwnershipMode, rowDiagnostics.actualRows, rowDiagnostics.resolvedRows, rowDiagnostics.source, scrollbackMode, sessionId, stdout?.rows])

  useLayoutEffect(() => {
    const managedViewportActive = scrollbackMode || liveShellOwnershipMode === "owned-live"
    if (!managedViewportActive) {
      previousManagedViewportResetKeyRef.current = managedViewportResetKey ?? null
      return
    }
    if (!canWriteManagedTerminalSequence(stdout)) {
      previousManagedViewportResetKeyRef.current = managedViewportResetKey ?? null
      return
    }
    const previousKey = previousManagedViewportResetKeyRef.current
    previousManagedViewportResetKeyRef.current = managedViewportResetKey ?? null
    if (previousKey == null || previousKey === managedViewportResetKey) return
    const previousClearToken = extractManagedResetClearToken(previousKey)
    const nextClearToken = extractManagedResetClearToken(managedViewportResetKey)
    const previousComposerToken = extractManagedComposerResetToken(previousKey)
    const nextComposerToken = extractManagedComposerResetToken(managedViewportResetKey)
    const explicitClearEpochChanged =
      scrollbackMode &&
      nextClearToken != null &&
      nextClearToken !== "0" &&
      previousClearToken !== nextClearToken
    const composerResetTokenChanged =
      scrollbackMode &&
      conversationCount === 0 &&
      nextComposerToken != null &&
      nextComposerToken !== "none" &&
      nextComposerToken !== "slash-active" &&
      previousComposerToken !== nextComposerToken
    if (scrollbackMode) {
      const shouldClear = shouldIssuePreservedScrollbackResetClear({
        previousKey,
        nextKey: managedViewportResetKey,
        transientOverlayOpen,
        conversationCount,
      })
      if (!shouldClear) {
        managedViewportRowsRef.current = activeManagedRowsAboveCursor
        if (composerResetTokenChanged) {
          setSurfaceEpoch((value) => value + 1)
        }
        return
      }
    }
    const reclaimRows = Math.max(
      managedViewportRowsRef.current,
      activeManagedRowsAboveCursor,
    )
    const leavingSlashSuggestions = previousComposerToken === "slash-active" && nextComposerToken !== "slash-active"
    const staleComposerLineRows = Math.min(Math.max(3, Math.floor(composerRowsAboveCursor ?? 0) + 2), Math.max(1, rows - 1))
    const preConversationTransientOverlayOpen =
      scrollbackMode &&
      transientOverlayOpen &&
      conversationCount === 0
    const nextSequence = preConversationTransientOverlayOpen
      ? buildViewportBottomAnchoredClearSequence(Math.max(1, rows - 5), rows)
      : composerResetTokenChanged
      ? leavingSlashSuggestions
        ? buildLineRangeAboveActiveBandClearSequence(staleComposerLineRows, 4)
        : buildLineAboveActiveBandClearSequence(staleComposerLineRows)
      : resolveManagedResetClearSequence({
          liveShellOwnershipMode,
          scrollbackMode,
          reclaimRows,
          composerRowsAboveCursor,
          transientOverlayOpen,
          terminalRows: rows,
        })
    const sequence = explicitClearEpochChanged ? "\u001b[2J\u001b[H" : nextSequence
    if (sequence) {
      try {
        stdout.write(sequence)
      } catch {
        // Ignore write failures; Ink will still attempt to rerender.
      }
    }
    if (explicitClearEpochChanged) {
      setStaticFeedEpoch((value) => value + 1)
    }
    managedViewportRowsRef.current = activeManagedRowsAboveCursor
    if (sequence) {
      scheduleSurfaceRepaint()
    } else {
      setSurfaceEpoch((value) => value + 1)
    }
  }, [activeManagedRowsAboveCursor, composerRowsAboveCursor, conversationCount, liveShellOwnershipMode, managedViewportResetKey, pendingResponse, rows, scrollbackMode, stdout, transientOverlayOpen])

  useLayoutEffect(() => {
    const managedViewportActive = scrollbackMode || liveShellOwnershipMode === "owned-live"
    if (!managedViewportActive) {
      previousSizeRef.current = cols > 0 && rows > 0 ? { cols, rows } : null
      return
    }
    if (!canWriteManagedTerminalSequence(stdout) || cols <= 0 || rows <= 0) return
    const previous = previousSizeRef.current
    if (!previous) {
      previousSizeRef.current = { cols, rows }
      return
    }
    if (previous.cols === cols && previous.rows === rows) return
    previousSizeRef.current = { cols, rows }
    const reclaimRows = Math.max(
      managedViewportRowsRef.current,
      activeManagedRowsAboveCursor,
    )
    try {
      // Owned-live resize must preserve the existing scene until the remounted
      // surface paints. A fullscreen clear produces a near-blank gap that shows
      // up immediately in the real-terminal shrink checkpoint.
      const nextSequence = resolveManagedResizeClearSequence({
        liveShellOwnershipMode,
        scrollbackMode,
        resetOnResizeEnabled,
        volatileActiveBand,
        reclaimRows,
        composerRowsAboveCursor,
        preConversationIdle: conversationCount === 0 && !pendingResponse && !disconnected,
        terminalRows: rows,
        pendingResponse,
      })
      if (nextSequence) {
        stdout.write(nextSequence)
        scheduleSurfaceRepaint()
      }
    } catch {
      // Ignore write failures; Ink will still re-render.
    }
    managedViewportRowsRef.current = activeManagedRowsAboveCursor
    if (liveShellOwnershipMode === "owned-live" || (scrollbackMode && volatileActiveBand)) {
      setSurfaceEpoch((value) => value + 1)
    }
  }, [activeManagedRowsAboveCursor, cols, composerRowsAboveCursor, conversationCount, disconnected, liveShellOwnershipMode, pendingResponse, resetOnResizeEnabled, rows, scrollbackMode, stdout, volatileActiveBand])

  const transcriptViewerDetailLabel =
    altBufferDesired && transcriptViewerOpen
      ? transcriptDetailLabel
        ? `${transcriptDetailLabel} • alt-buffer`
        : "alt-buffer"
      : transcriptDetailLabel || undefined
  const renderTranscriptInsideEscalatedHost = transcriptViewerOpen && isEscalatedOwnedHostActive(liveShellOwnershipMode, liveShellRendererHost)
  const transcriptNestedModalRows = useMemo(() => {
    if (!transcriptViewerOpen || renderTranscriptInsideEscalatedHost || modalStack.length === 0) return 0
    const topModal = modalStack[modalStack.length - 1]
    const estimatedRows = Math.max(0, Math.floor(topModal?.estimatedRows ?? 12))
    const compactTranscriptReserve = Math.max(0, rowCount - 8)
    return Math.min(Math.max(0, rowCount - 4), Math.max(estimatedRows, compactTranscriptReserve))
  }, [modalStack, renderTranscriptInsideEscalatedHost, rowCount, transcriptViewerOpen])
  const transcriptViewerRows = renderTranscriptInsideEscalatedHost
    ? Math.max(1, rowCount - 1)
    : Math.max(1, rowCount - transcriptNestedModalRows)
  const transcriptViewerNode = (
    <TranscriptViewer
      lines={transcriptViewerLines}
      cols={columnWidth}
      rows={transcriptViewerRows}
      scroll={transcriptViewerEffectiveScroll}
      searchOpen={transcriptSearchOpen}
      searchQuery={transcriptSearchOpen ? transcriptSearchQuery : ""}
      matchLines={transcriptSearchOpen ? transcriptSearchLineMatches : undefined}
      matchCount={transcriptSearchOpen ? transcriptSearchMatches.length : undefined}
      activeMatchIndex={transcriptSearchOpen && transcriptSearchMatches.length > 0 ? transcriptSearchSafeIndex : undefined}
      activeMatchLine={transcriptSearchOpen ? transcriptSearchActiveLine : null}
      toggleHint={keymap === "claude" ? "Ctrl+O transcript" : "Ctrl+T transcript"}
      titleLabel={transcriptViewerRawMode ? "raw event viewer" : "transcript viewer"}
      detailLabel={transcriptViewerDetailLabel}
      variant={keymap === "claude" ? "claude" : "default"}
    />
  )
  const transcriptShortcutHint = keymap === "claude" ? "Ctrl+O Transcript" : "Ctrl+T Transcript"
  const focusedTranscriptModalOpen =
    transcriptViewerOpen &&
    !renderTranscriptInsideEscalatedHost &&
    modalStack.length > 0
  const shellBody = renderTranscriptInsideEscalatedHost ? (
    transcriptViewerNode
  ) : focusedTranscriptModalOpen ? (
    <ModalHost stack={modalStack}>
      <React.Fragment />
    </ModalHost>
  ) : transientOverlayOpen ? (
    <ModalHost stack={modalStack}>
      <React.Fragment />
    </ModalHost>
  ) : (
    <ModalHost stack={modalStack}>{baseContent}</ModalHost>
  )
  useEffect(() => {
    traceShellDiagnostic({
      phase: "shell-render",
      transcriptViewerOpen,
      transcriptViewerRawMode,
      renderTranscriptInsideEscalatedHost,
      modalStack: modalStack.map((modal) => ({ id: modal.id, layout: modal.layout ?? null, estimatedRows: modal.estimatedRows ?? null })),
      rowCount,
      transcriptNestedModalRows,
      transcriptViewerRows,
    })
  }, [modalStack, renderTranscriptInsideEscalatedHost, rowCount, transcriptNestedModalRows, transcriptViewerOpen, transcriptViewerRawMode, transcriptViewerRows])
  const surfaceKey = resolveManagedSurfaceKey({
    liveShellOwnershipMode,
    scrollbackMode,
    managedViewportResetKey,
    surfaceEpoch,
  })

  useEffect(() => {
    if (liveShellOwnershipMode !== "owned-live") return
    writeRenderTimelineDebugRecord({
      event: "scene_owned_shell_render",
      sessionId,
      transcriptViewerOpen,
      transcriptViewerRawMode,
      renderTranscriptInsideEscalatedHost,
      managedViewportResetKey,
      surfaceKey,
      liveShellRendererHost,
      liveShellSceneStrategy,
    })
  }, [
    liveShellOwnershipMode,
    liveShellRendererHost,
    liveShellSceneStrategy,
    managedViewportResetKey,
    renderTranscriptInsideEscalatedHost,
    sessionId,
    surfaceKey,
    transcriptViewerOpen,
    transcriptViewerRawMode,
  ])

  return (
    <Box flexDirection="column">
      {scrollbackMode && (
        <Static key={`static-feed-${staticFeedEpoch}`} items={staticFeed}>
          {(item) => {
            const entry = item as { id: string; node: React.ReactNode }
            return <React.Fragment key={entry.id}>{entry.node}</React.Fragment>
          }}
        </Static>
      )}
      {transcriptViewerOpen && !renderTranscriptInsideEscalatedHost ? (
        focusedTranscriptModalOpen ? (
          <ModalHost stack={modalStack}>
            <React.Fragment />
          </ModalHost>
        ) : (
          <ModalHost stack={modalStack}>{transcriptViewerNode}</ModalHost>
        )
      ) : (
        <React.Fragment>
          <React.Fragment key={surfaceKey}>
            {liveShellOwnershipMode === "owned-live" ? (
              liveShellRendererHost === "escalated-owned" ? (
                liveShellSceneStrategy === "dedicated-scene-buffer" ? (
                  <DedicatedSceneBufferShellHost>{shellBody}</DedicatedSceneBufferShellHost>
                ) : (
                  <SceneOwnedRuntimeShellHost
                    sessionId={sessionId}
                    status={status}
                    pendingResponse={pendingResponse}
                    disconnected={disconnected}
                    rows={rowCount}
                    transcriptShortcutHint={transcriptShortcutHint}
                  >
                    {shellBody}
                  </SceneOwnedRuntimeShellHost>
                )
              ) : (
                <OwnedLiveShellHost>
                  <ModalHost stack={modalStack}>{baseContent}</ModalHost>
                </OwnedLiveShellHost>
              )
            ) : (
              <ModalHost stack={modalStack}>{baseContent}</ModalHost>
            )}
          </React.Fragment>
        </React.Fragment>
      )}
    </Box>
  )
}
