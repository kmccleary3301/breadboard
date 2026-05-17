import { describe, expect, it } from "vitest"
import { buildTranscript, buildTranscriptFromEvents, dedupeTranscriptStatusItems } from "../transcriptBuilder.js"
import type { TranscriptItem } from "../transcriptModel.js"

describe("transcriptBuilder status dedupe", () => {
  it("dedupes repeated status/log cells without dropping user requests", () => {
    const items: TranscriptItem[] = [
      {
        id: "msg:user-1",
        kind: "message",
        speaker: "user",
        text: "Show me markdown.",
        phase: "final",
        createdAt: 1,
        source: "conversation",
      },
      {
        id: "sys:log-1",
        kind: "system",
        systemKind: "log",
        text: "Log link available.",
        status: "success",
        createdAt: 2,
        source: "system",
      },
      {
        id: "sys:log-2",
        kind: "system",
        systemKind: "log",
        text: "Log link available.",
        status: "success",
        createdAt: 3,
        source: "system",
      },
      {
        id: "msg:user-2",
        kind: "message",
        speaker: "user",
        text: "Show me markdown.",
        phase: "final",
        createdAt: 4,
        source: "conversation",
      },
    ]

    expect(dedupeTranscriptStatusItems(items).map((item) => item.id)).toEqual([
      "msg:user-1",
      "sys:log-1",
      "msg:user-2",
    ])
  })

  it("dedupes status-like tool log entries when building transcript from tool events", () => {
    const result = buildTranscript({
      conversation: [],
      toolEvents: [
        {
          id: "log-1",
          kind: "status",
          text: "Log link available.",
          status: "success",
          createdAt: 1,
        },
        {
          id: "log-2",
          kind: "status",
          text: "Log link available.",
          status: "success",
          createdAt: 2,
        },
      ],
    })

    expect(result.committed.map((item) => item.id)).toEqual(["sys:log-1"])
  })

  it("dedupes repeated normalized log-link events", () => {
    const result = buildTranscriptFromEvents([
      {
        seq: 1,
        type: "log_link",
        eventId: "log-a",
        timestamp: 1,
        actor: { kind: "system" },
        visibility: "host",
        textDelta: "Log link available.",
      },
      {
        seq: 2,
        type: "log_link",
        eventId: "log-b",
        timestamp: 2,
        actor: { kind: "system" },
        visibility: "host",
        textDelta: "Log link available.",
      },
    ])

    expect(result.committed.map((item) => item.id)).toEqual(["sys:log-a"])
  })
})
