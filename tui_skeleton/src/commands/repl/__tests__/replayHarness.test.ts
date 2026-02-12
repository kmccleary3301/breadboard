import { describe, it, expect } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import { ReplSessionController } from "../controller.js"
import { renderStateToText } from "../renderText.js"

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
    configPath: path.resolve("agent_configs/codex_cli_gpt51mini_e4_live.yaml"),
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
    ;(controller as any).liveSlotTimers?.forEach?.((timer: NodeJS.Timeout) => clearTimeout(timer))
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
      configPath: path.resolve("agent_configs/codex_cli_gpt51mini_e4_live.yaml"),
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

  it("orders assistant segments around tool events by createdAt", () => {
    const controller = new ReplSessionController({
      configPath: path.resolve("agent_configs/codex_cli_gpt51mini_e4_live.yaml"),
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
      configPath: path.resolve("agent_configs/codex_cli_gpt51mini_e4_live.yaml"),
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
  })
})
