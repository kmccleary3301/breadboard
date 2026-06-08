import { describe, expect, it } from "vitest"
import { buildTranscript } from "../../../repl/transcriptBuilder.js"
import { dumpTranscriptCellRecords } from "../../../repl/transcriptModel.js"
import { buildTranscriptViewerModel } from "../../../repl/components/replView/controller/transcriptViewerModel.js"
import { renderStateToText } from "../renderText.js"
import type { ReplState } from "../controller.js"

const state = (): ReplState =>
  ({
    sessionId: "h0-render-session",
    status: "Ready",
    pendingResponse: false,
    mainFollowTail: true,
    mode: "build",
    permissionMode: "prompt",
    conversation: [
      {
        id: "user-1",
        speaker: "user",
        text: "Summarize the renderer contract for H0.",
        phase: "final",
        createdAt: 1,
      },
      {
        id: "assistant-1",
        speaker: "assistant",
        text: "H0 renderer contract acknowledged.",
        phase: "final",
        createdAt: 2,
      },
    ],
    toolEvents: [
      {
        id: "tool-1",
        kind: "result",
        text: "Read(package.json) -> 12 lines",
        status: "success",
        createdAt: 3,
      },
      {
        id: "status-1",
        kind: "status",
        text: "Log link available.",
        status: "success",
        createdAt: 4,
      },
      {
        id: "status-2",
        kind: "status",
        text: "Log link available.",
        status: "success",
        createdAt: 5,
      },
    ],
    hints: [],
    stats: { eventCount: 5, toolCount: 1, lastTurn: 1, remote: false, model: "gpt-5.4-mini" },
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
  }) as ReplState

describe("H0 transcript cell render contract", () => {
  it("projects user, assistant, tool, and status cells into rich, raw, and viewer surfaces", () => {
    const current = state()
    const transcript = buildTranscript({
      conversation: current.conversation,
      toolEvents: current.toolEvents,
      rawEvents: current.rawEvents,
    })
    const items = [...transcript.committed, ...transcript.tail]
    const cells = dumpTranscriptCellRecords(items)

    expect(cells.map((cell) => cell.role)).toEqual([
      "user-request",
      "assistant-message",
      "tool-result",
      "status",
    ])
    expect(cells.every((cell) => cell.renderModes.includes("rich") && cell.renderModes.includes("raw") && cell.renderModes.includes("viewer"))).toBe(true)

    const viewer = buildTranscriptViewerModel(items)
    expect(viewer.lines.join("\n")).toContain("Summarize the renderer contract")
    expect(viewer.lines.join("\n")).toContain("H0 renderer contract acknowledged.")
    expect(viewer.lines.join("\n")).toContain("Read(package.json)")

    for (const width of [48, 80, 120]) {
      const rendered = renderStateToText(current, {
        includeHeader: false,
        includeStatus: false,
        colors: false,
        asciiOnly: true,
        maxWidth: width,
      })
      expect(rendered).toContain("> Summarize the renderer contract")
      expect(rendered).toContain("* H0 renderer contract acknowledged.")
      expect(rendered).toContain("Read(package.json)")
    }
  })
})
