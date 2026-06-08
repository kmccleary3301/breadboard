import { describe, expect, it } from "vitest"
import type { Block } from "@stream-mdx/core/types"
import type { ConversationEntry } from "../../../../types.js"
import { stripAnsiCodes } from "../../utils/ansi.js"
import { resolveConversationEntryDisplayLines } from "../conversationRenderer.js"

const richHeadingBlocks: Block[] = [
  {
    id: "h",
    type: "heading",
    isFinalized: false,
    payload: {
      raw: "Streaming Heading",
      meta: { headingLevel: 2, headingText: "Streaming Heading" },
      inline: [{ kind: "text", text: "Streaming Heading" }],
    },
  },
  {
    id: "p",
    type: "paragraph",
    isFinalized: false,
    payload: {
      raw: "**Visible body**",
      inline: [{ kind: "strong", children: [{ kind: "text", text: "Visible body" }] }],
    },
  },
]

const baseEntry: ConversationEntry = {
  id: "assistant-1",
  speaker: "assistant",
  phase: "streaming",
  text: "## Streaming Heading\n\n**Visible body**",
  createdAt: 0,
  richBlocks: richHeadingBlocks,
  markdownStreaming: true,
}

describe("conversation markdown display policy", () => {
  it("tail-slices rich streaming markdown instead of replacing it with Assistant streaming preview", () => {
    const lines = resolveConversationEntryDisplayLines(baseEntry, {
      viewPrefs: { richMarkdown: true, collapseMode: "auto", virtualization: "auto" },
      markdownWidth: 80,
      streamingAssistantPreviewLines: 1,
    }).map(stripAnsiCodes)

    expect(lines).toHaveLength(1)
    expect(lines).toContain("Visible body")
    expect(lines.join("\n")).not.toContain("Assistant streaming")
    expect(lines.join("\n")).not.toContain("## Streaming Heading")
    expect(lines.join("\n")).not.toContain("**Visible body**")
  })

  it("renders rich final markdown when no active preview budget is applied", () => {
    const lines = resolveConversationEntryDisplayLines({ ...baseEntry, phase: "final" }, {
      viewPrefs: { richMarkdown: true, collapseMode: "auto", virtualization: "auto" },
      markdownWidth: 80,
      streamingAssistantPreviewLines: 1,
    }).map(stripAnsiCodes)

    expect(lines).toContain("Streaming Heading")
    expect(lines).toContain("Visible body")
    expect(lines.join("\n")).not.toContain("Assistant latest")
  })

  it("tail-slices rendered markdown instead of replacing final content with a latest placeholder", () => {
    const lines = resolveConversationEntryDisplayLines({ ...baseEntry, phase: "final", activePreviewLines: 1 }, {
      viewPrefs: { richMarkdown: true, collapseMode: "auto", virtualization: "auto" },
      markdownWidth: 80,
      streamingAssistantPreviewLines: 1,
    }).map(stripAnsiCodes)

    expect(lines).toHaveLength(1)
    expect(lines.join("\n")).toContain("Visible body")
    expect(lines.join("\n")).not.toContain("Assistant latest")
    expect(lines.join("\n")).not.toContain("**Visible body**")
  })
})
