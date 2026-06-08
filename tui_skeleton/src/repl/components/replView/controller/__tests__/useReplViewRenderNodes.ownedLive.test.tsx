import React from "react"
import { Box } from "ink"
import { render } from "ink-testing-library"
import { describe, expect, it } from "vitest"
import { useReplViewRenderNodes } from "../useReplViewRenderNodes.js"

type ProbeProps = {
  readonly pendingResponse: boolean
  readonly scrollbackMode?: boolean
}

const Probe: React.FC<ProbeProps> = ({ pendingResponse, scrollbackMode = false }) => {
  const nodes = useReplViewRenderNodes({
    liveShellOwnershipMode: "owned-live",
    claudeChrome: false,
    screenReaderMode: false,
    keymap: "claude",
    contentWidth: 100,
    hints: [],
    completionHint: null,
    statusLinePosition: "above_input",
    statusLineAlign: "left",
    shortcutsOpen: false,
    ctrlCPrimedAt: null,
    escPrimedAt: null,
    pendingResponse,
    scrollbackMode,
    subagentStrip: null,
    liveSlots: [{ id: "slot-1", text: "tool: running", status: "pending", updatedAt: 0 }],
    animationTick: 0,
    collapsibleEntries: [],
    collapsibleMeta: new Map(),
    selectedCollapsibleEntryId: null,
    compactMode: false,
    transcriptWindow: { items: [], truncated: false, hiddenCount: 0 },
    renderTranscriptEntry: () => null,
  })
  return <Box flexDirection="column">{nodes.liveSlotNodes}</Box>
}

describe("useReplViewRenderNodes owned-live live slots", () => {
  it("suppresses settled live slots in owned-live mode", () => {
    const view = render(<Probe pendingResponse={false} />)
    const frame = view.lastFrame() ?? ""
    expect(frame).not.toContain("tool: running")
  })

  it("keeps live slots visible while owned-live is pending", () => {
    const view = render(<Probe pendingResponse={true} />)
    const frame = view.lastFrame() ?? ""
    expect(frame).toContain("tool: running")
  })

  it("keeps pending live slots visible in scrollback mode", () => {
    const view = render(<Probe pendingResponse={true} scrollbackMode={true} />)
    const frame = view.lastFrame() ?? ""
    expect(frame).toContain("tool: running")
  })
})
