import React from "react"
import { describe, it, expect } from "vitest"
import { render } from "ink-testing-library"
import { Text } from "ink"
import type { TranscriptItem } from "../../../../transcriptModel.js"
import { useScrollbackFeed } from "../useScrollbackFeed.js"

const FeedProbe = ({
  transcriptEntries,
  streamingEntries,
  landingNode = null,
  appendLandingToFeed = false,
  allowTranscriptAppend = true,
  staticMarkdownChunks = [],
}: {
  transcriptEntries: TranscriptItem[]
  streamingEntries: TranscriptItem[]
  landingNode?: React.ReactNode | null
  appendLandingToFeed?: boolean
  allowTranscriptAppend?: boolean
  staticMarkdownChunks?: Array<{ id: string; node: React.ReactNode; lineCount?: number }>
}) => {
  const { staticFeed } = useScrollbackFeed({
    enabled: true,
    sessionId: "session-1",
    viewClearAt: null,
    headerLines: ["Header"],
    headerSubtitleLines: [],
    historyLandingNode: landingNode,
    historyLandingLineCount: landingNode ? 1 : 0,
    appendHeaderToFeed: true,
    appendLandingToFeed,
    transcriptEntries,
    streamingEntries,
    staticMarkdownChunks,
    allowTranscriptAppend,
    renderTranscriptEntry: (entry: TranscriptItem) => <Text>{`tx ${entry.id}`}</Text>,
    measureTranscriptEntryLines: () => 1,
    transcriptViewerOpen: false,
  })

  return <Text>{staticFeed.map((item) => item.id).join(",")}</Text>
}

describe("useScrollbackFeed", () => {
  it("keeps streaming entry out of the committed feed", () => {
    const conversationEntries: TranscriptItem[] = [
      { id: "c1", kind: "message", speaker: "assistant", text: "A", phase: "final", createdAt: 1, source: "conversation" },
    ]
    const streamingEntry: TranscriptItem = {
      id: "c2",
      kind: "message",
      speaker: "assistant",
      text: "B",
      phase: "streaming",
      createdAt: 2,
      source: "conversation",
      streaming: true,
    }
    const { lastFrame } = render(
      <FeedProbe transcriptEntries={conversationEntries} streamingEntries={[streamingEntry]} />,
    )
    const frame = lastFrame() ?? ""
    expect(frame).toContain("tx-c1")
    expect(frame).not.toContain("tx-c2")
  })

  it("appends landing to static feed when the landing has retired", () => {
    const { lastFrame } = render(
      <FeedProbe
        transcriptEntries={[]}
        streamingEntries={[]}
        landingNode={<Text>landing</Text>}
        appendLandingToFeed
      />,
    )
    const frame = lastFrame() ?? ""
    expect(frame).toContain("landing-session-1-initial")
  })

  it("does not append newly cold transcript rows while transcript append is frozen", () => {
    const entries: TranscriptItem[] = [
      { id: "u1", kind: "message", speaker: "user", text: "prompt", phase: "final", createdAt: 1, source: "conversation" },
      { id: "a1", kind: "message", speaker: "assistant", text: "answer", phase: "final", createdAt: 2, source: "conversation" },
    ]
    const app = render(
      <FeedProbe transcriptEntries={entries} streamingEntries={[]} allowTranscriptAppend={false} />,
    )
    expect(app.lastFrame() ?? "").not.toContain("tx-u1")
    expect(app.lastFrame() ?? "").not.toContain("tx-a1")

    app.rerender(<FeedProbe transcriptEntries={entries} streamingEntries={[]} allowTranscriptAppend />)
    const frame = app.lastFrame() ?? ""
    expect(frame).toContain("tx-u1")
    expect(frame).toContain("tx-a1")
    expect(frame.indexOf("tx-u1")).toBeLessThan(frame.indexOf("tx-a1"))
  })

  it("uses unique spacer ids across multiple append cycles in one session", () => {
    const firstEntries: TranscriptItem[] = [
      { id: "u1", kind: "message", speaker: "user", text: "prompt 1", phase: "final", createdAt: 1, source: "conversation" },
    ]
    const secondEntries: TranscriptItem[] = [
      ...firstEntries,
      { id: "a1", kind: "message", speaker: "assistant", text: "answer 1", phase: "final", createdAt: 2, source: "conversation" },
    ]
    const thirdEntries: TranscriptItem[] = [
      ...secondEntries,
      { id: "u2", kind: "message", speaker: "user", text: "prompt 2", phase: "final", createdAt: 3, source: "conversation" },
    ]
    const app = render(<FeedProbe transcriptEntries={firstEntries} streamingEntries={[]} />)
    app.rerender(<FeedProbe transcriptEntries={secondEntries} streamingEntries={[]} />)
    app.rerender(<FeedProbe transcriptEntries={thirdEntries} streamingEntries={[]} />)

    const frame = app.lastFrame() ?? ""
    expect(frame).toContain("spacer-session-1-0")
    expect(frame).toContain("spacer-session-1-1")
    expect(frame.match(/spacer-session-1-0/g)?.length ?? 0).toBe(1)
    expect(frame.match(/spacer-session-1-1/g)?.length ?? 0).toBe(1)
  })

  it("appends static markdown chunks once by id", () => {
    const chunk = { id: "md-static-msg-1", node: <Text>chunk</Text>, lineCount: 1 }
    const app = render(<FeedProbe transcriptEntries={[]} streamingEntries={[]} staticMarkdownChunks={[chunk]} />)
    app.rerender(<FeedProbe transcriptEntries={[]} streamingEntries={[]} staticMarkdownChunks={[chunk]} />)
    const frame = app.lastFrame() ?? ""
    expect(frame).toContain("md-static-msg-1")
    expect(frame.match(/md-static-msg-1/g)?.length ?? 0).toBe(1)
  })

  it("renders user prompts as first-class static transcript entries", () => {
    const entries: TranscriptItem[] = [
      { id: "u1", kind: "message", speaker: "user", text: "prompt", phase: "final", createdAt: 1, source: "conversation" },
      { id: "a1", kind: "message", speaker: "assistant", text: "answer", phase: "final", createdAt: 2, source: "conversation" },
    ]
    const { lastFrame } = render(
      <FeedProbe transcriptEntries={entries} streamingEntries={[]} />,
    )
    const frame = lastFrame() ?? ""
    expect(frame).toContain("tx-u1")
    expect(frame).toContain("tx-a1")
  })
})
