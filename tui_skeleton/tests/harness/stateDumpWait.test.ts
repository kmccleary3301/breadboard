import { describe, expect, it } from "vitest"
import { matchesStateWaitCriteria } from "../../scripts/harness/stateDumpWait.js"

describe("matchesStateWaitCriteria", () => {
  it("derives last conversation and counts from full state dumps", () => {
    const record = {
      timestamp: 20,
      state: {
        pendingResponse: false,
        conversation: [
          { speaker: "user", phase: "final", text: "first" },
          { speaker: "assistant", phase: "final", text: "done" },
        ],
      },
    }

    expect(
      matchesStateWaitCriteria(
        record,
        {
          fresh: true,
          pendingResponse: false,
          conversationCountAtLeast: 2,
          lastConversationSpeaker: "assistant",
          lastConversationPhase: "final",
          lastConversationPreviewIncludes: "done",
        },
        10,
      ),
    ).toBe(true)
  })

  it("derives last tool event from full state dumps", () => {
    const record = {
      timestamp: 20,
      state: {
        toolEvents: [
          { kind: "exec", status: "running", text: "first" },
          { kind: "exec", status: "success", text: "npm test" },
        ],
      },
    }

    expect(
      matchesStateWaitCriteria(
        record,
        {
          lastToolEventKind: "exec",
          lastToolEventStatus: "success",
          lastToolEventTextIncludes: "npm test",
        },
        10,
      ),
    ).toBe(true)
  })
})
