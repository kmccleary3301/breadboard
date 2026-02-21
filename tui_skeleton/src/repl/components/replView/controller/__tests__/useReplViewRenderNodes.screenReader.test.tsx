import React from "react"
import { Box } from "ink"
import { render } from "ink-testing-library"
import { describe, expect, it } from "vitest"
import { useReplViewRenderNodes } from "../useReplViewRenderNodes.js"

type ProbeProps = {
  readonly screenReaderMode: boolean
  readonly screenReaderProfile?: "concise" | "balanced" | "verbose"
}

const Probe: React.FC<ProbeProps> = ({ screenReaderMode, screenReaderProfile }) => {
  const nodes = useReplViewRenderNodes({
    claudeChrome: false,
    screenReaderMode,
    screenReaderProfile,
    keymap: "claude",
    contentWidth: 100,
    hints: ["Working…", "Ready."],
    completionHint: null,
    statusLinePosition: "above_input",
    statusLineAlign: "left",
    shortcutsOpen: false,
    ctrlCPrimedAt: null,
    escPrimedAt: null,
    pendingResponse: false,
    scrollbackMode: false,
    subagentStrip: null,
    liveSlots: [],
    animationTick: 0,
    collapsibleEntries: [],
    collapsibleMeta: new Map(),
    selectedCollapsibleEntryId: null,
    compactMode: false,
    transcriptWindow: { items: [], truncated: false, hiddenCount: 0 },
    renderTranscriptEntry: () => null,
  })
  return (
    <Box flexDirection="column">
      {nodes.metaNodes}
      {nodes.hintNodes}
      {nodes.shortcutHintNodes}
    </Box>
  )
}

describe("useReplViewRenderNodes screen reader mode", () => {
  it("renders accessibility-first meta hints when enabled", () => {
    const view = render(<Probe screenReaderMode={true} />)
    const frame = view.lastFrame() ?? ""
    expect(frame).toContain("Accessibility mode active")
    expect(frame).not.toContain("Slash commands")
  })

  it("keeps default meta hints when accessibility mode is disabled", () => {
    const view = render(<Probe screenReaderMode={false} />)
    const frame = view.lastFrame() ?? ""
    expect(frame).toContain("Slash commands")
    expect(frame).not.toContain("Accessibility mode active")
  })

  it("supports concise accessibility profile output", () => {
    const view = render(<Probe screenReaderMode={true} screenReaderProfile="concise" />)
    const frame = view.lastFrame() ?? ""
    expect(frame).toContain("Accessibility mode active. Enter submit, Esc interrupt.")
    expect(frame).not.toContain("Extended shortcuts")
  })

  it("supports verbose accessibility profile output", () => {
    const view = render(<Probe screenReaderMode={true} screenReaderProfile="verbose" />)
    const frame = view.lastFrame() ?? ""
    expect(frame).toContain("Extended shortcuts")
    expect(frame).toContain("Ctrl+G")
  })
})
