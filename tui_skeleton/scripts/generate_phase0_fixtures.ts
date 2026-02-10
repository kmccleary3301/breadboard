import { writeFileSync, mkdirSync } from "node:fs"
import path from "node:path"
import type { ReplState } from "../src/commands/repl/controller.ts"
import { renderStateToText } from "../src/commands/repl/renderText.ts"

const fixturesDir = path.resolve("tests", "fixtures")
mkdirSync(fixturesDir, { recursive: true })

const writeFixture = (name: string, state: ReplState, options: Record<string, unknown> = {}) => {
  const output = renderStateToText(state, {
    includeHeader: false,
    includeStatus: false,
    colors: false,
    asciiOnly: true,
    maxWidth: 80,
    ...options,
  })
  writeFileSync(path.join(fixturesDir, name), `${output.trimEnd()}\n`, "utf8")
}

const baseState = {
  liveSlots: [],
  rawEvents: [],
  hints: [],
  stats: { eventCount: 0, toolCount: 0, lastTurn: 1, remote: false, model: "openai/gpt-5.1-mini-codex" },
  modelMenu: { status: "hidden" },
  skillsMenu: { status: "hidden" },
  inspectMenu: { status: "hidden" },
  completionReached: false,
  completionSeen: false,
  lastCompletion: null,
  disconnected: false,
  guardrailNotice: null,
  viewPrefs: { collapseMode: "auto", virtualization: "auto", richMarkdown: false, toolRail: true, toolInline: false, rawStream: false, showReasoning: false },
  permissionRequest: null,
  permissionError: null,
  permissionQueueDepth: 0,
  rewindMenu: { status: "hidden" },
  todos: [],
  tasks: [],
  ctreeTreeStatus: "idle",
  ctreeStage: "FROZEN",
  ctreeIncludePreviews: false,
  ctreeSource: "auto",
} satisfies Partial<ReplState>

writeFixture(
  "streaming_pending_basic.txt",
  {
    ...(baseState as ReplState),
    sessionId: "sess-stream-basic",
    status: "Assistant thinking…",
    pendingResponse: true,
    conversation: [
      { id: "conv-1", speaker: "user", text: "List files in the current directory.", phase: "final", createdAt: 1 },
      { id: "conv-2", speaker: "assistant", text: "Sure — checking now…", phase: "streaming", createdAt: 3 },
    ],
    toolEvents: [
      {
        id: "tool-1",
        kind: "call",
        text: "List(./)\n│ Found 0 items",
        status: "pending",
        createdAt: 2,
        callId: "call-1",
      },
    ],
  } as ReplState,
)

writeFixture(
  "ascii_only_pending.txt",
  {
    ...(baseState as ReplState),
    sessionId: "sess-ascii-pending",
    status: "Assistant responding…",
    pendingResponse: true,
    conversation: [
      { id: "conv-1", speaker: "user", text: "Ping.", phase: "final", createdAt: 1 },
      { id: "conv-2", speaker: "assistant", text: "Pong.", phase: "streaming", createdAt: 2 },
    ],
    toolEvents: [],
  } as ReplState,
  { maxWidth: 60 },
)

const detailLines = Array.from({ length: 30 }, (_, idx) => `| item ${idx + 1}`)
writeFixture(
  "tool_call_result_collapsed.txt",
  {
    ...(baseState as ReplState),
    sessionId: "sess-tool-collapse",
    status: "Ready",
    pendingResponse: false,
    conversation: [{ id: "conv-1", speaker: "user", text: "List files.", phase: "final", createdAt: 1 }],
    toolEvents: [
      {
        id: "tool-1",
        kind: "result",
        text: ["List(./)", ...detailLines].join("\n"),
        status: "success",
        createdAt: 2,
      },
    ],
  } as ReplState,
)

const diffLines = [
  "ApplyPatch(foo.txt)",
  "diff --git a/foo.txt b/foo.txt",
  "index 1234567..89abcde 100644",
  "--- a/foo.txt",
  "+++ b/foo.txt",
  "@@ -1,5 +1,6 @@",
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
]
const padding = Array.from({ length: 18 }, (_, idx) => ` context ${idx + 1}`)
writeFixture(
  "diff_block_collapsed.txt",
  {
    ...(baseState as ReplState),
    sessionId: "sess-diff-collapse",
    status: "Ready",
    pendingResponse: false,
    conversation: [{ id: "conv-1", speaker: "user", text: "Apply patch.", phase: "final", createdAt: 1 }],
    toolEvents: [
      {
        id: "tool-1",
        kind: "result",
        text: [...diffLines, ...padding].join("\n"),
        status: "success",
        createdAt: 2,
      },
    ],
  } as ReplState,
)

console.log("[generate_phase0_fixtures] wrote fixtures to tests/fixtures")
