import { describe, expect, it } from "vitest"
import { readFileSync } from "node:fs"
import path from "node:path"
import { ReplSessionController } from "../controller.js"
import { validateRuntimeScenario } from "../controllerRuntimeScenarioValidator.js"
import { evaluateTranscriptNoiseGate } from "../transcriptNoiseGate.js"
import { evaluateJitterGate } from "../../../repl/markdown/jitterGate.js"

type RawEvent = Record<string, unknown>

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

const createController = () =>
  new ReplSessionController({
    configPath: "agent_configs/test_simple_native.yaml",
    workspace: ".",
  }) as unknown as {
    applyEvent: (evt: any) => void
    getState: () => any
  }

const applyFixture = (fixtureName: string): any => {
  const controller = createController()
  const fixturePath = path.resolve("src/commands/repl/__tests__/fixtures", `${fixtureName}.jsonl`)
  const lines = readFileSync(fixturePath, "utf8")
    .split(/\r?\n/)
    .filter((line) => line.trim().length > 0)
  for (const line of lines) {
    const event = parseEvent(line)
    if (!event) continue
    controller.applyEvent(event)
  }
  return controller.getState()
}

describe("runtime transition gate", () => {
  it("keeps strict runtime warnings empty for canonical replay fixtures", () => {
    const fixtureNames = [
      "tool_call_result",
      "assistant_tool_interleave",
      "system_notice_tool_error",
      "multi_turn_interleave",
      "runtime_plan_path",
    ]
    for (const fixtureName of fixtureNames) {
      const state = applyFixture(fixtureName)
      const strictResult = validateRuntimeScenario(state, "strict")
      expect(strictResult.ok, fixtureName).toBe(true)
      expect(state.runtimeTelemetry?.illegalTransitions ?? 0, fixtureName).toBe(0)
    }
  })

  it("keeps completion terminal after reconnect and compaction branches", () => {
    const controller = createController()
    controller.applyEvent({ id: "1", seq: 1, turn: 1, type: "stream.gap", payload: {} })
    controller.applyEvent({ id: "2", seq: 2, turn: 1, type: "run.start", payload: {} })
    controller.applyEvent({ id: "3", seq: 3, turn: 1, type: "conversation.compaction.start", payload: {} })
    controller.applyEvent({ id: "4", seq: 4, turn: 1, type: "conversation.compaction.end", payload: {} })
    controller.applyEvent({ id: "5", seq: 5, turn: 1, type: "completion", payload: { completed: true } })

    const state = controller.getState()
    const strictResult = validateRuntimeScenario(state, "strict")
    expect(strictResult.ok).toBe(true)
    expect(state.activity?.primary).toBe("completed")
    expect(state.runtimeTelemetry?.illegalTransitions ?? 0).toBe(0)
  })

  it("passes strict + transcript-noise + jitter gates for reconnect-injected flow", () => {
    const controller = createController()
    controller.applyEvent({ id: "r1", seq: 1, turn: 1, type: "stream.gap", payload: {} })
    controller.applyEvent({ id: "r2", seq: 2, turn: 1, type: "run.start", payload: {} })
    controller.applyEvent({ id: "r3", seq: 3, turn: 1, type: "assistant.message.start", payload: {} })
    controller.applyEvent({ id: "r4", seq: 4, turn: 1, type: "assistant.message.delta", payload: { delta: "hello" } })
    controller.applyEvent({ id: "r5", seq: 5, turn: 1, type: "assistant.message.delta", payload: { delta: " world" } })
    controller.applyEvent({ id: "r6", seq: 6, turn: 1, type: "completion", payload: { completed: true } })

    const state = controller.getState()
    const strict = validateRuntimeScenario(state, "strict")
    const noise = evaluateTranscriptNoiseGate(state, 100)
    const jitter = evaluateJitterGate(
      ["hello", "hello W"],
      ["hello", "hello world"],
      { maxPrefixChurnDelta: 0, maxReflowDelta: 0 },
    )
    expect(strict.ok).toBe(true)
    expect(noise.ok).toBe(true)
    expect(jitter.ok).toBe(true)
  })

  it("passes strict + transcript-noise + jitter gates for compaction lifecycle flow", () => {
    const controller = createController()
    controller.applyEvent({ id: "c1", seq: 1, turn: 1, type: "run.start", payload: {} })
    controller.applyEvent({ id: "c2", seq: 2, turn: 1, type: "conversation.compaction.start", payload: {} })
    controller.applyEvent({ id: "c3", seq: 3, turn: 1, type: "conversation.compaction.end", payload: {} })
    controller.applyEvent({ id: "c4", seq: 4, turn: 1, type: "assistant.message.start", payload: {} })
    controller.applyEvent({ id: "c5", seq: 5, turn: 1, type: "assistant.message.delta", payload: { delta: "done" } })
    controller.applyEvent({ id: "c6", seq: 6, turn: 1, type: "completion", payload: { completed: true } })

    const state = controller.getState()
    const strict = validateRuntimeScenario(state, "strict")
    const noise = evaluateTranscriptNoiseGate(state, 100)
    const jitter = evaluateJitterGate(
      ["| c1 | c2 |\n| --- | --- |\n| 1 | 2 |\n", "| c1 | c2 |\n| --- | --- |\n| 1 | 9 |\n| 3 | 4 |\n"],
      ["| c1 | c2 |\n| --- | --- |\n| 1 | 2 |\n", "| c1 | c2 |\n| --- | --- |\n| 1 | 2 |\n| 3 | 4 |\n"],
      { maxPrefixChurnDelta: 0, maxReflowDelta: 0 },
    )
    expect(strict.ok).toBe(true)
    expect(noise.ok).toBe(true)
    expect(jitter.ok).toBe(true)
    expect(state.hints.some((hint: string) => hint.includes("Compaction started"))).toBe(true)
    expect(state.hints.some((hint: string) => hint.includes("Compaction complete"))).toBe(true)
  })
})
