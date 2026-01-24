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

  it("matches streaming fixture output", () => {
    const state: ReplState = {
      sessionId: "sess-stream",
      status: "Assistant thinking…",
      pendingResponse: true,
      conversation: [
        { id: "conv-1", speaker: "user", text: "Hello", phase: "final", createdAt: 0 },
        { id: "conv-2", speaker: "assistant", text: "Streaming partial", phase: "streaming", createdAt: 1 },
      ],
      toolEvents: [],
      liveSlots: [],
      hints: [],
      stats: { eventCount: 0, toolCount: 0, lastTurn: 1, remote: false, model: "openai/gpt-5.1-mini-codex" },
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
    compareSnapshotToFile(output, path.join("tests", "fixtures", "streaming_state_output.txt"))
  })

  it("matches diff streaming fixture output", () => {
    const diffText = [
      "diff --git a/foo.txt b/foo.txt",
      "index 1234567..89abcde 100644",
      "--- a/foo.txt",
      "+++ b/foo.txt",
      "@@ -1,6 +1,8 @@",
      "-old line 1",
      "+new line 1",
      "+new line 2",
      " unchanged",
      "-old line 2",
      "+new line 3",
      "@@ -10,2 +12,4 @@",
      "-tail old",
      "+tail new",
      "+tail extra",
      " end",
    ].join("\n")

    const state: ReplState = {
      sessionId: "sess-diff",
      status: "Ready",
      pendingResponse: false,
      conversation: [
        { id: "conv-1", speaker: "user", text: "Apply patch", phase: "final", createdAt: 0 },
        {
          id: "conv-2",
          speaker: "assistant",
          text: diffText,
          phase: "final",
          createdAt: 1,
          richBlocks: [
            { id: "b1", type: "paragraph", isFinalized: true, payload: { raw: diffText } },
          ],
        },
      ],
      toolEvents: [],
      liveSlots: [],
      rawEvents: [],
      hints: [],
      stats: { eventCount: 6, toolCount: 0, lastTurn: 2, remote: false, model: "openai/gpt-5.1-mini-codex" },
      modelMenu: { status: "hidden" },
      skillsMenu: { status: "hidden" },
      inspectMenu: { status: "hidden" },
      completionReached: false,
      completionSeen: false,
      lastCompletion: null,
      disconnected: false,
      viewPrefs: { collapseMode: "auto", virtualization: "auto", richMarkdown: true },
      rewindMenu: { status: "hidden" },
      todos: [],
      tasks: [],
    }
    const output = renderStateToText(state, {
      includeHeader: false,
      includeStatus: false,
    })
    compareSnapshotToFile(output, path.join("tests", "fixtures", "diff_streaming_state_output.txt"))
  })
})
