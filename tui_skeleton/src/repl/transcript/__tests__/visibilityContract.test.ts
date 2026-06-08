import { describe, expect, it } from "vitest"
import { buildTranscriptFromEvents } from "../../transcriptBuilder.js"
import type { NormalizedEvent } from "../normalizedEvent.js"

describe("transcript event visibility contract", () => {
  it("excludes audit and diagnostic events from transcript projection", () => {
    const events: NormalizedEvent[] = [
      {
        seq: 1,
        type: "assistant_message",
        actor: { kind: "assistant" },
        messageId: "audit",
        textDelta: "private prompt text",
        visibility: "audit",
      },
      {
        seq: 2,
        type: "ctree_node",
        actor: { kind: "system" },
        textDelta: "ctree audit payload",
        visibility: "audit",
      },
      {
        seq: 3,
        type: "assistant_message",
        actor: { kind: "assistant" },
        messageId: "visible",
        textDelta: "visible answer",
        visibility: "transcript",
      },
    ]

    const transcript = buildTranscriptFromEvents(events)
    expect(transcript.committed).toHaveLength(1)
    expect(transcript.committed[0]).toMatchObject({ kind: "message", text: "visible answer" })
  })

  it("excludes log-link artifacts from transcript projection", () => {
    const events: NormalizedEvent[] = [
      {
        seq: 1,
        type: "log_link",
        actor: { kind: "system" },
        textDelta: "file://logging/20260430-203935_ray_SCE",
        visibility: "host",
      },
      {
        seq: 2,
        type: "assistant_message",
        actor: { kind: "assistant" },
        messageId: "visible",
        textDelta: "visible answer",
        visibility: "transcript",
      },
    ]

    const transcript = buildTranscriptFromEvents(events)
    expect(transcript.committed).toHaveLength(1)
    expect(transcript.committed[0]).toMatchObject({ kind: "message", text: "visible answer" })
  })
})
