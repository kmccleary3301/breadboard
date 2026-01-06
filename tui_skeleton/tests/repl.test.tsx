import React from "react"
import { render as inkRender } from "ink"
import { describe, it, expect } from "vitest"
import { ReplView, ConversationEntry, StreamStats } from "../src/commands/repl.js"
import { createMockStdin, createWritableCapture } from "./helpers/inkStreams.js"

const stripAnsi = (value: string): string => value.replaceAll(/\u001B\[[0-9;]*m/g, "")

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
})
