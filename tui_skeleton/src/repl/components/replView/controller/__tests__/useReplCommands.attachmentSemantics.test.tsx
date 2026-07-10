import React from "react"
import { Text } from "ink"
import { render } from "ink-testing-library"
import { describe, expect, it, vi } from "vitest"
import { useReplCommands } from "../useReplCommands.js"

const flush = () => new Promise((resolve) => setTimeout(resolve, 0))

const attachment = {
  id: "att-1",
  mime: "image/png",
  base64: "abc",
  size: 68,
}

const baseContext = (overrides: Record<string, unknown> = {}) => ({
  closeFilePicker: vi.fn(),
  configPath: null,
  fileMentionConfig: {
    mode: "reference",
    maxInlineBytesPerFile: 1_000,
    maxInlineBytesTotal: 2_000,
    snippetHeadLines: 4,
    snippetTailLines: 4,
    snippetMaxBytes: 1_000,
  },
  handleLineEdit: vi.fn(),
  onListFiles: vi.fn(async () => []),
  onReadFile: vi.fn(),
  onReadWorkingTreeDiff: undefined,
  onExportWorkingTreeDiffPatch: undefined,
  onCopyWorkingTreeDiffPatch: undefined,
  pushCommandResult: vi.fn(),
  pushHistoryEntry: vi.fn(),
  setSuggestIndex: vi.fn(),
  input: "",
  cursor: 0,
  attachments: [attachment],
  clearAttachments: vi.fn(),
  fileMentions: [],
  clearFileMentions: vi.fn(),
  onSubmit: vi.fn(async () => undefined),
  setTodosOpen: vi.fn(),
  setUsageOpen: vi.fn(),
  setTasksOpen: vi.fn(),
  setTaskSearchQuery: vi.fn(),
  setTaskStatusFilter: vi.fn(),
  setTaskLaneFilter: vi.fn(),
  setTaskIndex: vi.fn(),
  setTaskScroll: vi.fn(),
  taskLaneOrder: ["lane-a"],
  selectedTask: { id: "task-1", laneId: "lane-a" },
  setTaskFocusLaneId: vi.fn(),
  setTaskFocusViewOpen: vi.fn(),
  setTaskFocusFollowTail: vi.fn(),
  setTaskFocusRawMode: vi.fn(),
  setTaskFocusTailLines: vi.fn(),
  taskFocusDefaultTailLines: 24,
  taskFocusFollowTail: true,
  requestTaskTail: vi.fn(),
  taskboardInputQuarantineUntilRef: { current: 0 },
  setShortcutsOpen: vi.fn(),
  enterTranscriptViewer: vi.fn(),
  jumpTranscriptToAnchor: vi.fn(() => false),
  refreshRecentSessions: vi.fn(),
  setRecentSessionsOpen: vi.fn(),
  onModelMenuOpen: vi.fn(),
  onSkillsMenuOpen: vi.fn(),
  inputLocked: false,
  pendingResponse: false,
  closePalette: vi.fn(),
  saveTranscriptExport: vi.fn(async () => undefined),
  ...overrides,
})

const renderCommands = async (context: Record<string, unknown>) => {
  let commands: any = null
  const Probe = () => {
    commands = useReplCommands(context)
    return <Text>probe</Text>
  }
  render(<Probe />)
  await flush()
  if (!commands) throw new Error("commands hook did not render")
  return commands
}

describe("useReplCommands attachment semantics", () => {
  it("submits attachments and clears the queue at the submission boundary", async () => {
    const context = baseContext()
    const order: string[] = []
    context.clearAttachments = vi.fn(() => order.push("clear"))
    context.onSubmit = vi.fn(async () => {
      order.push("submit")
    })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("describe this image")
    expect(context.onSubmit).toHaveBeenCalledWith(expect.stringContaining("[Attachment 1: image/png"), [attachment])
    expect(context.clearAttachments).toHaveBeenCalledTimes(1)
    expect(order).toEqual(["clear", "submit"])
  })

  it("embeds queued inline file mentions into the submitted model-visible payload and clears the file queue", async () => {
    const context = baseContext({
      attachments: [],
      fileMentionConfig: {
        mode: "inline",
        maxInlineBytesPerFile: 1_000,
        maxInlineBytesTotal: 2_000,
        snippetHeadLines: 2,
        snippetTailLines: 1,
        snippetMaxBytes: 1_000,
      },
      fileMentions: [{
        id: "file-a",
        path: "src/example.ts",
        size: 42,
        requestedMode: "inline",
        addedAt: 1,
      }],
      onReadFile: vi.fn(async () => ({
        path: "src/example.ts",
        content: "export const value = 42\n",
        truncated: false,
        total_bytes: 24,
      })),
    })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("summarize this file")
    const submitted = (context.onSubmit as any).mock.calls[0][0]
    expect(submitted).toContain("summarize this file")
    expect(submitted).toContain("### File: src/example.ts")
    expect(submitted).toContain("export const value = 42")
    expect(context.clearFileMentions).toHaveBeenCalledTimes(1)
  })

  it("uses bounded snippets for oversized queued file mentions and preserves truncation truth copy", async () => {
    const context = baseContext({
      attachments: [],
      fileMentionConfig: {
        mode: "auto",
        maxInlineBytesPerFile: 10,
        maxInlineBytesTotal: 20,
        snippetHeadLines: 1,
        snippetTailLines: 1,
        snippetMaxBytes: 10,
      },
      fileMentions: [{
        id: "file-large",
        path: "large.md",
        size: 10_000,
        requestedMode: "auto",
        addedAt: 1,
      }],
      onReadFile: vi.fn(async (_path: string, options: any) => ({
        path: "large.md",
        content: `head\n...\ntail`,
        truncated: options?.mode === "snippet",
        total_bytes: 10_000,
      })),
    })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("summarize large file")
    expect(context.onReadFile).toHaveBeenCalledWith(
      "large.md",
      expect.objectContaining({ mode: "snippet", headLines: 1, tailLines: 1, maxBytes: 10 }),
    )
    const submitted = (context.onSubmit as any).mock.calls[0][0]
    expect(submitted).toContain("### File: large.md")
    expect(submitted).toContain("This file is truncated")
    expect(context.clearFileMentions).toHaveBeenCalledTimes(1)
  })

  it("turns binary or unreadable queued file mentions into explicit model-visible errors", async () => {
    const context = baseContext({
      attachments: [],
      fileMentionConfig: {
        mode: "inline",
        maxInlineBytesPerFile: 1_000,
        maxInlineBytesTotal: 2_000,
        snippetHeadLines: 2,
        snippetTailLines: 1,
        snippetMaxBytes: 1_000,
      },
      fileMentions: [{
        id: "file-bin",
        path: "asset.bin",
        size: 8,
        requestedMode: "inline",
        addedAt: 1,
      }],
      onReadFile: vi.fn(async () => {
        throw new Error("Binary file cannot be attached as text context: asset.bin")
      }),
    })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("inspect asset")
    const submitted = (context.onSubmit as any).mock.calls[0][0]
    expect(submitted).toContain("File asset.bin: Error: Binary file cannot be attached as text context: asset.bin")
    expect(context.clearFileMentions).toHaveBeenCalledTimes(1)
  })

  it("does not queue attachment prompts while another response is running", async () => {
    const context = baseContext({ pendingResponse: true })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("describe this image")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.clearAttachments).not.toHaveBeenCalled()
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "Queued prompt",
      expect.arrayContaining([expect.stringContaining("cannot be queued")]),
    )
    expect(context.handleLineEdit).not.toHaveBeenCalledWith("", 0)
  })

  it("uses the single queued prompt panel instead of adding a duplicate command-result notice", async () => {
    const context = baseContext({ attachments: [], pendingResponse: true })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("queued follow-up")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.pushCommandResult).not.toHaveBeenCalledWith("Queued prompt", expect.anything())
    expect(commands.queuedPrompt).toBe(null)
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("rejects unknown slash commands with nearest matches instead of submitting them as prompts", async () => {
    const context = baseContext({ attachments: [] })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/modles")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "Unknown command",
      expect.arrayContaining([expect.stringContaining("Unknown slash command"), expect.stringContaining("/models")]),
    )
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("rejects unavailable exact slash commands instead of falling through to the model", async () => {
    const context = baseContext({ attachments: [] })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/goal ship this")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/goal",
      expect.arrayContaining([expect.stringContaining("durable goal")]),
    )
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("rejects malformed known slash command arguments before dispatch", async () => {
    const context = baseContext({ attachments: [] })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/mode chaos")
    await commands.handleLineSubmit("/model")
    await commands.handleLineSubmit("/help extra")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/mode",
      expect.arrayContaining([expect.stringContaining("Expected: plan|build|auto")]),
    )
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/model",
      expect.arrayContaining([expect.stringContaining("Missing required argument")]),
    )
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/help",
      expect.arrayContaining([expect.stringContaining("does not take arguments")]),
    )
  })

  it("rejects deferred goal with explicit reasons", async () => {
    const context = baseContext({ attachments: [] })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/goal ship this")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/goal",
      expect.arrayContaining([expect.stringContaining("durable goal")]),
    )
  })

  it("renders a truthful /diff no-diff result without model submission", async () => {
    const context = baseContext({ attachments: [], jumpTranscriptToAnchor: vi.fn(() => false) })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/diff")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.enterTranscriptViewer).not.toHaveBeenCalled()
    expect(context.pushHistoryEntry).toHaveBeenCalledWith("/diff")
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/diff",
      expect.arrayContaining([expect.stringContaining("No transcript diff found")]),
    )
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("renders working-tree /diff locally without model submission when git changes exist", async () => {
    const context = baseContext({
      attachments: [],
      onReadWorkingTreeDiff: vi.fn(async () => ({
        kind: "dirty",
        workspace: "/tmp/work",
        repoRoot: "/tmp/work",
        changedFiles: [
          { path: "src/app.ts", status: " M", staged: false, unstaged: true, untracked: false },
          { path: "notes.txt", status: "??", staged: false, unstaged: false, untracked: true },
        ],
        additions: 2,
        deletions: 1,
        patch: "diff --git a/src/app.ts b/src/app.ts\n--- a/src/app.ts\n+++ b/src/app.ts\n@@ -1 +1,2 @@\n-old\n+new\n+extra",
        untrackedCount: 1,
        warnings: ["1 untracked file listed; untracked file contents are not included in the patch preview."],
      })),
      jumpTranscriptToAnchor: vi.fn(() => {
        throw new Error("should not inspect transcript diff when working tree is dirty")
      }),
    })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/diff")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.enterTranscriptViewer).not.toHaveBeenCalled()
    expect(context.pushHistoryEntry).toHaveBeenCalledWith("/diff")
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/diff",
      expect.arrayContaining([
        "Working-tree diff (read-only)",
        expect.stringContaining("+2/-1"),
        expect.stringContaining("src/app.ts"),
        expect.stringContaining("```diff"),
        "Review/approval actions: read-only in this TUI surface; no policy or approval state changed.",
      ]),
    )
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("targets working-tree diff files and hunks without model submission", async () => {
    const patch = [
      "diff --git a/src/app.ts b/src/app.ts",
      "--- a/src/app.ts",
      "+++ b/src/app.ts",
      "@@ -1 +1,2 @@",
      "-old",
      "+new",
      "+extra",
      "diff --git a/src/other.ts b/src/other.ts",
      "--- a/src/other.ts",
      "+++ b/src/other.ts",
      "@@ -1 +1 @@",
      "-a",
      "+b",
    ].join("\n")
    const context = baseContext({
      attachments: [],
      onReadWorkingTreeDiff: vi.fn(async () => ({
        kind: "dirty",
        workspace: "/tmp/work",
        repoRoot: "/tmp/work",
        changedFiles: [
          { path: "src/app.ts", status: " M", staged: false, unstaged: true, untracked: false },
          { path: "src/other.ts", status: " M", staged: false, unstaged: true, untracked: false },
        ],
        additions: 3,
        deletions: 2,
        patch,
        untrackedCount: 0,
        warnings: [],
      })),
    })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/diff file 2")
    await commands.handleLineSubmit("/diff hunk 1")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/diff file",
      expect.arrayContaining([
        expect.stringContaining("2/2: src/other.ts"),
        expect.stringContaining("```diff"),
        expect.stringContaining("+b"),
      ]),
    )
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/diff hunk",
      expect.arrayContaining([
        expect.stringContaining("1/2: src/app.ts"),
        expect.stringContaining("@@ -1 +1,2 @@"),
      ]),
    )
  })

  it("exports and copies working-tree patch through local /diff actions", async () => {
    const context = baseContext({
      attachments: [],
      onExportWorkingTreeDiffPatch: vi.fn(async (target: string | null) => ({
        ok: true,
        path: `/tmp/work/${target || ".breadboard/diff.patch"}`,
        bytes: 42,
      })),
      onCopyWorkingTreeDiffPatch: vi.fn(async () => ({ ok: true, method: "fake-file", bytes: 42 })),
    })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/diff export review.patch")
    await commands.handleLineSubmit("/diff copy")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.onExportWorkingTreeDiffPatch).toHaveBeenCalledWith("review.patch")
    expect(context.onCopyWorkingTreeDiffPatch).toHaveBeenCalledTimes(1)
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/diff export",
      expect.arrayContaining([
        expect.stringContaining("exported"),
        expect.stringContaining("review.patch"),
        expect.stringContaining("does not approve or apply changes"),
      ]),
    )
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/diff copy",
      expect.arrayContaining([
        expect.stringContaining("copied"),
        expect.stringContaining("fake-file"),
        expect.stringContaining("does not approve or apply changes"),
      ]),
    )
  })

  it("keeps deferred goal and fork commands fail-closed without model submission", async () => {
    const context = baseContext({ attachments: [] })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/goal finish the mission")
    await commands.handleLineSubmit("/fork")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/goal",
      expect.arrayContaining([
        expect.stringContaining("durable goal"),
        expect.stringContaining("Status: feature-gated"),
      ]),
    )
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/fork",
      expect.arrayContaining([
        expect.stringContaining("session graph"),
        expect.stringContaining("Status: feature-gated"),
      ]),
    )
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("opens the transcript viewer for /diff when a diff anchor exists", async () => {
    const context = baseContext({ attachments: [], jumpTranscriptToAnchor: vi.fn(() => true) })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/diff")
    await flush()
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.enterTranscriptViewer).toHaveBeenCalledTimes(1)
    expect(context.jumpTranscriptToAnchor).toHaveBeenCalledWith("diff")
    expect(context.pushHistoryEntry).toHaveBeenCalledWith("/diff")
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("opens the multiagent task dashboard through /agents without model submission", async () => {
    const context = baseContext({ attachments: [] })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/agents")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.setTaskSearchQuery).toHaveBeenCalledWith("")
    expect(context.setTaskStatusFilter).toHaveBeenCalledWith("all")
    expect(context.setTaskLaneFilter).toHaveBeenCalledWith("all")
    expect(context.setTaskIndex).toHaveBeenCalledWith(0)
    expect(context.setTaskScroll).toHaveBeenCalledWith(0)
    expect(context.setTasksOpen).toHaveBeenCalledWith(true)
    expect((context.taskboardInputQuarantineUntilRef as { current: number }).current).toBeGreaterThan(Date.now() - 1)
    expect(context.pushHistoryEntry).toHaveBeenCalledWith("/agents")
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("opens Task Focus through /inspect task without model submission", async () => {
    const context = baseContext({ attachments: [] })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/inspect task")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.setTaskLaneFilter).toHaveBeenCalledWith("lane-a")
    expect(context.setTaskFocusLaneId).toHaveBeenCalledWith("lane-a")
    expect(context.setTaskFocusFollowTail).toHaveBeenCalledWith(true)
    expect(context.setTaskFocusRawMode).toHaveBeenCalledWith(false)
    expect(context.setTaskFocusTailLines).toHaveBeenCalledWith(24)
    expect(context.setTasksOpen).toHaveBeenCalledWith(true)
    expect(context.setTaskFocusViewOpen).toHaveBeenCalledWith(true)
    expect(context.requestTaskTail).toHaveBeenCalledWith({ raw: false, tailLines: 24 })
    expect(context.pushHistoryEntry).toHaveBeenCalledWith("/inspect task")
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("controls Task Focus follow through /follow task without model submission", async () => {
    const context = baseContext({ attachments: [] })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/follow task pause")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.setTaskFocusFollowTail).toHaveBeenCalledWith(false)
    expect(context.setTaskFocusViewOpen).toHaveBeenCalledWith(true)
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/follow task",
      expect.arrayContaining([
        "Task Focus follow paused.",
        expect.stringContaining("no task state mutation or model submission"),
      ]),
    )
    expect(context.pushHistoryEntry).toHaveBeenCalledWith("/follow task pause")
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("reports Task Focus follow status through /follow task status without model submission", async () => {
    const context = baseContext({ attachments: [], taskFocusFollowTail: false })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/follow task status")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/follow task",
      expect.arrayContaining([
        "Task Focus follow: paused.",
        expect.stringContaining("main transcript follow is controlled by /follow status|pause|resume"),
      ]),
    )
    expect(context.pushHistoryEntry).toHaveBeenCalledWith("/follow task status")
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("renders read-only permission status without model submission", async () => {
    const context = baseContext({
      attachments: [],
      configPath: "/tmp/breadboard.yaml",
      permissionMode: "prompt",
      permissionRequest: null,
      permissionQueueDepth: 0,
      permissionScope: "project",
    })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/permissions")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.pushHistoryEntry).toHaveBeenCalledWith("/permissions")
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/permissions",
      expect.arrayContaining([
        "Permission status (read-only)",
        expect.stringContaining("/tmp/breadboard.yaml"),
        expect.stringContaining("prompt"),
        "Policy editing: not productized in this TUI yet.",
        "This command does not change permission policy.",
      ]),
    )
    const permissionLines = (context.pushCommandResult as any).mock.calls.find((call: any[]) => call[0] === "/permissions")?.[1] ?? []
    expect(permissionLines.join("\n")).not.toMatch(/allow once|allow for session|persisted policy|policy changed/i)
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("opens raw transcript mode through the controller and transcript viewer", async () => {
    const context = baseContext({ attachments: [] })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/raw")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.enterTranscriptViewer).toHaveBeenCalledWith({ raw: true })
    expect(context.pushHistoryEntry).toHaveBeenCalledWith("/raw")
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("exports transcript content for the copy command without submitting a model prompt", async () => {
    const context = baseContext({ attachments: [] })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/copy")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.saveTranscriptExport).toHaveBeenCalledTimes(1)
    expect(context.pushHistoryEntry).toHaveBeenCalledWith("/copy")
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("opens recent sessions for /resume when the current session is already active", async () => {
    const context = baseContext({ attachments: [], disconnected: false })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/resume")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.setRecentSessionsOpen).toHaveBeenCalledWith(true)
    expect(context.refreshRecentSessions).toHaveBeenCalledTimes(1)
    expect(context.pushHistoryEntry).toHaveBeenCalledWith("/resume")
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("dispatches controller-owned slash commands without converting them into model prompts", async () => {
    const context = baseContext({ attachments: [] })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/new")
    expect(context.onSubmit).toHaveBeenCalledWith("/new")
    expect(context.pushHistoryEntry).toHaveBeenCalledWith("/new")
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("dispatches hidden debug commands without exposing them in suggestions", async () => {
    const context = baseContext({ attachments: [], configPath: "/tmp/breadboard.json" })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/debug-config")
    expect(context.onSubmit).not.toHaveBeenCalled()
    expect(context.pushCommandResult).toHaveBeenCalledWith(
      "/debug-config",
      expect.arrayContaining([expect.stringContaining("/tmp/breadboard.json")]),
    )
    expect(context.handleLineEdit).toHaveBeenCalledWith("", 0)
  })

  it("stages local slash commands in composer history according to registry history behavior", async () => {
    const context = baseContext({ attachments: [] })
    const commands = await renderCommands(context)
    await commands.handleLineSubmit("/models")
    await commands.handleLineSubmit("/help")
    expect(context.pushHistoryEntry).toHaveBeenCalledWith("/models")
    expect(context.pushHistoryEntry).not.toHaveBeenCalledWith("/help")
  })
})
