import { describe, expect, it } from "vitest"
import { describeTerminalFailedState, matchesStateWaitCriteria, type StateDumpRecord } from "../scripts/harness/stateDumpWait"

describe("matchesStateWaitCriteria", () => {
  const record: StateDumpRecord = {
    timestamp: 25,
    state: {
      pendingResponse: true,
      mainFollowTail: false,
      status: "Responding · live tool output",
      lifecycle: {
        mode: "local-owned",
        owned: true,
        pid: 12345,
        restartPolicy: "bounded",
      },
      liveSlots: [{ status: "pending", text: "bash: demo • live stdout" }],
      stats: { eventCount: 14 },
      counts: { conversation: 3 },
      lastConversation: {
        speaker: "assistant",
        phase: "streaming",
        preview: "hello world",
      },
      lastToolEvent: {
        kind: "tool_result",
        status: "ok",
        text: "Tool\nstdout 1 line\n/tmp/demo",
      },
    },
  }

  it("matches full criteria", () => {
    expect(
      matchesStateWaitCriteria(
        record,
        {
          fresh: true,
          pendingResponse: true,
          mainFollowTail: false,
          statusIncludes: "live tool",
          conversationCountAtLeast: 2,
          eventCountAtLeast: 10,
          lastConversationSpeaker: "assistant",
          lastConversationPhase: "streaming",
          lastConversationPreviewIncludes: "hello",
          lastToolEventKind: "tool_result",
          lastToolEventStatus: "ok",
          lastToolEventTextIncludes: "/tmp/demo",
          lastLiveSlotStatus: "pending",
          lastLiveSlotTextIncludes: "live stdout",
          lifecycleMode: "local-owned",
          lifecycleOwned: true,
          lifecyclePidPresent: true,
        },
        20,
      ),
    ).toBe(true)
  })

  it("rejects stale fresh records", () => {
    expect(matchesStateWaitCriteria(record, { fresh: true }, 25)).toBe(false)
  })

  it("rejects mismatched criteria", () => {
    expect(matchesStateWaitCriteria(record, { mainFollowTail: true })).toBe(false)
    expect(matchesStateWaitCriteria(record, { lastToolEventStatus: "error" })).toBe(false)
    expect(matchesStateWaitCriteria(record, { lastConversationPreviewIncludes: "nope" })).toBe(false)
  })

  it("rejects missing state", () => {
    expect(matchesStateWaitCriteria(null, {})).toBe(false)
    expect(matchesStateWaitCriteria({ timestamp: 1 }, {})).toBe(false)
  })

  it("describes fresh halted states that cannot satisfy final-assistant criteria", () => {
    const halted: StateDumpRecord = {
      timestamp: 30,
      state: {
        pendingResponse: false,
        status: "Halted (max_steps_exhausted)",
        lastConversation: {
          speaker: "system",
          phase: "final",
          preview: "[halted] max_steps_exhausted",
        },
      },
    }

    expect(
      describeTerminalFailedState(
        halted,
        {
          fresh: true,
          pendingResponse: false,
          lastConversationSpeaker: "assistant",
          lastConversationPhase: "final",
        },
        20,
      ),
    ).toContain("terminal failed state reached")
  })

  it("does not describe stale or matching halted records as terminal wait failures", () => {
    const halted: StateDumpRecord = {
      timestamp: 30,
      state: {
        pendingResponse: false,
        status: "Halted (max_steps_exhausted)",
        lastConversation: {
          speaker: "system",
          phase: "final",
          preview: "[halted] max_steps_exhausted",
        },
      },
    }

    expect(describeTerminalFailedState(halted, { fresh: true }, 30)).toBeNull()
    expect(
      describeTerminalFailedState(
        halted,
        {
          pendingResponse: false,
          lastConversationSpeaker: "system",
          lastConversationPhase: "final",
        },
      ),
    ).toBeNull()
  })
})
