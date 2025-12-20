import path from "node:path"
import { describe, it } from "vitest"
import type { ReplState } from "../src/commands/repl/controller.js"
import { renderStateToText } from "../src/commands/repl/renderText.js"
import { compareSnapshotToFile } from "./helpers/snapshot.js"

describe("replay snapshot harness", () => {
  it("matches fixture output", () => {
    const state: ReplState = {
      sessionId: "sess-abc",
      status: "Ready",
      pendingResponse: false,
      conversation: [{ id: "conv-1", speaker: "user", text: "Run integration tests", phase: "final" }],
      toolEvents: [
        {
          id: "tool-1",
          kind: "call",
          text: '[call] run_tests {"suite":"integration"}',
          status: "pending",
          createdAt: 0,
        },
      ],
      liveSlots: [
        {
          id: "slot-1",
          text: 'Tool running — run_tests {"suite":"integration"}',
          color: "#FACC15",
          status: "pending",
          updatedAt: 0,
        },
      ],
      hints: [],
      stats: { eventCount: 3, toolCount: 1, lastTurn: 1, remote: false, model: "anthropic/claude-haiku-4-5-20251001" },
      modelMenu: { status: "hidden" },
      completionReached: false,
      completionSeen: false,
      lastCompletion: null,
      disconnected: false,
    }
    const output = renderStateToText(state, {
      includeHeader: false,
      includeStatus: false,
    })
    compareSnapshotToFile(output, path.join("tests", "fixtures", "sample_state_output.txt"))
  })
})
