import { describe, expect, it } from "vitest"
import {
  dumpTranscriptCellRecords,
  resolveTranscriptCellRole,
  resolveTranscriptDedupeKey,
  resolveTranscriptLifecycle,
  resolveTranscriptRenderModes,
  type TranscriptItem,
} from "../transcriptModel.js"

describe("transcriptModel cell contract", () => {
  it("classifies submitted user messages as durable user-request cells", () => {
    const item: TranscriptItem = {
      id: "msg:user-1",
      kind: "message",
      speaker: "user",
      text: "Show me the files in this workspace.",
      phase: "final",
      createdAt: 1,
      source: "conversation",
    }

    expect(resolveTranscriptCellRole(item)).toBe("user-request")
    expect(resolveTranscriptLifecycle(item)).toBe("committed")
    expect(resolveTranscriptRenderModes(item)).toEqual(["rich", "raw", "viewer"])
  })

  it("classifies streaming assistant messages as live assistant-message cells", () => {
    const item: TranscriptItem = {
      id: "msg:assistant-1",
      kind: "message",
      speaker: "assistant",
      text: "I am checking the workspace now.",
      phase: "streaming",
      createdAt: 2,
      source: "conversation",
      streaming: true,
    }

    expect(resolveTranscriptCellRole(item)).toBe("assistant-message")
    expect(resolveTranscriptLifecycle(item)).toBe("live")
  })

  it("classifies completed tool items as tool-result cells", () => {
    const item: TranscriptItem = {
      id: "tool:list-files",
      kind: "tool",
      toolKind: "result",
      text: "List .",
      status: "success",
      callId: "list-files",
      createdAt: 3,
      source: "tool",
    }

    expect(resolveTranscriptCellRole(item)).toBe("tool-result")
    expect(resolveTranscriptLifecycle(item)).toBe("committed")
    expect(resolveTranscriptDedupeKey(item)).toBeNull()
  })

  it("classifies pending, failed, and diff tool items with typed H5 roles", () => {
    const base = {
      id: "tool:base",
      kind: "tool" as const,
      text: "Bash(ls)",
      createdAt: 3,
      source: "tool" as const,
    }

    expect(
      resolveTranscriptCellRole({
        ...base,
        id: "tool:pending",
        toolKind: "call",
        status: "pending",
      }),
    ).toBe("tool-call")
    expect(
      resolveTranscriptCellRole({
        ...base,
        id: "tool:error",
        toolKind: "error",
        status: "error",
      }),
    ).toBe("tool-error")
    expect(
      resolveTranscriptCellRole({
        ...base,
        id: "tool:diff",
        toolKind: "result",
        status: "success",
        display: {
          diff_blocks: [{ kind: "diff", filePath: "src/example.ts", unified: "@@ -1 +1 @@\n-old\n+new" }],
        },
      }),
    ).toBe("diff")
  })

  it("keeps unknown tool kinds on the fallback tool-summary role", () => {
    const item: TranscriptItem = {
      id: "tool:status",
      kind: "tool",
      toolKind: "status",
      text: "[task] Task · completed · Review files",
      status: undefined,
      createdAt: 3,
      source: "tool",
    }

    expect(resolveTranscriptCellRole(item)).toBe("tool-summary")
  })

  it("assigns stable dedupe keys to status-like system cells", () => {
    const item: TranscriptItem = {
      id: "sys:log-link-1",
      kind: "system",
      systemKind: "log",
      text: "Log link available.",
      status: "success",
      createdAt: 4,
      source: "system",
    }

    expect(resolveTranscriptCellRole(item)).toBe("status")
    expect(resolveTranscriptDedupeKey(item)).toBe("log:Log link available.")
  })

  it("classifies permission transcript statuses as approval cells", () => {
    const item: TranscriptItem = {
      id: "sys:permission-1",
      kind: "system",
      systemKind: "status",
      text: "[permission] Write (edit)",
      status: "pending",
      createdAt: 4,
      source: "system",
    }

    expect(resolveTranscriptCellRole(item)).toBe("approval")
    expect(resolveTranscriptDedupeKey(item)).toBeNull()
  })

  it("classifies interrupt and cancel transcript statuses as interrupted cells", () => {
    const toolItem: TranscriptItem = {
      id: "tool:interrupt",
      kind: "tool",
      toolKind: "status",
      text: "[interrupt] stopping",
      status: "pending",
      createdAt: 4,
      source: "tool",
    }
    const systemItem: TranscriptItem = {
      id: "sys:cancel",
      kind: "system",
      systemKind: "status",
      text: "[cancel] acknowledged",
      status: "success",
      createdAt: 5,
      source: "system",
    }

    expect(resolveTranscriptCellRole(toolItem)).toBe("interrupted")
    expect(resolveTranscriptCellRole(systemItem)).toBe("interrupted")
    expect(resolveTranscriptDedupeKey(toolItem)).toBeNull()
  })

  it("allows explicit lifecycle and render-mode overrides", () => {
    const item: TranscriptItem = {
      id: "sys:landing",
      kind: "system",
      systemKind: "notice",
      text: "BreadBoard",
      createdAt: 5,
      source: "system",
      cellRole: "landing",
      lifecycle: "frozen",
      renderModes: ["rich", "viewer"],
      dedupeKey: "landing:session-1",
    }

    expect(resolveTranscriptCellRole(item)).toBe("landing")
    expect(resolveTranscriptLifecycle(item)).toBe("frozen")
    expect(resolveTranscriptRenderModes(item)).toEqual(["rich", "viewer"])
    expect(resolveTranscriptDedupeKey(item)).toBe("landing:session-1")
  })

  it("classifies local command output as command-result cells without status dedupe", () => {
    const item: TranscriptItem = {
      id: "cmd:help",
      kind: "system",
      systemKind: "command-result",
      text: "Slash commands\n/help — Show available slash commands.",
      status: "success",
      createdAt: 6,
      source: "system",
      cellRole: "command-result",
    }

    expect(resolveTranscriptCellRole(item)).toBe("command-result")
    expect(resolveTranscriptLifecycle(item)).toBe("committed")
    expect(resolveTranscriptDedupeKey(item)).toBeNull()
  })

  it("dumps JSON-safe validation records", () => {
    const items: TranscriptItem[] = [
      {
        id: "msg:user-1",
        kind: "message",
        speaker: "user",
        text: "Please inspect README.md and summarize it.",
        phase: "final",
        createdAt: 1,
        source: "conversation",
      },
      {
        id: "sys:usage-1",
        kind: "system",
        systemKind: "usage",
        text: "tok 100 · in 80 · out 20",
        status: "success",
        createdAt: 2,
        source: "system",
      },
    ]

    expect(dumpTranscriptCellRecords(items)).toEqual([
      {
        id: "msg:user-1",
        kind: "message",
        role: "user-request",
        lifecycle: "committed",
        source: "conversation",
        createdAt: 1,
        streaming: false,
        renderModes: ["rich", "raw", "viewer"],
        dedupeKey: null,
        textPreview: "Please inspect README.md and summarize it.",
        speaker: "user",
      },
      {
        id: "sys:usage-1",
        kind: "system",
        role: "status",
        lifecycle: "ephemeral",
        source: "system",
        createdAt: 2,
        streaming: false,
        renderModes: ["rich", "raw", "viewer"],
        dedupeKey: "usage:tok 100 · in 80 · out 20",
        textPreview: "tok 100 · in 80 · out 20",
        status: "success",
      },
    ])
  })
})
