import React from "react"
import { describe, it, expect } from "vitest"
import { render } from "ink-testing-library"
import { Text } from "ink"
import type { TranscriptItem } from "../../../../transcriptModel.js"
import { useScrollbackFeed } from "../useScrollbackFeed.js"

const FeedProbe = ({
  transcriptEntries,
  streamingEntries,
}: {
  transcriptEntries: TranscriptItem[]
  streamingEntries: TranscriptItem[]
}) => {
  const { staticFeed } = useScrollbackFeed({
    enabled: true,
    sessionId: "session-1",
    viewClearAt: null,
    headerLines: ["Header"],
    headerSubtitleLines: [],
    landingNode: null,
    transcriptEntries,
    streamingEntries,
    renderTranscriptEntry: (entry: TranscriptItem) => <Text>{`tx ${entry.id}`}</Text>,
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
})
