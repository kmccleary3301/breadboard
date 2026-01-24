import React from "react"
import { render as inkRender } from "ink"
import { describe, it, expect, vi } from "vitest"
import { render } from "ink-testing-library"
import { ReplView, ConversationEntry, StreamStats } from "../src/commands/repl.js"
import { createMockStdin, createWritableCapture } from "./helpers/inkStreams.js"

const stripAnsi = (value: string): string => value.replaceAll(/\u001B\[[0-9;]*m/g, "")
const delay = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms))

describe("ReplView", () => {
  it("renders conversation and tool panes", async () => {
    const conversation: ConversationEntry[] = [
      { id: "conv-a", speaker: "assistant", text: "Hello", phase: "final", createdAt: 0 },
      { id: "conv-b", speaker: "user", text: "Run tests", phase: "final", createdAt: 1 },
    ]
    const toolEvents = [
      { id: "tool-1", kind: "call", text: "[call] run_shell", status: "pending", createdAt: 0 },
      { id: "tool-2", kind: "result", text: "[result] success", status: "success", createdAt: 1 },
    ]
    const stats: StreamStats = { eventCount: 4, toolCount: 2, lastTurn: 2, remote: true, model: "anthropic/claude-haiku-4-5-20251001" }
    const hints = ["Hint 1", "Hint 2"]
    const stdout = createWritableCapture()
    const stderr = createWritableCapture()
    const stdin = createMockStdin()
    const instance = inkRender(
      <ReplView
        sessionId="sess-1"
        conversation={conversation}
        toolEvents={toolEvents}
        liveSlots={[]}
        status="Streaming..."
        pendingResponse={false}
        disconnected={false}
        hints={hints}
        stats={stats}
        modelMenu={{ status: "hidden" }}
        skillsMenu={{ status: "hidden" }}
        inspectMenu={{ status: "hidden" }}
        guardrailNotice={null}
        viewClearAt={null}
        viewPrefs={{ collapseMode: "auto", virtualization: "auto", richMarkdown: false }}
        todos={[]}
        tasks={[]}
        permissionRequest={null}
        permissionError={null}
        permissionQueueDepth={0}
        rewindMenu={{ status: "hidden" }}
        onSubmit={async () => {}}
        onModelMenuOpen={async () => {}}
        onModelSelect={async () => {}}
        onModelMenuCancel={() => {}}
        onSkillsMenuOpen={async () => {}}
        onSkillsMenuCancel={() => {}}
        onSkillsApply={async () => {}}
        onGuardrailToggle={() => {}}
        onGuardrailDismiss={() => {}}
        onPermissionDecision={async () => {}}
        onRewindClose={() => {}}
        onRewindRestore={async () => {}}
        onCtreeRequest={async () => {}}
        onListFiles={async () => []}
        onReadFile={async (path) => ({ path, content: "", truncated: false, total_bytes: 0 })}
      />,
      {
        stdout: stdout.stream,
        stderr: stderr.stream,
        stdin,
        exitOnCtrlC: false,
        patchConsole: false,
        debug: true,
      },
    )
    const frame = stripAnsi(stdout.lastFrame())
    expect(frame).toContain("ASSISTANT")
    expect(frame).toContain("Run tests")
    expect(frame).toContain("run_shell")
    expect(frame).toContain("success")
    instance.unmount()
    instance.cleanup()
  })

  it("renders malformed tool args without crashing (fuzz)", async () => {
    const previousKeymap = process.env.BREADBOARD_TUI_KEYMAP
    process.env.BREADBOARD_TUI_KEYMAP = "claude"
    try {
      const base = "{\"tool\":{\"name\":\"write_file\",\"args\":{\"path\":\"notes.txt\",\"content\":\"Hello world\"}}}"
      const fragments = [12, 24, 36, 48, 60].map((length) => base.slice(0, length))
      const stats: StreamStats = { eventCount: 1, toolCount: 1, lastTurn: 1, remote: true, model: "openai/gpt-5.1-mini-codex" }

      for (let i = 0; i < fragments.length; i += 1) {
        const toolEvents = [
          { id: `tool-${i}`, kind: "call", text: `[call] ${fragments[i]}`, status: "pending", createdAt: i },
        ]
        const { lastFrame, unmount } = render(
          <ReplView
            sessionId="sess-fuzz"
            conversation={[]}
            toolEvents={toolEvents}
            liveSlots={[]}
            status="Idle"
            pendingResponse={false}
            disconnected={false}
            hints={[]}
            stats={stats}
            modelMenu={{ status: "hidden" }}
            skillsMenu={{ status: "hidden" }}
            inspectMenu={{ status: "hidden" }}
            guardrailNotice={null}
            viewClearAt={null}
            viewPrefs={{ collapseMode: "auto", virtualization: "auto", richMarkdown: false }}
            todos={[]}
            tasks={[]}
            permissionRequest={null}
            permissionError={null}
            permissionQueueDepth={0}
            rewindMenu={{ status: "hidden" }}
            onSubmit={async () => {}}
            onModelMenuOpen={async () => {}}
            onModelSelect={async () => {}}
            onModelMenuCancel={() => {}}
            onSkillsMenuOpen={async () => {}}
            onSkillsMenuCancel={() => {}}
            onSkillsApply={async () => {}}
            onGuardrailToggle={() => {}}
            onGuardrailDismiss={() => {}}
            onPermissionDecision={async () => {}}
            onRewindClose={() => {}}
            onRewindRestore={async () => {}}
            onCtreeRequest={async () => {}}
            onListFiles={async () => []}
            onReadFile={async (path) => ({ path, content: "", truncated: false, total_bytes: 0 })}
          />,
        )
        await delay(10)
        const frame = stripAnsi(lastFrame())
        expect(frame.length).toBeGreaterThan(0)
        unmount()
      }
    } finally {
      if (previousKeymap === undefined) {
        delete process.env.BREADBOARD_TUI_KEYMAP
      } else {
        process.env.BREADBOARD_TUI_KEYMAP = previousKeymap
      }
    }
  })

  it("renders malformed tool args without crashing (fuzz)", async () => {
    const previousKeymap = process.env.BREADBOARD_TUI_KEYMAP
    process.env.BREADBOARD_TUI_KEYMAP = "claude"
    try {
      const base = "{\"tool\":{\"name\":\"write_file\",\"args\":{\"path\":\"notes.txt\",\"content\":\"Hello world\"}}}"
      const fragments = [12, 24, 36, 48, 60].map((length) => base.slice(0, length))
      const stats: StreamStats = { eventCount: 1, toolCount: 1, lastTurn: 1, remote: true, model: "openai/gpt-5.1-mini-codex" }

      for (let i = 0; i < fragments.length; i += 1) {
        const toolEvents = [
          { id: `tool-${i}`, kind: "call", text: `[call] ${fragments[i]}`, status: "pending", createdAt: i },
        ]
        const { lastFrame, unmount } = render(
          <ReplView
            sessionId="sess-fuzz"
            conversation={[]}
            toolEvents={toolEvents}
            liveSlots={[]}
            status="Idle"
            pendingResponse={false}
            disconnected={false}
            hints={[]}
            stats={stats}
            modelMenu={{ status: "hidden" }}
            skillsMenu={{ status: "hidden" }}
            inspectMenu={{ status: "hidden" }}
            guardrailNotice={null}
            viewClearAt={null}
            viewPrefs={{ collapseMode: "auto", virtualization: "auto", richMarkdown: false }}
            todos={[]}
            tasks={[]}
            permissionRequest={null}
            permissionError={null}
            permissionQueueDepth={0}
            rewindMenu={{ status: "hidden" }}
            onSubmit={async () => {}}
            onModelMenuOpen={async () => {}}
            onModelSelect={async () => {}}
            onModelMenuCancel={() => {}}
            onSkillsMenuOpen={async () => {}}
            onSkillsMenuCancel={() => {}}
            onSkillsApply={async () => {}}
            onGuardrailToggle={() => {}}
            onGuardrailDismiss={() => {}}
            onPermissionDecision={async () => {}}
            onRewindClose={() => {}}
            onRewindRestore={async () => {}}
            onCtreeRequest={async () => {}}
            onListFiles={async () => []}
            onReadFile={async (path) => ({ path, content: "", truncated: false, total_bytes: 0 })}
          />,
        )
        await delay(10)
        const frame = stripAnsi(lastFrame())
        expect(frame.length).toBeGreaterThan(0)
        unmount()
      }
    } finally {
      if (previousKeymap === undefined) {
        delete process.env.BREADBOARD_TUI_KEYMAP
      } else {
        process.env.BREADBOARD_TUI_KEYMAP = previousKeymap
      }
    }
  })

  it("opens the C-Tree viewer on Ctrl+Y", async () => {
    const stats: StreamStats = { eventCount: 0, toolCount: 0, lastTurn: 1, remote: true, model: "m" }
    const ctreeModel = {
      nodesById: {
        n1: { id: "n1", digest: "d1", kind: "message", turn: 1, payload: { text: "hello" } },
      },
      nodeOrder: ["n1"],
      byTurn: { "1": ["n1"] },
      snapshot: {
        schema_version: "0.1",
        node_count: 1,
        event_count: 1,
        last_id: "n1",
        node_hash: "h1",
      },
    }
    const ctreeTree = {
      source: "memory",
      stage: "FROZEN",
      root_id: "ctrees:root",
      nodes: [
        { id: "ctrees:root", parent_id: null, kind: "root", label: "C-Tree", turn: null, meta: {} },
        { id: "ctrees:turn:1", parent_id: "ctrees:root", kind: "turn", label: "Turn 1", turn: 1, meta: {} },
      ],
    }

    const { stdin, lastFrame, unmount } = render(
      <ReplView
        sessionId="sess-1"
        conversation={[]}
        toolEvents={[]}
        liveSlots={[]}
        status="Idle"
        pendingResponse={false}
        disconnected={false}
        hints={[]}
        stats={stats}
        modelMenu={{ status: "hidden" }}
        skillsMenu={{ status: "hidden" }}
        inspectMenu={{ status: "hidden" }}
        guardrailNotice={null}
        viewClearAt={null}
        viewPrefs={{ collapseMode: "auto", virtualization: "auto", richMarkdown: false }}
        todos={[]}
        tasks={[]}
        ctreeModel={ctreeModel as any}
        ctreeTree={ctreeTree as any}
        ctreeTreeStatus="idle"
        ctreeTreeError={null}
        ctreeStage="FROZEN"
        ctreeIncludePreviews={false}
        ctreeSource="memory"
        ctreeUpdatedAt={Date.now()}
        permissionRequest={null}
        permissionError={null}
        permissionQueueDepth={0}
        rewindMenu={{ status: "hidden" }}
        onSubmit={async () => {}}
        onModelMenuOpen={async () => {}}
        onModelSelect={async () => {}}
        onModelMenuCancel={() => {}}
        onSkillsMenuOpen={async () => {}}
        onSkillsMenuCancel={() => {}}
        onSkillsApply={async () => {}}
        onGuardrailToggle={() => {}}
        onGuardrailDismiss={() => {}}
        onPermissionDecision={async () => {}}
        onRewindClose={() => {}}
        onRewindRestore={async () => {}}
        onCtreeRequest={async () => {}}
        onListFiles={async () => []}
        onReadFile={async (path) => ({ path, content: "", truncated: false, total_bytes: 0 })}
      />,
    )

    stdin.write("\u0019")
    await delay(10)

    const frame = stripAnsi(lastFrame())
    expect(frame).toContain("C-Tree")
    expect(frame).toContain("Turn 1")
    unmount()
  })

  it("sends deny-stop when Esc pressed during permission prompt", async () => {
    const stdinPatch = (stream: any) => {
      stream.ref = () => stream
      stream.unref = () => stream
      return stream
    }
    const onPermissionDecision = vi.fn(async () => {})

    const stats: StreamStats = { eventCount: 0, toolCount: 0, lastTurn: 0, remote: true, model: "m" }
    const permissionRequest = {
      requestId: "perm-1",
      tool: "write_file",
      kind: "edit",
      rewindable: true,
      summary: "Permission required.",
      diffText: null,
      ruleSuggestion: null,
      defaultScope: "project" as const,
      createdAt: Date.now(),
    }

    const { stdin, unmount } = render(
      <ReplView
        sessionId="sess-1"
        conversation={[]}
        toolEvents={[]}
        liveSlots={[]}
        status="Permission required"
        pendingResponse={false}
        disconnected={false}
        hints={[]}
        stats={stats}
        modelMenu={{ status: "hidden" }}
        skillsMenu={{ status: "hidden" }}
        inspectMenu={{ status: "hidden" }}
        guardrailNotice={null}
        viewClearAt={null}
        viewPrefs={{ collapseMode: "auto", virtualization: "auto", richMarkdown: false }}
        todos={[]}
        tasks={[]}
        permissionRequest={permissionRequest}
        permissionError={null}
        permissionQueueDepth={0}
        rewindMenu={{ status: "hidden" }}
        onSubmit={async () => {}}
        onModelMenuOpen={async () => {}}
        onModelSelect={async () => {}}
        onModelMenuCancel={() => {}}
        onSkillsMenuOpen={async () => {}}
        onSkillsMenuCancel={() => {}}
        onSkillsApply={async () => {}}
        onGuardrailToggle={() => {}}
        onGuardrailDismiss={() => {}}
        onPermissionDecision={onPermissionDecision}
        onRewindClose={() => {}}
        onRewindRestore={async () => {}}
        onCtreeRequest={async () => {}}
        onListFiles={async () => []}
        onReadFile={async (path) => ({ path, content: "", truncated: false, total_bytes: 0 })}
      />,
    )

    stdinPatch(stdin).write("\u001b")
    await delay(200)

    expect(onPermissionDecision).toHaveBeenCalledWith({ kind: "deny-stop" })

    unmount()
  })
})
