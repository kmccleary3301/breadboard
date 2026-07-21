import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import { setTimeout as sleep } from "node:timers/promises"
import path from "node:path"
import { ReplSessionController } from "../controller.js"
import { enqueueStreamEventForGeneration } from "../controllerEventMethods.js"
import { renderStateToText } from "../renderText.js"
import { buildTranscript } from "../../../repl/transcriptBuilder.js"

type RawEvent = Record<string, unknown>
const normalizeEol = (value: string): string => value.replace(/\r\n/g, "\n")

const parseEvent = (raw: string): RawEvent | null => {
  let parsed: any
  try {
    parsed = JSON.parse(raw)
  } catch {
    return null
  }
  if (parsed && typeof parsed === "object") {
    if (parsed.event && typeof parsed.event === "object") return parsed.event as RawEvent
    if (parsed.data && typeof parsed.data === "string") {
      try {
        const inner = JSON.parse(parsed.data)
        if (inner && typeof inner === "object") return inner as RawEvent
      } catch {
        return null
      }
    }
    if (parsed.type) return parsed as RawEvent
  }
  return null
}

const buildControllerFromFixture = (name: string): ReplSessionController => {
  const fixtureDir = path.resolve("src/commands/repl/__tests__/fixtures")
  const jsonlPath = path.join(fixtureDir, `${name}.jsonl`)
  const raw = readFileSync(jsonlPath, "utf8")
  const lines = raw.split(/\r?\n/).filter((line) => line.trim().length > 0)
  const controller = new ReplSessionController({
    configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
    workspace: null,
    model: null,
    remotePreference: null,
    permissionMode: null,
  })

  for (const line of lines) {
    const event = parseEvent(line)
    if (!event) continue
    ;(controller as any).applyEvent(event)
  }

  return controller
}

const renderFixture = (name: string): string => {
  const controller = buildControllerFromFixture(name)
  try {
    ;(controller as any).liveSlots?.clear?.()
    ;(controller as any).liveSlotTimers?.forEach?.((timer: ReturnType<typeof setTimeout>) => clearTimeout(timer))
    ;(controller as any).liveSlotTimers?.clear?.()
  } catch {
    // ignore
  }
  const snapshot = renderStateToText(controller.getState(), {
    includeHeader: false,
    includeStatus: false,
    includeHints: false,
    includeModelMenu: false,
    colors: false,
    asciiOnly: true,
  })
  // Keep fixtures as normal text files that end with a newline.
  return snapshot.endsWith("\n") ? snapshot : `${snapshot}\n`
}

const readExpected = (name: string): string => {
  const fixtureDir = path.resolve("src/commands/repl/__tests__/fixtures")
  const expectedPath = path.join(fixtureDir, `${name}.render.txt`)
  return readFileSync(expectedPath, "utf8")
}

const newReplayController = (): ReplSessionController =>
  new ReplSessionController({
    configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
    workspace: null,
    model: null,
    remotePreference: null,
    permissionMode: null,
  })

const countOccurrences = (value: string, needle: string): number => value.split(needle).length - 1

describe("render_events_jsonl replay fixtures", () => {
  it("matches tool call + tool result fixture", () => {
    const snapshot = renderFixture("tool_call_result")
    expect(normalizeEol(snapshot)).toBe(normalizeEol(readExpected("tool_call_result")))
  })

  it("matches tool display head/tail truncation fixture", () => {
    const snapshot = renderFixture("tool_display_head_tail")
    expect(normalizeEol(snapshot)).toBe(normalizeEol(readExpected("tool_display_head_tail")))
  })

  it("matches assistant streaming interrupted by tool fixture", () => {
    const snapshot = renderFixture("assistant_tool_interleave")
    expect(normalizeEol(snapshot)).toBe(normalizeEol(readExpected("assistant_tool_interleave")))
  })

  it("segments assistant output around tool events", () => {
    const controller = buildControllerFromFixture("assistant_tool_interleave")
    const conversation = controller.getState().conversation
    const assistantEntries = conversation.filter((entry) => entry.speaker === "assistant")
    expect(assistantEntries.length).toBeGreaterThanOrEqual(2)
    expect(assistantEntries[0]?.text).toContain("Sure, I'll check.")
    expect(assistantEntries[1]?.text).toContain("Done.")
  })

  it("matches system notice + tool error fixture", () => {
    const snapshot = renderFixture("system_notice_tool_error")
    expect(normalizeEol(snapshot)).toBe(normalizeEol(readExpected("system_notice_tool_error")))
  })

  it("matches multi-turn interleaving fixture", () => {
    const snapshot = renderFixture("multi_turn_interleave")
    expect(normalizeEol(snapshot)).toBe(normalizeEol(readExpected("multi_turn_interleave")))
  })

  it("merges tool call + result into a single tool entry", () => {
    const controller = buildControllerFromFixture("tool_call_result")
    const toolEvents = controller.getState().toolEvents
    const toolEntries = toolEvents.filter((entry) => entry.kind === "call" || entry.kind === "result")
    expect(toolEntries.length).toBe(1)
    expect(toolEntries[0]?.callId).toBe("call-1")
  })

  it("binds tool result without tool_name to prior call_id", () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })
    ;(controller as any).applyEvent({
      id: "t1",
      seq: 1,
      type: "tool_call",
      payload: { call_id: "call-x", tool_name: "bash", display: { title: "Bash(pwd)" } },
    })
    ;(controller as any).applyEvent({
      id: "t2",
      seq: 2,
      type: "tool.result",
      payload: { call_id: "call-x", display: { title: "Bash(pwd)", summary: "Exit 0" } },
    })
    const toolEntries = controller.getState().toolEvents.filter((entry) => entry.kind === "call" || entry.kind === "result")
    expect(toolEntries.length).toBe(1)
    expect(toolEntries[0]?.callId).toBe("call-x")
  })

  it("preserves tool exec output in the final rendered tool row", () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })
    ;(controller as any).applyEvent({
      id: "tx1",
      seq: 1,
      type: "tool_call",
      payload: { call_id: "call-exec", tool_name: "bash", display: { title: "Bash(pwd)" } },
    })
    ;(controller as any).applyEvent({
      id: "tx2",
      seq: 2,
      type: "tool.exec.start",
      payload: { call_id: "call-exec", exec_id: "exec-1", tool_name: "bash", command: "pwd" },
    })
    ;(controller as any).applyEvent({
      id: "tx3",
      seq: 3,
      type: "tool.exec.stdout.delta",
      payload: { call_id: "call-exec", exec_id: "exec-1", delta: "/tmp/project\n" },
    })
    ;(controller as any).applyEvent({
      id: "tx4",
      seq: 4,
      type: "tool.exec.end",
      payload: { call_id: "call-exec", exec_id: "exec-1", exit_code: 0 },
    })
    ;(controller as any).applyEvent({
      id: "tx5",
      seq: 5,
      type: "tool.result",
      payload: { call_id: "call-exec", tool_name: "bash", display: { title: "Bash(pwd)", summary: "Exit 0" } },
    })

    const state = controller.getState()
    expect(state.toolEvents).toHaveLength(1)
    expect(state.toolEvents[0]?.display?.detail).toEqual(["/tmp/project"])

    const snapshot = renderStateToText(state, {
      includeHeader: false,
      includeStatus: false,
      includeHints: false,
      includeModelMenu: false,
      colors: false,
      asciiOnly: true,
    })
    expect(normalizeEol(snapshot)).toContain("o Bash(pwd)\n  `- /tmp/project\n")
  })

  it("preserves labeled stdout/stderr sections in the final rendered tool row", () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })
    ;(controller as any).applyEvent({
      id: "tb1",
      seq: 1,
      type: "tool_call",
      payload: { call_id: "call-both", tool_name: "bash", display: { title: "Bash(ls -la)" } },
    })
    ;(controller as any).applyEvent({
      id: "tb2",
      seq: 2,
      type: "tool.exec.stdout.delta",
      payload: { call_id: "call-both", exec_id: "exec-both", delta: "file_a.txt\nfile_b.log\n" },
    })
    ;(controller as any).applyEvent({
      id: "tb3",
      seq: 3,
      type: "tool.exec.stderr.delta",
      payload: { call_id: "call-both", exec_id: "exec-both", delta: "warn: hidden file\n" },
    })
    ;(controller as any).applyEvent({
      id: "tb4",
      seq: 4,
      type: "tool.result",
      payload: { call_id: "call-both", tool_name: "bash", display: { title: "Bash(ls -la)" } },
    })

    const state = controller.getState()
    expect(state.toolEvents).toHaveLength(1)
    expect(state.toolEvents[0]?.display?.summary).toBe("stdout 2 lines · stderr 1 line")
    expect(state.toolEvents[0]?.display?.detail).toEqual([
      "stdout:",
      "file_a.txt",
      "file_b.log",
      "",
      "stderr:",
      "warn: hidden file",
    ])

    const snapshot = renderStateToText(state, {
      includeHeader: false,
      includeStatus: false,
      includeHints: false,
      includeModelMenu: false,
      colors: false,
      asciiOnly: true,
    })
    const normalized = normalizeEol(snapshot)
    expect(normalized).toContain("stdout:")
    expect(normalized).toContain("stderr:")
  })

  it("projects native run_shell results into the durable tool-row contract", () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })
    ;(controller as any).applyEvent({
      id: "ns1",
      seq: 1,
      type: "tool_call",
      payload: { call_id: "call-native", tool_name: "run_shell", args: { command: "pwd" } },
    })
    ;(controller as any).applyEvent({
      id: "ns2",
      seq: 2,
      type: "tool_result",
      payload: {
        call_id: "call-native",
        tool_name: "run_shell",
        result: { stdout: "/tmp/native\n", exit: 0, __mvi_text_output: "/tmp/native\n" },
      },
    })

    const state = controller.getState()
    expect(state.toolEvents).toHaveLength(1)
    expect(state.toolEvents[0]?.display?.title).toBe("Tool")
    expect(state.toolEvents[0]?.display?.summary).toBe("stdout 1 line")
    expect(state.toolEvents[0]?.display?.detail).toEqual(["/tmp/native"])
    expect(state.toolEvents[0]?.text).toContain("stdout 1 line")
    expect(state.toolEvents[0]?.text).toContain("/tmp/native")

    const snapshot = renderStateToText(state, {
      includeHeader: false,
      includeStatus: false,
      includeHints: false,
      includeModelMenu: false,
      colors: false,
      asciiOnly: true,
    })
    const normalized = normalizeEol(snapshot)
    expect(normalized).toContain("o Tool")
    expect(normalized).toContain("/tmp/native")
  })

  it("normalizes prebuilt shell displays into the durable tool-row contract", () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })
    ;(controller as any).applyEvent({
      id: "ps1",
      seq: 1,
      type: "tool_call",
      payload: { call_id: "call-shell-display", tool_name: "run_shell", args: { command: "pwd" } },
    })
    ;(controller as any).applyEvent({
      id: "ps2",
      seq: 2,
      type: "tool_result",
      payload: {
        call_id: "call-shell-display",
        tool_name: "run_shell",
        display: { title: "run_shell", detail: ["/tmp/native"] },
      },
    })

    const state = controller.getState()
    expect(state.toolEvents).toHaveLength(1)
    expect(state.toolEvents[0]?.display?.title).toBe("Tool")
    expect(state.toolEvents[0]?.display?.summary).toBe("stdout 1 line")
    expect(state.toolEvents[0]?.display?.detail).toEqual(["/tmp/native"])
  })

  it("orders assistant segments around tool events by createdAt", () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })
    let now = 1000
    const originalNow = Date.now
    Date.now = () => (now += 1)
    ;(controller as any).applyEvent({ id: "o1", seq: 1, type: "assistant.message.start", payload: {} })
    ;(controller as any).applyEvent({ id: "o2", seq: 2, type: "assistant.message.delta", payload: { delta: "First" } })
    ;(controller as any).applyEvent({ id: "o3", seq: 3, type: "tool_call", payload: { call_id: "call-o", tool_name: "list_dir" } })
    ;(controller as any).applyEvent({ id: "o4", seq: 4, type: "tool.result", payload: { call_id: "call-o", tool_name: "list_dir" } })
    ;(controller as any).applyEvent({ id: "o5", seq: 5, type: "assistant.message.delta", payload: { delta: "Second" } })
    Date.now = originalNow
    const state = controller.getState()
    const assistantEntries = state.conversation.filter((entry) => entry.speaker === "assistant")
    const toolEntry = state.toolEvents.find((entry) => entry.callId === "call-o")
    expect(assistantEntries.length).toBeGreaterThanOrEqual(2)
    expect(toolEntry).toBeTruthy()
    const firstAt = assistantEntries[0]?.createdAt ?? 0
    const secondAt = assistantEntries[1]?.createdAt ?? 0
    const toolAt = toolEntry?.createdAt ?? 0
    expect(firstAt).toBeLessThan(toolAt)
    expect(toolAt).toBeLessThan(secondAt)
  })

  it("preserves canonical assistant transcript text across markdown and status surfaces", () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    }) as unknown as {
      applyEvent: (evt: any) => void
      getState: () => any
      viewPrefs: Record<string, unknown>
      runtimeFlags: Record<string, unknown>
    }

    controller.viewPrefs = { ...controller.viewPrefs, richMarkdown: true, showReasoning: true }
    controller.runtimeFlags = {
      ...controller.runtimeFlags,
      thinkingEnabled: true,
      allowFullThinking: true,
    }

    const chunks = ["alpha ", "beta\n", "gamma", " delta"]
    controller.applyEvent({ id: "c1", seq: 1, type: "run.start", payload: {} })
    controller.applyEvent({ id: "c2", seq: 2, type: "assistant.message.start", payload: {} })
    controller.applyEvent({ id: "c3", seq: 3, type: "assistant.message.delta", payload: { delta: chunks[0] } })
    controller.applyEvent({ id: "c4", seq: 4, type: "assistant.reasoning.delta", payload: { delta: "thinking..." } })
    controller.applyEvent({ id: "c5", seq: 5, type: "assistant.message.delta", payload: { delta: chunks[1] } })
    controller.applyEvent({ id: "c6", seq: 6, type: "tool_call", payload: { call_id: "call-canon", tool_name: "bash" } })
    controller.applyEvent({ id: "c7", seq: 7, type: "tool.result", payload: { call_id: "call-canon", tool_name: "bash" } })
    controller.applyEvent({ id: "c8", seq: 8, type: "assistant.message.delta", payload: { delta: chunks[2] } })
    controller.applyEvent({ id: "c9", seq: 9, type: "assistant_delta", payload: { text: chunks[3] } })
    controller.applyEvent({ id: "c10", seq: 10, type: "assistant.message.end", payload: {} })

    const state = controller.getState()
    const renderedAssistant = state.conversation
      .filter((entry: any) => entry.speaker === "assistant")
      .map((entry: any) => entry.text)
      .join("")

    expect(renderedAssistant).toBe(chunks.join(""))
    expect(state.toolEvents.some((entry: any) => String(entry.text ?? "").startsWith("[reasoning]"))).toBe(false)
  })

  it("keeps cumulative assistant_message updates in one hot assistant entry until completion", () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })

    ;(controller as any).applyEvent({ id: "m1", seq: 1, type: "run.start", payload: {} })
    ;(controller as any).applyEvent({
      id: "m2",
      seq: 2,
      type: "assistant_message",
      payload: { text: "## Streaming\n- first", message: { role: "assistant", content: "## Streaming\n- first" } },
    })

    let state = controller.getState()
    let assistantEntries = state.conversation.filter((entry) => entry.speaker === "assistant")
    expect(assistantEntries).toHaveLength(1)
    expect(assistantEntries[0]?.phase).toBe("streaming")
    expect(assistantEntries[0]?.text).toBe("## Streaming\n- first")

    ;(controller as any).applyEvent({
      id: "m3",
      seq: 3,
      type: "assistant_message",
      payload: {
        text: "## Streaming\n- first\n- second",
        message: { role: "assistant", content: "## Streaming\n- first\n- second" },
      },
    })

    state = controller.getState()
    assistantEntries = state.conversation.filter((entry) => entry.speaker === "assistant")
    expect(assistantEntries).toHaveLength(1)
    expect(assistantEntries[0]?.phase).toBe("streaming")
    expect(assistantEntries[0]?.text).toBe("## Streaming\n- first\n- second")

    const partialSnapshot = renderStateToText(state, {
      includeHeader: false,
      includeStatus: false,
      includeHints: false,
      includeModelMenu: false,
      colors: false,
      asciiOnly: true,
    })
    expect(normalizeEol(partialSnapshot)).toContain("Streaming")
    expect(normalizeEol(partialSnapshot)).toContain("first")
    expect(normalizeEol(partialSnapshot)).toContain("second")

    ;(controller as any).applyEvent({
      id: "m4",
      seq: 4,
      type: "assistant_message",
      payload: {
        text: "## Streaming\n- first\n- second\n\nFinal.",
        message: { role: "assistant", content: "## Streaming\n- first\n- second\n\nFinal." },
      },
    })
    ;(controller as any).applyEvent({ id: "m5", seq: 5, type: "completion", payload: { summary: { completed: true } } })

    state = controller.getState()
    assistantEntries = state.conversation.filter((entry) => entry.speaker === "assistant")
    expect(assistantEntries).toHaveLength(1)
    expect(assistantEntries[0]?.phase).toBe("final")
    expect(assistantEntries[0]?.text).toBe("## Streaming\n- first\n- second\n\nFinal.")
  })

  it("deduplicates polluted backend user echo against the optimistic local prompt", async () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })

    ;(controller as any).sessionId = "session-live"
    ;(controller as any).dispatchSubmission = async () => {}

    await controller.handleInput("Hello how are you? Answer in exactly 3 markdown bullets.")

    ;(controller as any).applyEvent({
      id: "u1",
      seq: 1,
      type: "user_message",
      payload: {
        text: "Hello how are you? Answer in exactly 3 markdown bullets.\n\nindustry_coder_refs/codex/codex-rs/core/gpt_5_codex_prompt.md",
      },
    })

    const userEntries = controller.getState().conversation.filter((entry) => entry.speaker === "user")
    expect(userEntries).toHaveLength(1)
    expect(userEntries[0]?.text).toBe("Hello how are you? Answer in exactly 3 markdown bullets.")
  })

  it("sanitizes backend user echo prompt scaffolding before storing transcript text", () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })

    ;(controller as any).applyEvent({
      id: "u2",
      seq: 1,
      type: "user_message",
      payload: {
        text: "List the current files.\n\n<TOOLS_AVAILABLE>\n- shell_command\n- read_file",
      },
    })

    const userEntries = controller.getState().conversation.filter((entry) => entry.speaker === "user")
    expect(userEntries).toHaveLength(1)
    expect(userEntries[0]?.text).toBe("List the current files.")
  })

  it("keeps optimistic local user turns ordered before later assistant transcript rows", async () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })

    ;(controller as any).sessionId = "session-live"
    ;(controller as any).dispatchSubmission = async () => {}

    await controller.handleInput("Hello there")

    ;(controller as any).applyEvent({
      id: "a1",
      seq: 2,
      type: "assistant_message",
      payload: { text: "Hi." },
    })
    ;(controller as any).applyEvent({
      id: "done",
      seq: 3,
      type: "completion",
      payload: { summary: { completed: true } },
    })

    const transcript = buildTranscript({
      conversation: controller.getState().conversation,
      toolEvents: [],
      rawEvents: [],
    })

    expect(transcript.committed.map((entry) => entry.kind === "message" ? `${entry.speaker}:${entry.text}` : entry.id)).toEqual([
      "user:Hello there",
      "assistant:Hi.",
    ])
  })

  it("does not duplicate a final cumulative assistant_message after an unstable streamed tail", async () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })

    ;(controller as any).applyEvent({ id: "r1", seq: 1, type: "run.start", payload: {} })
    ;(controller as any).applyEvent({ id: "r2", seq: 2, type: "assistant.message.delta", payload: { delta: "323" } })
    ;(controller as any).applyEvent({ id: "r3", seq: 3, type: "assistant_message", payload: { text: "323" } })
    ;(controller as any).applyEvent({ id: "r4", seq: 4, type: "completion", payload: { summary: { completed: true } } })

    await sleep(300)

    const state = controller.getState()
    expect(state.conversation).toHaveLength(1)
    expect(state.conversation[0]?.text).toBe("323")
    expect(state.conversation[0]?.phase).toBe("final")

    const snapshot = renderStateToText(state, {
      includeHeader: false,
      includeStatus: false,
      includeHints: false,
      includeModelMenu: false,
      colors: false,
      asciiOnly: true,
    })
    expect(normalizeEol(snapshot)).toContain("* 323")
    expect(normalizeEol(snapshot)).not.toContain("323323")
  })

  it("keeps a streamed assistant entry hot after assistant.message.end until completion", async () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })

    const partial = "- item-01\n- item-02\n"
    const finalText = `${partial}- item-03\n- item-04`
    ;(controller as any).applyEvent({ id: "k1", seq: 1, type: "run.start", payload: {} })
    ;(controller as any).applyEvent({ id: "k2", seq: 2, type: "assistant.message.start", payload: {} })
    ;(controller as any).applyEvent({ id: "k3", seq: 3, type: "assistant.message.delta", payload: { delta: partial } })
    ;(controller as any).applyEvent({ id: "k4", seq: 4, type: "assistant.message.end", payload: {} })

    let state = controller.getState()
    expect(state.conversation).toHaveLength(1)
    expect(state.conversation[0]?.phase).toBe("streaming")
    expect((controller as any).streamingEntryId).toBe(state.conversation[0]?.id)

    ;(controller as any).applyEvent({
      id: "k5",
      seq: 5,
      type: "assistant_message",
      payload: { text: finalText, message: { role: "assistant", content: finalText } },
    })

    state = controller.getState()
    expect(state.conversation).toHaveLength(1)
    expect(state.conversation[0]?.text).toBe(finalText)
    expect(state.conversation[0]?.phase).toBe("streaming")

    ;(controller as any).applyEvent({ id: "k6", seq: 6, type: "completion", payload: { summary: { completed: true } } })

    state = controller.getState()
    expect(state.conversation).toHaveLength(1)
    expect(state.conversation[0]?.text).toBe(finalText)
    expect(state.conversation[0]?.phase).toBe("final")
  })

  it("rebinds a late cumulative assistant_message onto the finalized streamed assistant entry", async () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })

    const partial = "- item-01\n- item-02\n"
    const finalText = `${partial}- item-03\n- item-04`
    ;(controller as any).applyEvent({ id: "s1", seq: 1, type: "run.start", payload: {} })
    ;(controller as any).applyEvent({ id: "s2", seq: 2, type: "assistant.message.start", payload: {} })
    ;(controller as any).applyEvent({ id: "s3", seq: 3, type: "assistant.message.delta", payload: { delta: partial } })
    ;(controller as any).applyEvent({ id: "s4", seq: 4, type: "assistant.message.end", payload: {} })
    ;(controller as any).applyEvent({
      id: "s5",
      seq: 5,
      type: "assistant_message",
      payload: { text: finalText, message: { role: "assistant", content: finalText } },
    })
    ;(controller as any).applyEvent({ id: "s6", seq: 6, type: "completion", payload: { summary: { completed: true } } })

    await sleep(300)

    const state = controller.getState()
    const assistants = state.conversation.filter((entry) => entry.speaker === "assistant")
    expect(assistants).toHaveLength(1)
    expect(assistants[0]?.text).toBe(finalText)
    expect(assistants[0]?.phase).toBe("final")

    const snapshot = renderStateToText(state, {
      includeHeader: false,
      includeStatus: false,
      includeHints: false,
      includeModelMenu: false,
      colors: false,
      asciiOnly: true,
    })
    expect(normalizeEol(snapshot)).toContain("- item-04")
    expect(normalizeEol(snapshot)).not.toContain("- item-01\n- item-02\n- item-01")
  })

  it("rebinds a late cumulative assistant_message even when it arrives after completion", async () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })

    const finalText = `# First Turn
- one
- two
- three`
    ;(controller as any).applyEvent({ id: "l1", seq: 1, type: "run.start", payload: {} })
    ;(controller as any).applyEvent({ id: "l2", seq: 2, type: "assistant.message.delta", payload: { delta: `# First Turn
- one
` } })
    ;(controller as any).applyEvent({ id: "l3", seq: 3, type: "completion", payload: { summary: { completed: true } } })
    ;(controller as any).applyEvent({ id: "l4", seq: 4, type: "assistant_message", payload: { text: finalText } })

    const assistantEntries = controller.getState().conversation.filter((entry) => entry.speaker === "assistant")
    expect(assistantEntries).toHaveLength(1)
    expect(assistantEntries[0]?.text).toBe(finalText)
  })

  it("does not append completion final_message when it matches the final assistant content", async () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })

    const finalText = `# First Turn
- one
- two`
    ;(controller as any).applyEvent({ id: "x1", seq: 1, type: "assistant_message", payload: { text: finalText } })
    ;(controller as any).applyEvent({
      id: "x2",
      seq: 2,
      type: "completion",
      payload: { summary: { completed: true }, final_message: finalText },
    })

    const assistantEntries = controller.getState().conversation.filter((entry) => entry.speaker === "assistant")
    expect(assistantEntries).toHaveLength(1)
    expect(assistantEntries[0]?.text).toBe(finalText)
  })

  it("keeps the locally advanced turn number when later backend events still report turn 1", async () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })

    ;(controller as any).sessionId = "session-live"
    ;(controller as any).api = () => ({ postInput: async () => {} })

    await controller.handleInput("first")
    expect(controller.getState().stats.lastTurn).toBe(1)

    await controller.handleInput("second")
    expect(controller.getState().stats.lastTurn).toBe(2)

    ;(controller as any).applyEvent({ id: "t1", seq: 1, type: "turn_start", turn: 1, payload: {} })
    expect(controller.getState().stats.lastTurn).toBe(2)
  })

  it("sends a unique client message id with each interactive input", async () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })
    const submitted: Array<Record<string, unknown>> = []

    ;(controller as any).sessionId = "session-live"
    ;(controller as any).api = () => ({
      postInput: async (_sessionId: string, payload: Record<string, unknown>) => {
        submitted.push(payload)
      },
    })

    await controller.handleInput("first")
    await controller.handleInput("second")

    expect(submitted).toHaveLength(2)
    expect(submitted[0]).toMatchObject({ content: "first", client_message_id: expect.any(String) })
    expect(submitted[1]).toMatchObject({ content: "second", client_message_id: expect.any(String) })
    expect(submitted[0]?.client_message_id).not.toBe(submitted[1]?.client_message_id)
  })

  it("does not expose backend model-call turns as the visible local submission turn", async () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })

    ;(controller as any).sessionId = "session-live"
    ;(controller as any).api = () => ({ postInput: async () => {} })

    await controller.handleInput("create hello")
    expect(controller.getState().stats.lastTurn).toBe(1)

    ;(controller as any).applyEvent({ id: "t44", seq: 44, type: "turn_start", turn: 44, payload: {} })
    ;(controller as any).applyEvent({ id: "m45", seq: 45, type: "model_call_finished", turn: 44, payload: {} })

    expect(controller.getState().stats.lastTurn).toBe(1)
  })

  it("does not duplicate the final markdown tail when deltas are followed by a cumulative assistant_message", async () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })

    const finalText =
      "- I’m doing well, thanks.\n- I’m ready to help with whatever you need.\n- Hope your day is going smoothly."
    ;(controller as any).applyEvent({ id: "m1", seq: 1, type: "run.start", payload: {} })
    ;(controller as any).applyEvent({
      id: "m2",
      seq: 2,
      type: "assistant.message.delta",
      payload: { delta: "- I’m doing well, thanks.\n- I’m ready to help with whatever you need.\n" },
    })
    ;(controller as any).applyEvent({
      id: "m3",
      seq: 3,
      type: "assistant_message",
      payload: { text: finalText, message: { role: "assistant", content: finalText } },
    })
    ;(controller as any).applyEvent({ id: "m4", seq: 4, type: "completion", payload: { summary: { completed: true } } })

    await sleep(300)

    const state = controller.getState()
    expect(state.conversation).toHaveLength(1)
    expect(state.conversation[0]?.text).toBe(finalText)

    const snapshot = renderStateToText(state, {
      includeHeader: false,
      includeStatus: false,
      includeHints: false,
      includeModelMenu: false,
      colors: false,
      asciiOnly: true,
    })
    expect(normalizeEol(snapshot)).toContain("- Hope your day is going smoothly.")
    expect(normalizeEol(snapshot)).not.toContain(
      "- Hope your day is going smoothly.- Hope your day is going smoothly.",
    )
  })

  it("does not let a stale cumulative assistant_message overwrite a newer streamed assistant cell", async () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_0-107-0_e4_3-6-2026.yaml"),
      workspace: null,
      model: null,
      remotePreference: null,
      permissionMode: null,
    })

    const preamble = "I’ll keep this minimal and just return the requested three-item checklist about terminal UI quality."
    const checklist =
      "- [ ] Clear contrast and readable text\n- [ ] Responsive layout in narrow terminals\n- [ ] Stable scrollback on resize"

    ;(controller as any).applyEvent({ id: "v1", seq: 1, type: "run.start", payload: {} })
    ;(controller as any).applyEvent({ id: "v2", seq: 2, type: "assistant.message.start", payload: {} })
    ;(controller as any).applyEvent({ id: "v3", seq: 3, type: "assistant.message.delta", payload: { delta: preamble } })
    ;(controller as any).applyEvent({ id: "v4", seq: 4, type: "assistant.message.end", payload: {} })
    ;(controller as any).applyEvent({ id: "v5", seq: 5, type: "completion", payload: { summary: { completed: false } } })
    ;(controller as any).applyEvent({ id: "v6", seq: 6, type: "assistant.message.start", payload: {} })
    ;(controller as any).applyEvent({ id: "v7", seq: 7, type: "assistant.message.delta", payload: { delta: checklist } })
    ;(controller as any).applyEvent({
      id: "v8",
      seq: 8,
      type: "assistant_message",
      payload: { text: preamble, message: { role: "assistant", content: preamble } },
    })
    ;(controller as any).applyEvent({ id: "v9", seq: 9, type: "completion", payload: { summary: { completed: true } } })

    await sleep(300)

    const assistants = controller.getState().conversation.filter((entry) => entry.speaker === "assistant")
    expect(assistants).toHaveLength(2)
    expect(assistants[0]?.text).toBe(preamble)
    expect(assistants[0]?.phase).toBe("final")
    expect(assistants[1]?.text).toBe(checklist)
    expect(assistants[1]?.phase).toBe("final")
    expect(assistants[1]?.markdownStreaming).toBe(false)
  })

  it("deduplicates replayed queued user echo events by event id", () => {
    const controller = newReplayController()
    const event = {
      id: "replay-user-1",
      seq: 1,
      type: "user_message",
      payload: { text: "Replay-safe prompt" },
    }

    ;(controller as any).enqueueEvent(event)
    ;(controller as any).enqueueEvent(event)

    const users = controller.getState().conversation.filter((entry) => entry.speaker === "user")
    expect(users).toHaveLength(1)
    expect(users[0]?.text).toBe("Replay-safe prompt")
  })

  it("deduplicates replayed queued assistant deltas by event id", () => {
    const controller = newReplayController()

    ;(controller as any).enqueueEvent({ id: "replay-assistant-start", seq: 1, type: "assistant.message.start", payload: {} })
    ;(controller as any).enqueueEvent({
      id: "replay-assistant-delta",
      seq: 2,
      type: "assistant.message.delta",
      payload: { delta: "Replay-safe assistant chunk." },
    })
    ;(controller as any).enqueueEvent({
      id: "replay-assistant-delta",
      seq: 2,
      type: "assistant.message.delta",
      payload: { delta: "Replay-safe assistant chunk." },
    })

    const assistants = controller.getState().conversation.filter((entry) => entry.speaker === "assistant")
    expect(assistants).toHaveLength(1)
    expect(assistants[0]?.text).toBe("Replay-safe assistant chunk.")
  })

  it("deduplicates replayed queued tool stdout chunks by event id", () => {
    const controller = newReplayController()

    ;(controller as any).enqueueEvent({
      id: "replay-tool-start",
      seq: 1,
      type: "assistant.tool_call.start",
      payload: { call_id: "call-1", tool_name: "shell", args_text: "printf replay" },
    })
    ;(controller as any).enqueueEvent({
      id: "replay-exec-start",
      seq: 2,
      type: "tool.exec.start",
      payload: { call_id: "call-1", exec_id: "exec-1", tool_name: "shell", command: "printf replay" },
    })
    ;(controller as any).enqueueEvent({
      id: "replay-stdout-1",
      seq: 3,
      type: "tool.exec.stdout.delta",
      payload: { call_id: "call-1", exec_id: "exec-1", delta: "REPLAY_STDOUT_LINE" },
    })
    ;(controller as any).enqueueEvent({
      id: "replay-stdout-1",
      seq: 3,
      type: "tool.exec.stdout.delta",
      payload: { call_id: "call-1", exec_id: "exec-1", delta: "REPLAY_STDOUT_LINE" },
    })

    const state = controller.getState()
    const liveText = state.liveSlots.map((slot) => slot.text).join("\n")
    expect(countOccurrences(liveText, "REPLAY_STDOUT_LINE")).toBe(1)
  })

  it("deduplicates replayed queued tool result events by event id", () => {
    const controller = newReplayController()

    ;(controller as any).enqueueEvent({
      id: "replay-tool-result-start",
      seq: 1,
      type: "assistant.tool_call.start",
      payload: { call_id: "call-1", tool_name: "shell", args_text: "echo result" },
    })
    ;(controller as any).enqueueEvent({
      id: "replay-tool-result",
      seq: 2,
      type: "tool.result",
      payload: { call_id: "call-1", tool_name: "shell", result: "REPLAY_RESULT", ok: true, display: { title: "shell", detail: ["REPLAY_RESULT"] } },
    })
    ;(controller as any).enqueueEvent({
      id: "replay-tool-result",
      seq: 2,
      type: "tool.result",
      payload: { call_id: "call-1", tool_name: "shell", result: "REPLAY_RESULT", ok: true, display: { title: "shell", detail: ["REPLAY_RESULT"] } },
    })

    const state = controller.getState()
    const toolText = state.toolEvents.map((event) => event.text).join("\n")
    expect(state.toolEvents).toHaveLength(1)
    expect(countOccurrences(toolText, "REPLAY_RESULT")).toBe(1)
    expect(state.toolEvents[0]?.status).toBe("success")
  })

  it("deduplicates a full event-zero replay sequence by event id", () => {
    const controller = newReplayController() as any
    const events = [
      { id: "event-zero-user", seq: 1, type: "user_message", payload: { text: "EVENT_ZERO_PROMPT" } },
      { id: "event-zero-assistant-start", seq: 2, type: "assistant.message.start", payload: { message_id: "event-zero-msg" } },
      { id: "event-zero-assistant-delta", seq: 3, type: "assistant.message.delta", payload: { message_id: "event-zero-msg", delta: "EVENT_ZERO_ASSISTANT" } },
      { id: "event-zero-assistant-end", seq: 4, type: "assistant.message.end", payload: { message_id: "event-zero-msg" } },
      { id: "event-zero-tool-call", seq: 5, type: "assistant.tool_call.start", payload: { call_id: "event-zero-call", tool_name: "shell", args_text: "printf event-zero" } },
      { id: "event-zero-tool-start", seq: 6, type: "tool.exec.start", payload: { call_id: "event-zero-call", exec_id: "event-zero-exec", tool_name: "shell", command: "printf event-zero" } },
      { id: "event-zero-tool-stdout", seq: 7, type: "tool.exec.stdout.delta", payload: { call_id: "event-zero-call", exec_id: "event-zero-exec", delta: "EVENT_ZERO_STDOUT" } },
      { id: "event-zero-tool-result", seq: 8, type: "tool.result", payload: { call_id: "event-zero-call", tool_name: "shell", result: "EVENT_ZERO_RESULT", ok: true, display: { title: "shell", detail: ["EVENT_ZERO_RESULT"] } } },
      { id: "event-zero-completion", seq: 9, type: "completion", payload: { summary: { completed: true, reason: "event_zero" } } },
      { id: "event-zero-run-finished", seq: 10, type: "run_finished", payload: { completed: true, eventCount: 10 } },
    ]

    for (const event of events) controller.enqueueEvent(event)
    for (const event of events) controller.enqueueEvent(event)

    const state = controller.getState()
    const conversationText = state.conversation.map((entry: any) => String(entry.text ?? "")).join("\n")
    const toolText = state.toolEvents.map((entry: any) => String(entry.text ?? "")).join("\n")
    const liveText = state.liveSlots.map((entry: any) => String(entry.text ?? "")).join("\n")

    expect(state.conversation.filter((entry: any) => entry.speaker === "user")).toHaveLength(1)
    expect(countOccurrences(conversationText, "EVENT_ZERO_ASSISTANT")).toBe(1)
    expect(countOccurrences(toolText + "\n" + liveText, "EVENT_ZERO_STDOUT")).toBe(1)
    expect(countOccurrences(toolText, "EVENT_ZERO_RESULT")).toBe(1)
    expect(state.completionSeen).toBe(true)
    expect(state.pendingResponse).toBe(false)
  })

  it("drops stream events from stale stream generations before they reach transcript state", () => {
    const controller = newReplayController() as any
    controller.streamGeneration = 2

    const staleAccepted = enqueueStreamEventForGeneration.call(
      controller,
      {
        id: "stale-generation-user",
        seq: 1,
        session_id: "session-stale",
        turn: 1,
        timestamp: 1,
        type: "user_message",
        payload: { text: "STALE_GENERATION_PROMPT" },
      },
      1,
    )

    expect(staleAccepted).toBe(false)
    expect(controller.getState().conversation).toHaveLength(0)
    expect(controller.getState().rawEvents).toHaveLength(0)

    const currentAccepted = enqueueStreamEventForGeneration.call(
      controller,
      {
        id: "current-generation-user",
        seq: 2,
        session_id: "session-current",
        turn: 1,
        timestamp: 2,
        type: "user_message",
        payload: { text: "CURRENT_GENERATION_PROMPT" },
      },
      2,
    )

    expect(currentAccepted).toBe(true)
    const state = controller.getState()
    expect(state.conversation.filter((entry: any) => entry.speaker === "user")).toHaveLength(1)
    expect(state.conversation[0]?.text).toBe("CURRENT_GENERATION_PROMPT")
  })
})
