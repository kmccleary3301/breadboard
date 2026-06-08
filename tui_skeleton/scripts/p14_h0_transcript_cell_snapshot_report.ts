import { promises as fs } from "node:fs"
import path from "node:path"
import { buildTranscript } from "../src/repl/transcriptBuilder.js"
import { dumpTranscriptCellRecords } from "../src/repl/transcriptModel.js"
import { buildTranscriptViewerModel } from "../src/repl/components/replView/controller/transcriptViewerModel.js"
import { renderStateToText } from "../src/commands/repl/renderText.js"

const outputPath = process.argv[2]
if (!outputPath) {
  throw new Error("Usage: tsx scripts/p14_h0_transcript_cell_snapshot_report.ts <output.json>")
}

const state = {
  sessionId: "h0-snapshot-report",
  status: "Ready",
  pendingResponse: false,
  mainFollowTail: true,
  mode: "build",
  permissionMode: "prompt",
  conversation: [
    { id: "user-1", speaker: "user", text: "Create a compact H0 transcript snapshot.", phase: "final", createdAt: 1 },
    { id: "assistant-1", speaker: "assistant", text: "H0 snapshot response.", phase: "final", createdAt: 2 },
  ],
  toolEvents: [
    { id: "tool-1", kind: "result", text: "Read(src/main.ts) -> 4 lines", status: "success", createdAt: 3 },
    { id: "status-1", kind: "status", text: "Renderer snapshot complete.", status: "success", createdAt: 4 },
  ],
  hints: [],
  stats: { eventCount: 4, toolCount: 1, lastTurn: 1, remote: false, model: "gpt-5.4-mini" },
  modelMenu: { status: "hidden" },
  skillsMenu: { status: "hidden" },
  inspectMenu: { status: "hidden" },
  liveSlots: [],
  rawEvents: [],
  completionReached: true,
  completionSeen: true,
  lastCompletion: { completed: true, summary: null },
  disconnected: false,
  viewPrefs: { collapseMode: "none", virtualization: "auto", richMarkdown: false },
  todoScopeKey: "main",
  todoScopeLabel: "main",
  todoScopeStale: false,
  todoScopeOrder: ["main"],
  rewindMenu: { status: "hidden" },
  todoStore: { revision: 0, updatedAt: 0, itemsById: {}, order: [] },
  todos: [],
  tasks: [],
  workGraph: {
    itemsById: {},
    itemOrder: [],
    lanesById: {},
    laneOrder: [],
    processedEventKeys: [],
    lastSeq: 0,
  },
  ctreeSnapshot: null,
  ctreeTree: null,
  ctreeTreeStatus: "idle",
  ctreeTreeError: null,
  ctreeStage: "FROZEN",
  ctreeIncludePreviews: false,
  ctreeSource: "auto",
  ctreeUpdatedAt: null,
} as const

const transcript = buildTranscript({
  conversation: state.conversation,
  toolEvents: state.toolEvents,
  rawEvents: state.rawEvents,
})
const items = [...transcript.committed, ...transcript.tail]
const report = {
  schema_version: "bb.p14.h0.transcript_cell_snapshot_report.v1",
  generated_at: new Date().toISOString(),
  transcript_cells: dumpTranscriptCellRecords(items),
  rich_widths: Object.fromEntries(
    [48, 80, 120].map((width) => [
      String(width),
      renderStateToText(state as any, {
        includeHeader: false,
        includeStatus: false,
        colors: false,
        asciiOnly: true,
        maxWidth: width,
      }).split(/\r?\n/),
    ]),
  ),
  viewer_lines: buildTranscriptViewerModel(items).lines,
  raw_projection: items.map((item) => ({
    id: item.id,
    kind: item.kind,
    text: item.text,
  })),
}

await fs.mkdir(path.dirname(outputPath), { recursive: true })
await fs.writeFile(outputPath, `${JSON.stringify(report, null, 2)}\n`, "utf8")
console.log(outputPath)
