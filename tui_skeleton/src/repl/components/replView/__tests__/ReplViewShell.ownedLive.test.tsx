import React from "react"
import { describe, expect, it } from "vitest"
import { render } from "ink-testing-library"
import { Text } from "ink"
import { ReplViewShell } from "../ReplViewShell.js"
import type { LiveShellOwnershipMode, LiveShellRendererHost, LiveShellSceneStrategy } from "../../../../config/frontendMode.js"

const buildController = (overrides: Record<string, unknown> = {}) => ({
  liveShellOwnershipMode: "inline-scrollback" as LiveShellOwnershipMode,
  liveShellRendererHost: "ink-managed" as LiveShellRendererHost,
  liveShellSceneStrategy: "scene-owned-runtime" as LiveShellSceneStrategy,
  sessionId: "sess-12345678",
  status: "ready",
  pendingResponse: false,
  disconnected: false,
  stats: { lastTurn: 1 },
  scrollbackMode: false,
  staticFeed: [],
  transcriptViewerOpen: false,
  transcriptViewerRawMode: false,
  transcriptViewerLines: [],
  columnWidth: 80,
  rowCount: 24,
  transcriptViewerEffectiveScroll: 0,
  transcriptSearchOpen: false,
  transcriptSearchQuery: "",
  transcriptSearchLineMatches: undefined,
  transcriptSearchMatches: [],
  transcriptSearchSafeIndex: 0,
  transcriptSearchActiveLine: null,
  transcriptDetailLabel: undefined,
  keymap: "claude",
  modalStack: [],
  baseContent: <Text>base body</Text>,
  managedViewportRowsAboveCursor: 0,
  managedViewportResetKey: "stable",
  ...overrides,
})

describe("ReplViewShell owned live host", () => {
  it("shows owned live shell host label when ownership mode is owned-live", () => {
    const { lastFrame } = render(
      <ReplViewShell
        controller={buildController({ liveShellOwnershipMode: "owned-live" as LiveShellOwnershipMode }) as any}
      />,
    )
    expect(lastFrame()).toContain("owned live shell")
    expect(lastFrame()).toContain("base body")
  })

  it("shows product Live Shell rail when owned-live uses the escalated selection", () => {
    const { lastFrame } = render(
      <ReplViewShell
        controller={
          buildController({
            liveShellOwnershipMode: "owned-live" as LiveShellOwnershipMode,
            liveShellRendererHost: "escalated-owned" as LiveShellRendererHost,
          }) as any
        }
      />,
    )
    expect(lastFrame()).toContain("Live Shell")
    expect(lastFrame()).toContain("Ready")
    expect(lastFrame()).toContain("base body")
  })

  it("shows dedicated scene-buffer host label when the dormant S2 strategy is selected", () => {
    const { lastFrame } = render(
      <ReplViewShell
        controller={
          buildController({
            liveShellOwnershipMode: "owned-live" as LiveShellOwnershipMode,
            liveShellRendererHost: "escalated-owned" as LiveShellRendererHost,
            liveShellSceneStrategy: "dedicated-scene-buffer" as LiveShellSceneStrategy,
          }) as any
        }
      />,
    )
    expect(lastFrame()).toContain("scene buffer host")
    expect(lastFrame()).toContain("base body")
  })

  it("does not show owned live shell host label in inline mode", () => {
    const { lastFrame } = render(<ReplViewShell controller={buildController() as any} />)
    expect(lastFrame()).not.toContain("owned live shell")
    expect(lastFrame()).not.toContain("owned viewport host")
    expect(lastFrame()).toContain("base body")
  })

  it("renders frozen static feed only in scrollback mode", () => {
    const { lastFrame } = render(
      <ReplViewShell
        controller={buildController({
          scrollbackMode: true,
          staticFeed: [{ id: "feed-1", node: <Text>frozen entry</Text> }],
        }) as any}
      />,
    )
    expect(lastFrame()).toContain("frozen entry")
  })

  it("keeps transcript detail sheets visible instead of rendering them below the transcript viewer", () => {
    const transcriptLines = Array.from({ length: 24 }, (_, index) => `transcript line ${index + 1}`)
    const { lastFrame } = render(
      <ReplViewShell
        controller={
          buildController({
            rowCount: 18,
            transcriptViewerOpen: true,
            transcriptViewerLines: transcriptLines,
            transcriptDetailLabel: "1/1 Write(report.txt) · o inspect",
            modalStack: [
              {
                id: "result-detail",
                layout: "sheet",
                estimatedRows: 8,
                render: () => <Text>Result detail visible</Text>,
              },
            ],
          }) as any
        }
      />,
    )
    expect(lastFrame()).not.toContain("transcript line 24")
    expect(lastFrame()).toContain("Result detail visible")
  })

  it("labels raw event mode as a raw event viewer, not a transcript viewer", () => {
    const { lastFrame } = render(
      <ReplViewShell
        controller={
          buildController({
            keymap: "codex",
            transcriptViewerOpen: true,
            transcriptViewerRawMode: true,
            transcriptViewerLines: ['Status [raw] {"event":"debug"}'],
            transcriptDetailLabel: "raw event viewer · follow tail",
          }) as any
        }
      />,
    )
    const frame = lastFrame() ?? ""
    expect(frame).toContain("breadboard raw event viewer")
    expect(frame).not.toContain("breadboard transcript viewer")
  })
})
