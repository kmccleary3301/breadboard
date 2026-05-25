import { describe, expect, it } from "vitest"
import {
  isEscalatedOwnedHostActive,
  buildLineRangeAboveActiveBandClearSequence,
  extractManagedResetClearToken,
  resolveManagedResizeClearSequence,
  resolveManagedResetClearSequence,
  resolveManagedSurfaceKey,
  resolveShellAltBufferDesired,
  resolveShellAltBufferSessionEnabled,
  shouldIssuePreservedScrollbackResetClear,
} from "../ReplViewShell.js"

describe("ReplViewShell alt-buffer ownership helpers", () => {
  it("recognizes the escalated owned host only for owned-live plus escalated-owned", () => {
    expect(isEscalatedOwnedHostActive("owned-live", "escalated-owned")).toBe(true)
    expect(isEscalatedOwnedHostActive("owned-live", "ink-managed")).toBe(false)
    expect(isEscalatedOwnedHostActive("inline-scrollback", "escalated-owned")).toBe(false)
  })

  it("keeps alt-buffer session available for viewer mode or escalated host mode", () => {
    expect(
      resolveShellAltBufferSessionEnabled({
        altBufferViewerEnabled: true,
        liveShellOwnershipMode: "inline-scrollback",
        liveShellRendererHost: "ink-managed",
      }),
    ).toBe(true)
    expect(
      resolveShellAltBufferSessionEnabled({
        altBufferViewerEnabled: false,
        liveShellOwnershipMode: "owned-live",
        liveShellRendererHost: "escalated-owned",
      }),
    ).toBe(true)
    expect(
      resolveShellAltBufferSessionEnabled({
        altBufferViewerEnabled: false,
        liveShellOwnershipMode: "owned-live",
        liveShellRendererHost: "ink-managed",
      }),
    ).toBe(false)
  })

  it("keeps transient preserved-scrollback overlay alt-buffer ownership opt-in", () => {
    expect(
      resolveShellAltBufferSessionEnabled({
        altBufferViewerEnabled: false,
        transientOverlayOpen: true,
        transientOverlayAltBufferEnabled: false,
        liveShellOwnershipMode: "inline-scrollback",
        liveShellRendererHost: "ink-managed",
      }),
    ).toBe(false)
    expect(
      resolveShellAltBufferSessionEnabled({
        altBufferViewerEnabled: false,
        transientOverlayOpen: true,
        transientOverlayAltBufferEnabled: true,
        liveShellOwnershipMode: "inline-scrollback",
        liveShellRendererHost: "ink-managed",
      }),
    ).toBe(true)
  })

  it("keeps the escalated owned host in alt-buffer continuously", () => {
    expect(
      resolveShellAltBufferDesired({
        altBufferViewerEnabled: false,
        transcriptViewerOpen: false,
        liveShellOwnershipMode: "owned-live",
        liveShellRendererHost: "escalated-owned",
      }),
    ).toBe(true)
    expect(
      resolveShellAltBufferDesired({
        altBufferViewerEnabled: false,
        transcriptViewerOpen: true,
        liveShellOwnershipMode: "owned-live",
        liveShellRendererHost: "escalated-owned",
      }),
    ).toBe(true)
  })

  it("retains transcript-viewer alt-buffer behavior for non-escalated modes", () => {
    expect(
      resolveShellAltBufferDesired({
        altBufferViewerEnabled: true,
        transcriptViewerOpen: true,
        liveShellOwnershipMode: "inline-scrollback",
        liveShellRendererHost: "ink-managed",
      }),
    ).toBe(true)
    expect(
      resolveShellAltBufferDesired({
        altBufferViewerEnabled: true,
        transcriptViewerOpen: false,
        liveShellOwnershipMode: "inline-scrollback",
        liveShellRendererHost: "ink-managed",
      }),
    ).toBe(false)
  })

  it("uses alt-buffer for transient overlays only when explicitly enabled", () => {
    expect(
      resolveShellAltBufferDesired({
        altBufferViewerEnabled: false,
        transcriptViewerOpen: false,
        transientOverlayOpen: true,
        transientOverlayAltBufferEnabled: false,
        liveShellOwnershipMode: "inline-scrollback",
        liveShellRendererHost: "ink-managed",
      }),
    ).toBe(false)
    expect(
      resolveShellAltBufferDesired({
        altBufferViewerEnabled: false,
        transcriptViewerOpen: false,
        transientOverlayOpen: true,
        transientOverlayAltBufferEnabled: true,
        liveShellOwnershipMode: "inline-scrollback",
        liveShellRendererHost: "ink-managed",
      }),
    ).toBe(true)
  })
})


describe("ReplViewShell preserved resize clear policy", () => {
  it("does not issue resize clears in preserved scrollback mode after conversation", () => {
    expect(
      resolveManagedResizeClearSequence({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: true,
        resetOnResizeEnabled: true,
        reclaimRows: 8,
        composerRowsAboveCursor: 3,
        preConversationIdle: false,
        terminalRows: 26,
        pendingResponse: false,
      }),
    ).toBe("")
  })

  it("does not issue a pre-conversation idle composer-band clear in preserved scrollback mode by default", () => {
    expect(
      resolveManagedResizeClearSequence({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: true,
        resetOnResizeEnabled: false,
        reclaimRows: 8,
        composerRowsAboveCursor: 3,
        preConversationIdle: true,
        terminalRows: 26,
        pendingResponse: false,
      }),
    ).toBe("")
  })

  it("issues a single stale-line clear above volatile preserved-scrollback composer resize", () => {
    expect(
      resolveManagedResizeClearSequence({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: true,
        resetOnResizeEnabled: false,
        volatileActiveBand: true,
        reclaimRows: 9,
        composerRowsAboveCursor: 6,
        preConversationIdle: true,
        terminalRows: 24,
        pendingResponse: false,
      }),
    ).toBe("\u001b7\r\u001b[8A\u001b[2K\u001b8")
  })

  it("does not issue volatile preserved-scrollback resize clears after conversation starts", () => {
    expect(
      resolveManagedResizeClearSequence({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: true,
        resetOnResizeEnabled: false,
        volatileActiveBand: true,
        reclaimRows: 9,
        composerRowsAboveCursor: 6,
        preConversationIdle: false,
        terminalRows: 24,
        pendingResponse: false,
      }),
    ).toBe("")
  })

  it("keeps volatile preserved-scrollback resize bounded to the line above composer when reset clear is enabled", () => {
    expect(
      resolveManagedResizeClearSequence({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: true,
        resetOnResizeEnabled: true,
        volatileActiveBand: true,
        reclaimRows: 9,
        composerRowsAboveCursor: 6,
        preConversationIdle: true,
        terminalRows: 24,
        pendingResponse: false,
      }),
    ).toBe("\u001b7\r\u001b[8A\u001b[2K\u001b8")
  })

  it("ignores high reclaim estimates for volatile stale-line resize cleanup", () => {
    expect(
      resolveManagedResizeClearSequence({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: true,
        resetOnResizeEnabled: true,
        volatileActiveBand: true,
        reclaimRows: 80,
        composerRowsAboveCursor: 6,
        preConversationIdle: true,
        terminalRows: 24,
        pendingResponse: false,
      }),
    ).toBe("\u001b7\r\u001b[8A\u001b[2K\u001b8")
  })

  it("keeps an explicit opt-in idle active-band clear budget when row estimates are low", () => {
    expect(
      resolveManagedResizeClearSequence({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: true,
        resetOnResizeEnabled: true,
        reclaimRows: 0,
        composerRowsAboveCursor: 0,
        preConversationIdle: true,
        terminalRows: 26,
        pendingResponse: false,
      }),
    ).toBe("\u001b[26;1H\u001b[16A\u001b[J")
  })

  it("does not issue pending preserved scrollback resize clears", () => {
    expect(
      resolveManagedResizeClearSequence({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: true,
        resetOnResizeEnabled: false,
        volatileActiveBand: true,
        reclaimRows: 19,
        composerRowsAboveCursor: 1,
        preConversationIdle: true,
        terminalRows: 26,
        pendingResponse: true,
      }),
    ).toBe("")
  })

  it("does not expose reset-on-resize as a preserved-scrollback hard clear", () => {
    expect(
      resolveManagedResizeClearSequence({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: true,
        resetOnResizeEnabled: true,
        reclaimRows: 23,
        composerRowsAboveCursor: 4,
        preConversationIdle: false,
        terminalRows: 26,
      }),
    ).toBe("")
  })
})

describe("ReplViewShell preserved reset clear policy", () => {
  it("extracts explicit clear tokens from managed reset keys", () => {
    expect(extractManagedResetClearToken("session:a|clear:0|run:idle")).toBe("0")
    expect(extractManagedResetClearToken("session:a|clear:177|run:idle")).toBe("177")
    expect(extractManagedResetClearToken("session:a|run:idle")).toBeNull()
  })

  it("does not issue destructive preserved-scrollback clears for ordinary run-state changes", () => {
    expect(
      shouldIssuePreservedScrollbackResetClear({
        previousKey: "session:a|clear:0|run:idle|turn:1",
        nextKey: "session:a|clear:0|run:active|turn:2",
        transientOverlayOpen: false,
      }),
    ).toBe(false)
  })

  it("does not clear merely because slash suggestions become active", () => {
    expect(
      shouldIssuePreservedScrollbackResetClear({
        previousKey: "session:a|clear:0|composer:empty",
        nextKey: "session:a|clear:0|composer:slash-active",
        transientOverlayOpen: false,
      }),
    ).toBe(false)
  })

  it("clears when leaving slash suggestions for a plain composer state", () => {
    expect(
      shouldIssuePreservedScrollbackResetClear({
        previousKey: "session:a|clear:0|composer:slash-active",
        nextKey: "session:a|clear:0|composer:multi:2",
        transientOverlayOpen: false,
      }),
    ).toBe(true)
  })

  it("does not clear committed transcript rows for composer-token changes after conversation starts", () => {
    expect(
      shouldIssuePreservedScrollbackResetClear({
        previousKey: "session:a|clear:0|composer:multi:1",
        nextKey: "session:a|clear:0|composer:empty",
        transientOverlayOpen: false,
        conversationCount: 2,
      }),
    ).toBe(false)
  })

  it("can clear a narrow range above the active band after slash dismissal", () => {
    expect(buildLineRangeAboveActiveBandClearSequence(5, 3)).toBe(
      "\u001b7\r\u001b[5A\u001b[2K\u001b8\u001b7\r\u001b[6A\u001b[2K\u001b8\u001b7\r\u001b[7A\u001b[2K\u001b8",
    )
  })

  it("issues preserved-scrollback reset clears for explicit clear epochs only", () => {
    expect(
      shouldIssuePreservedScrollbackResetClear({
        previousKey: "session:a|clear:0|run:idle",
        nextKey: "session:a|clear:123|run:idle",
        transientOverlayOpen: false,
      }),
    ).toBe(true)
    expect(
      shouldIssuePreservedScrollbackResetClear({
        previousKey: "session:a|clear:0|run:idle",
        nextKey: "session:a|clear:0|run:idle",
        transientOverlayOpen: true,
      }),
    ).toBe(false)
  })

  it("issues cursor-preserving reset-key clears for the active band in preserved scrollback mode", () => {
    expect(
      resolveManagedResetClearSequence({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: true,
        reclaimRows: 8,
        composerRowsAboveCursor: 3,
      }),
    ).toBe(buildLineRangeAboveActiveBandClearSequence(1, 10))
  })

  it("caps preserved-scrollback reset-key line clears below full-screen height", () => {
    expect(
      resolveManagedResetClearSequence({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: true,
        reclaimRows: 24,
        composerRowsAboveCursor: 6,
        terminalRows: 24,
      }),
    ).toBe(buildLineRangeAboveActiveBandClearSequence(1, 23))
  })

  it("uses cursor-preserving line-range clears for transient preserved-scrollback overlays", () => {
    expect(
      resolveManagedResetClearSequence({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: true,
        reclaimRows: 8,
        composerRowsAboveCursor: 3,
        transientOverlayOpen: true,
      }),
    ).toBe(
      "\u001b7\r\u001b[3A\u001b[2K\u001b8\u001b7\r\u001b[4A\u001b[2K\u001b8\u001b7\r\u001b[5A\u001b[2K\u001b8\u001b7\r\u001b[6A\u001b[2K\u001b8\u001b7\r\u001b[7A\u001b[2K\u001b8",
    )
  })

  it("does not clear transient preserved-scrollback overlays when no repaint rows are owned", () => {
    expect(
      resolveManagedResetClearSequence({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: true,
        reclaimRows: 0,
        transientOverlayOpen: true,
      }),
    ).toBe("")
  })

  it("still allows non-scrollback managed reset clears for legacy inline surfaces", () => {
    expect(
      resolveManagedResetClearSequence({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: false,
        reclaimRows: 3,
      }),
    ).toBe("\r\u001b[3A\r\u001b[J")
  })
})

describe("ReplViewShell managed surface keys", () => {
  it("keys preserved scrollback surface by managed reset epoch", () => {
    expect(
      resolveManagedSurfaceKey({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: true,
        managedViewportResetKey: "session:a",
        surfaceEpoch: 1,
      }),
    ).toBe("surface-preserved-scrollback-1")
    expect(
      resolveManagedSurfaceKey({
        liveShellOwnershipMode: "inline-scrollback",
        scrollbackMode: true,
        managedViewportResetKey: "session:a",
        surfaceEpoch: 2,
      }),
    ).toBe("surface-preserved-scrollback-2")
  })

  it("keeps owned-live keyed by reset state and epoch", () => {
    expect(
      resolveManagedSurfaceKey({
        liveShellOwnershipMode: "owned-live",
        scrollbackMode: false,
        managedViewportResetKey: "session:a",
        surfaceEpoch: 2,
      }),
    ).toBe("surface-session:a-2")
  })
})
